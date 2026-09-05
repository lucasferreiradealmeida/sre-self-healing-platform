# remediation-controller — Fase 3

Implementação do design em
[`../docs/remediation-controller-design.md`](../docs/remediation-controller-design.md).
Webhook do Alertmanager → diagnóstico por pod no Prometheus → ação
corretiva direcionada → confirmação de recuperação (MTTR real) → evento
estruturado logado em stdout.

## Endpoints

- `POST /webhook/alert` — receiver no formato de webhook do Alertmanager.
- `GET /health`, `GET /metrics` — mesmo padrão dos outros serviços.

## Variáveis de ambiente

| Variável | Default | Uso |
|---|---|---|
| `NAMESPACE` | `sre-platform` | Namespace onde diagnostica e age |
| `PROMETHEUS_URL` | `http://monitoring-kube-prometheus-prometheus.monitoring.svc.cluster.local:9090` | Base URL da API HTTP do Prometheus |
| `COOLDOWN_SECONDS` | `600` | Janela anti-flapping por alvo (pod ou Deployment) |
| `CONFIRM_TIMEOUT_SECONDS` | `120` | Teto de polling pra confirmar recuperação |
| `CONFIRM_POLL_INTERVAL_SECONDS` | `5` | Intervalo entre tentativas de confirmação |

`SERVICOS_PERMITIDOS` e `SEVERIDADES_ACIONAVEIS` (allowlist de jobs e
severidades que acionam ação) estão hardcoded no topo de `main.py` — ver
"Catálogo de ações" no design pra por quê.

## Rodando localmente (fora do cluster)

Precisa de um `kubeconfig` válido apontando pro cluster local (Multipass) —
o controller cai pra `load_kube_config()` quando não encontra config
in-cluster:

```bash
cd remediation-controller
pip install -r requirements.txt
export PROMETHEUS_URL=http://localhost:9090  # com port-forward do Prometheus ativo
uvicorn main:app --reload
```

## Deploy no cluster

Manifests em [`../k8s/remediation-controller/`](../k8s/remediation-controller/)
(Deployment, Service, `ServiceAccount`/`Role`/`RoleBinding` escopados ao
namespace `sre-platform`). Build/push da imagem segue o mesmo fluxo dos
outros serviços — ver [`../k8s/README.md`](../k8s/README.md).

## Ainda não é o fluxo ponta a ponta

Este serviço já fica de pé e responde no webhook, mas **nada aponta pra ele
ainda**: faltam os pré-requisitos de infra listados no design (reativar o
Alertmanager, `PrometheusRule` executável para os burn rates, sonda
blackbox). Sem isso, `/webhook/alert` só pode ser testado manualmente com
`curl` simulando o payload do Alertmanager.
