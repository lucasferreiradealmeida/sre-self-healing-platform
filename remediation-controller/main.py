import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import Response
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from kubernetes.client.exceptions import ApiException
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

# Ver docs/remediation-controller-design.md para o desenho completo (fluxo de
# decisao, catalogo de acoes, requisitos rastreados aos Game Days 001-004).

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("remediation-controller")

app = FastAPI(title="Remediation Controller")

NAMESPACE = os.getenv("NAMESPACE", "sre-platform")
PROMETHEUS_URL = os.getenv(
    "PROMETHEUS_URL",
    "http://monitoring-kube-prometheus-prometheus.monitoring.svc.cluster.local:9090",
)
COOLDOWN_SECONDS = int(os.getenv("COOLDOWN_SECONDS", str(10 * 60)))
CONFIRM_TIMEOUT_SECONDS = int(os.getenv("CONFIRM_TIMEOUT_SECONDS", "120"))
CONFIRM_POLL_INTERVAL_SECONDS = int(os.getenv("CONFIRM_POLL_INTERVAL_SECONDS", "5"))

# Defesa em profundidade: mesmo com o RBAC do ServiceAccount ja restrito ao
# namespace sre-platform, so agimos sobre os Deployments que conhecemos --
# um label "job" inesperado no alerta nao vira acao no cluster.
SERVICOS_PERMITIDOS = {"pedidos-service", "estoque-service", "notificacao-service"}
SEVERIDADES_ACIONAVEIS = {"critical", "high"}

REQUEST_COUNT = Counter(
    "http_requests_total", "Total de requisicoes HTTP", ["method", "endpoint", "status"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds", "Latencia das requisicoes HTTP", ["method", "endpoint"]
)
REMEDIACOES_TOTAL = Counter(
    "remediacoes_total", "Total de remediacoes processadas", ["acao", "resultado"]
)

# Estado de cooldown em memoria -- ver "Cooldown / anti-flapping" no design.
# Zera se o pod do controller reiniciar; pior caso e' agir de novo antes da
# hora, nao deixar de agir, entao aceitavel sem persistencia no MVP.
_ultima_acao: dict[tuple[str, str], float] = {}


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start_time = time.time()
    endpoint = request.url.path
    method = request.method
    response = await call_next(request)
    status = response.status_code
    duration = time.time() - start_time
    REQUEST_LATENCY.labels(method=method, endpoint=endpoint).observe(duration)
    REQUEST_COUNT.labels(method=method, endpoint=endpoint, status=status).inc()
    return response


@app.on_event("startup")
async def startup():
    try:
        k8s_config.load_incluster_config()
        logger.info("Config do cluster carregada via ServiceAccount (in-cluster).")
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()
        logger.info("Config do cluster carregada via kubeconfig local (fora do cluster).")
    app.state.core_v1 = k8s_client.CoreV1Api()
    app.state.apps_v1 = k8s_client.AppsV1Api()
    app.state.prom_client = httpx.AsyncClient(base_url=PROMETHEUS_URL, timeout=10.0)


@app.on_event("shutdown")
async def shutdown():
    await app.state.prom_client.aclose()


@app.get("/health")
async def health():
    return {"status": "ok", "service": "remediation-controller"}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


async def prom_query(prom_client: httpx.AsyncClient, promql: str) -> list[dict]:
    resp = await prom_client.get("/api/v1/query", params={"query": promql})
    resp.raise_for_status()
    corpo = resp.json()
    if corpo.get("status") != "success":
        raise ValueError(f"Prometheus retornou status '{corpo.get('status')}' para: {promql}")
    return corpo["data"]["result"]


async def diagnostica_pods(prom_client: httpx.AsyncClient, job: str) -> list[dict]:
    """Acha pod(s) especifico(s) fora do normal -- responde ao achado do Game
    Day 004: a media agregada do Deployment nao diz qual pod esta doente."""
    candidatos: dict[str, str] = {}

    consultas = [
        (
            "crashloop",
            f'increase(kube_pod_container_status_restarts_total'
            f'{{namespace="{NAMESPACE}", pod=~"{job}-.*"}}[10m]) > 0',
        ),
        (
            "cpu_throttled",
            f'(rate(container_cpu_usage_seconds_total'
            f'{{namespace="{NAMESPACE}", pod=~"{job}-.*", container="{job}"}}[2m]) '
            f'/ on(pod) kube_pod_container_resource_limits'
            f'{{namespace="{NAMESPACE}", pod=~"{job}-.*", resource="cpu"}}) > 0.9',
        ),
        (
            "not_ready",
            f'max_over_time(kube_pod_status_ready'
            f'{{namespace="{NAMESPACE}", pod=~"{job}-.*", condition="false"}}[2m]) == 1',
        ),
    ]

    for sinal, promql in consultas:
        try:
            resultados = await prom_query(prom_client, promql)
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            logger.warning(f"Diagnostico '{sinal}' falhou, ignorando este sinal: {exc}")
            continue
        for r in resultados:
            pod = r.get("metric", {}).get("pod")
            if pod and pod not in candidatos:
                candidatos[pod] = sinal

    return [{"pod": pod, "sinal": sinal} for pod, sinal in candidatos.items()]


def em_cooldown(alvo_key: tuple[str, str]) -> bool:
    ultima = _ultima_acao.get(alvo_key)
    return ultima is not None and (time.time() - ultima) < COOLDOWN_SECONDS


def registra_acao(alvo_key: tuple[str, str]) -> None:
    _ultima_acao[alvo_key] = time.time()


def deleta_pod(core_v1: k8s_client.CoreV1Api, pod: str) -> None:
    core_v1.delete_namespaced_pod(name=pod, namespace=NAMESPACE)


def reinicia_deployment(apps_v1: k8s_client.AppsV1Api, deployment: str) -> None:
    patch = {
        "spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "kubectl.kubernetes.io/restartedAt": datetime.now(timezone.utc).isoformat()
                    }
                }
            }
        }
    }
    apps_v1.patch_namespaced_deployment(name=deployment, namespace=NAMESPACE, body=patch)


