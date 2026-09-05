# Design do `remediation-controller` (Fase 3)

Abre a Fase 3 do roadmap. Antes de escrever código, este documento fecha as
perguntas de design que os quatro Game Days da Fase 2 deixaram em aberto —
ver a seção "Encerrando a Fase 2" em
[`game-days/2026-09-04-stress-cpu-estoque.md`](game-days/2026-09-04-stress-cpu-estoque.md).
Objetivo explícito: **não construir "alerta disparou → reinicia o
Deployment"**. Esse caminho feliz não sobrevive a nenhum dos quatro achados
abaixo.

## Requisitos de design (rastreáveis aos Game Days)

| # | Achado | Game Day | Consequência pro design |
|---|---|---|---|
| 1 | MTTR de recuperação de pod varia (~12s–70s) | 001/002 | Nunca assumir um tempo fixo de recuperação — medir, sempre, e comparar contra a janela real. |
| 2 | Burn rate multi-janela não pega falha total que se autorrecupera em segundos | 002 | Sinal de burn rate sozinho não basta; precisa de um caminho de detecção mais rápido que a janela curta de 5m. |
| 3 | SLI atual (contador dentro do processo) é cego a falha que mata o processo rápido | 003 | Precisa de sonda blackbox/sintética, externa ao processo. |
| 4 | HPA dilui pod individual doente na média do Deployment | 004 | Diagnóstico precisa ser **por pod**, não só reagir ao alerta agregado. |

## Escopo do MVP

**Dentro**: webhook do Alertmanager → diagnóstico por pod via Prometheus →
ação corretiva direcionada (reciclar pod específico ou, na falta de um alvo
claro, reiniciar o Deployment) → confirmação de recuperação → log estruturado
do evento.

**Fora do MVP** (fica pra depois, listado em "Próximos passos"): sonda
blackbox real (documentada aqui como requisito, mas o `blackbox-exporter`
não vai na primeira versão), persistência do histórico de eventos além de
stdout, qualquer ação que não seja "reciclar pod" ou "reiniciar Deployment"
(ex.: cordon de node, rollback de imagem), e o `postmortem-generator`
(Fase 4) que vai consumir esse log.

## Por que reagir ao alerta não basta: o passo de diagnóstico

O Game Day 004 mostrou que a métrica que dispara o alerta (média agregada de
CPU do Deployment, ou disponibilidade agregada do serviço) não diz **qual**
pod está com problema — só que o SLO está sendo violado. Um controller que
reage só ao alerta (`alertname=EstoqueServiceBurnRateAlto` → `kubectl
rollout restart deploy/estoque-service`) reinicia réplicas saudáveis junto
com a doente, e nada impede o mesmo pod problemático de voltar a aparecer
sadio até o próximo agregado ruim.

Por isso o webhook não age direto: ele primeiro **diagnostica por pod**,
consultando o Prometheus diretamente (não só o payload do alerta) para achar
o(s) pod(s) específico(s) fora do normal, dentro do namespace/serviço que o
alerta aponta (`job` do label do alerta → seletor `pod=~"<job>-.*"`):

```promql
# Restart/crashloop por pod (kube-state-metrics)
increase(kube_pod_container_status_restarts_total{namespace="sre-platform", pod=~"$job-.*"}[10m]) > 0

# Pod preso perto do limite de CPU (achado do Game Day 004 — cAdvisor via kubelet)
rate(container_cpu_usage_seconds_total{namespace="sre-platform", pod=~"$job-.*", container="$job"}[2m])
  / on(pod) kube_pod_container_resource_limits{namespace="sre-platform", pod=~"$job-.*", resource="cpu"} > 0.9

# Pod not-ready sustentado (não é só uma leitura isolada de scrape)
max_over_time(kube_pod_status_ready{namespace="sre-platform", pod=~"$job-.*", condition="false"}[2m]) == 1
```

A união dessas três consultas produz uma lista de pods candidatos — pode ser
zero, um, ou vários.

## Fluxo de decisão

```
Alertmanager --POST /webhook/alert--> remediation-controller
                                            │
                                   filtra severidade
                                   (só "critical"/"high" —
                                    "low"/ticket não aciona ação)
                                            │
                                   diagnóstico por pod
                                   (3 queries acima, no job
                                    apontado pelo alerta)
                                            │
                        ┌───────────────────┴───────────────────┐
                        │                                        │
              1+ pod(s) doente(s)                      nenhum pod específico
              identificado(s)                          identificado, mas o
                        │                               alerta disparou mesmo
                        │                               assim (ex.: outage
                        │                               total ou falha que o
                        │                               SLI em-processo não
                        │                               capturou — Game Day 003)
                        │                                        │
              checa cooldown por pod                   checa cooldown por
              (já mexi nesse pod nos                   Deployment
              últimos 10 min?)                                  │
                        │                                        │
              ┌─────────┴─────────┐                    kubectl rollout
              │                   │                    restart (fallback,
           dentro do          fora do                  mais bruto — sem
           cooldown           cooldown                 alvo melhor)
              │                   │                              │
         só loga, não          delete pod                        │
         age de novo           específico                        │
         (evita flapping)      (Deployment                       │
                                recria)                           │
                        └───────────────────┬───────────────────┘
                                             │
                                confirma recuperação
                                (poll do SLI/status do pod
                                 por até 120s, mede o MTTR
                                 real observado)
                                             │
                                evento estruturado (JSON) →
                                stdout, formato pronto pro
                                postmortem-generator (Fase 4)
```

**Por que "deletar o pod específico" e não `rollout restart` como ação
primária**: deletar um pod gerenciado por um Deployment/ReplicaSet é
suficiente pra forçar recriação — mais cirúrgico que reiniciar todas as
réplicas, e é exatamente a lacuna que o Game Day 004 documentou (HPA nunca
ia reciclar sozinho os 2 pods presos no limite). `rollout restart` fica como
fallback só quando o diagnóstico não consegue apontar um pod específico.

## Cooldown / anti-flapping

Estado em memória (dicionário `{(namespace, kind, name): timestamp_ultima_acao}`,
sem persistência no MVP — reinicia zerado se o controller cair, aceitável
porque o pior caso é agir de novo antes da hora, não deixar de agir). Janela
de cooldown default: **10 minutos** por alvo (pod ou Deployment). Se o mesmo
alvo pedir ação de novo dentro da janela, o controller só loga
`cooldown_ativo: true` e não age — sinal de que o problema não é transiente
(precisa de investigação humana, não de mais um restart).

## Catálogo de ações (MVP)

| Ação | Quando | Implementação |
|---|---|---|
| `delete_pod` | Diagnóstico aponta pod(s) específico(s), fora do cooldown | K8s API: `DELETE /api/v1/namespaces/sre-platform/pods/{nome}` — o ReplicaSet recria |
| `rollout_restart` | Alerta disparou sem pod específico identificável, fora do cooldown | K8s API: patch de annotation `kubectl.kubernetes.io/restartedAt` no `spec.template.metadata` do Deployment |
| `no_action_cooldown` | Alvo já mexido há menos de 10 min | Só log, sem chamada à API |

RBAC do controller (`ServiceAccount` dedicado, escopado ao namespace
`sre-platform` — nunca cluster-wide): `get/list/delete` em `pods`,
`get/patch` em `deployments`. Sem acesso a nenhum outro namespace ou recurso.

## Confirmando a remediação (fecha o requisito #1)

Depois de agir, o controller faz polling (intervalo curto, ex. 5s, até um
teto de 120s — folga sobre o pior MTTR observado até agora, ~70s no Game Day
002) checando se o SLI voltou ao normal (mesma query de disponibilidade do
`slo-definitions.md`, mas numa janela curta tipo `[1m]`) ou, na falta disso,
se o pod recriado está `Ready`. Isso mede o **MTTR real de cada incidente**
em vez de assumir um número — é a métrica central que o README do projeto
promete (`MTTD`/`MTTR` manual vs. automatizado).

## Evento estruturado (contrato com a Fase 4)

Cada ciclo completo emite um JSON (stdout por enquanto) com este formato —
já pensado pra ser o input direto do `postmortem-generator`:

```json
{
  "timestamp_alerta": "2026-09-10T14:32:01Z",
  "alertname": "EstoqueServiceBurnRateAlto",
  "severidade": "critical",
  "job_afetado": "estoque-service",
  "diagnostico": {
    "pods_candidatos": ["estoque-service-7d9f-abc12"],
    "sinal": "cpu_throttled"
  },
  "acao": "delete_pod",
  "alvo": "estoque-service-7d9f-abc12",
  "cooldown_ativo": false,
  "recuperacao_confirmada": true,
  "mttr_observado_segundos": 34,
  "timestamp_resolucao": "2026-09-10T14:32:35Z"
}
```

## O que falta pra isso rodar de verdade (pré-requisitos de infra)

Nenhum desses é o controller em si, mas o controller não tem o que consumir
sem eles — próximos passos antes ou junto da implementação:

1. **Reativar o Alertmanager** (`alertmanager.enabled: true` em
   `k8s/monitoring/kube-prometheus-stack.values.yaml`) e configurar a rota
   pro webhook do controller (`AlertmanagerConfig` ou `alertmanager.yml`
   apontando `critical`/`high` pro receiver HTTP).
2. **`PrometheusRule` executável** pras três linhas de burn rate da tabela
   em [`slo-definitions.md`](slo-definitions.md#alertas-de-burn-rate-multi-janelamulti-burn-rate) —
   hoje só documentadas em prosa/PromQL solto.
3. **Sonda blackbox** (requisito #3, Game Day 003): instalar
   `prometheus-blackbox-exporter` com uma `Probe` HTTP contra
   `pedidos-service:8000/pedidos` (ou `/health`), alertando em
   `probe_success == 0` sustentado — sinal independente do contador
   em-processo, que é o que falha quando o processo morre rápido.
4. Manifests do controller em `k8s/remediation-controller/` (Deployment,
   Service, `ServiceAccount` + `Role` + `RoleBinding` do RBAC acima).

## Próximo passo

Com este design validado, implementar o `remediation-controller` (FastAPI,
mesmo padrão dos outros serviços) em `remediation-controller/main.py`,
seguindo o fluxo de decisão acima, e então rodar um Game Day novo (repetindo
o cenário do Game Day 004 — stress de CPU) ponta a ponta, agora com o
Alertmanager ligado, pra medir o primeiro MTTR **automatizado** de verdade.