async def pods_prontos(core_v1: k8s_client.CoreV1Api, job: str) -> bool:
    pods = core_v1.list_namespaced_pod(namespace=NAMESPACE, label_selector=f"app={job}")
    if not pods.items:
        return False
    for pod in pods.items:
        condicoes = {c.type: c.status for c in (pod.status.conditions or [])}
        if condicoes.get("Ready") != "True":
            return False
    return True


async def confirma_recuperacao(
    prom_client: httpx.AsyncClient, core_v1: k8s_client.CoreV1Api, job: str
) -> tuple[bool, float]:
    """Mede o MTTR real em vez de assumir um valor fixo (requisito #1,
    Game Days 001/002). Sinal primario: disponibilidade do job no
    Prometheus; sem dado ainda (pod acabou de subir, sem trafego), cai pro
    status Ready do pod via API do cluster."""
    inicio = time.monotonic()
    deadline = inicio + CONFIRM_TIMEOUT_SECONDS
    promql = (
        f'sum(rate(http_requests_total{{job="{job}", status!~"5.."}}[1m])) '
        f'/ sum(rate(http_requests_total{{job="{job}"}}[1m]))'
    )

    while time.monotonic() < deadline:
        recuperado = False
        try:
            resultados = await prom_query(prom_client, promql)
            if resultados:
                disponibilidade = float(resultados[0]["value"][1])
                recuperado = disponibilidade >= 0.99
            else:
                recuperado = await pods_prontos(core_v1, job)
        except (httpx.HTTPError, KeyError, ValueError, IndexError) as exc:
            logger.warning(f"Falha ao checar recuperacao nesta tentativa: {exc}")

        if recuperado:
            return True, time.monotonic() - inicio
        await asyncio.sleep(CONFIRM_POLL_INTERVAL_SECONDS)

    return False, time.monotonic() - inicio


async def processa_alerta(alerta: dict, job: str, severidade: str) -> dict:
    prom_client: httpx.AsyncClient = app.state.prom_client
    core_v1: k8s_client.CoreV1Api = app.state.core_v1
    apps_v1: k8s_client.AppsV1Api = app.state.apps_v1

    evento = {
        "timestamp_alerta": alerta.get("startsAt"),
        "alertname": alerta.get("labels", {}).get("alertname"),
        "severidade": severidade,
        "job_afetado": job,
    }

    pods_candidatos = await diagnostica_pods(prom_client, job)
    evento["diagnostico"] = {
        "pods_candidatos": [c["pod"] for c in pods_candidatos],
        "sinal": pods_candidatos[0]["sinal"] if pods_candidatos else None,
    }

    if pods_candidatos:
        alvo_pod = pods_candidatos[0]["pod"]
        alvo_key = (NAMESPACE, alvo_pod)
        acao = "delete_pod"
        alvo = alvo_pod
    else:
        alvo_key = (NAMESPACE, job)
        acao = "rollout_restart"
        alvo = job

    if em_cooldown(alvo_key):
        logger.info(f"Alvo {alvo_key} em cooldown, nao agindo de novo agora.")
        evento.update({"acao": "no_action_cooldown", "alvo": alvo, "cooldown_ativo": True})
        REMEDIACOES_TOTAL.labels(acao="no_action_cooldown", resultado="ignorado").inc()
        return evento

    try:
        if acao == "delete_pod":
            deleta_pod(core_v1, alvo)
        else:
            reinicia_deployment(apps_v1, alvo)
        registra_acao(alvo_key)
    except ApiException as exc:
        logger.error(f"Falha ao executar '{acao}' em '{alvo}': {exc}")
        evento.update({"acao": acao, "alvo": alvo, "cooldown_ativo": False, "erro": str(exc)})
        REMEDIACOES_TOTAL.labels(acao=acao, resultado="erro").inc()
        return evento

    recuperado, segundos = await confirma_recuperacao(prom_client, core_v1, job)
    evento.update(
        {
            "acao": acao,
            "alvo": alvo,
            "cooldown_ativo": False,
            "recuperacao_confirmada": recuperado,
            "mttr_observado_segundos": round(segundos, 1),
            "timestamp_resolucao": datetime.now(timezone.utc).isoformat(),
        }
    )
    REMEDIACOES_TOTAL.labels(acao=acao, resultado="confirmado" if recuperado else "sem_confirmacao").inc()
    return evento


@app.post("/webhook/alert")
async def webhook_alert(payload: dict):
    """Receiver do Alertmanager. So severidades 'critical'/'high' (mapeadas
    das linhas de burn rate de docs/slo-definitions.md) acionam remediacao;
    'low'/ticket fica de fora por design -- ver docs/remediation-controller-design.md."""
    eventos = []
    for alerta in payload.get("alerts", []):
        if alerta.get("status") != "firing":
            continue

        labels = alerta.get("labels", {})
        severidade = labels.get("severity", "").lower()
        if severidade not in SEVERIDADES_ACIONAVEIS:
            logger.info(f"Severidade '{severidade}' nao aciona remediacao automatica, ignorando.")
            continue

        job = labels.get("job")
        if job not in SERVICOS_PERMITIDOS:
            logger.warning(f"job '{job}' fora da allowlist, ignorando por seguranca.")
            continue

        evento = await processa_alerta(alerta, job, severidade)
        logger.info(f"EVENTO: {json.dumps(evento, ensure_ascii=False)}")
        eventos.append(evento)

    return {"processados": len(eventos), "eventos": eventos}
