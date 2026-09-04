# Definição de SLIs, SLOs e Error Budget

Fecha a Fase 1 do roadmap: com os três serviços em produção no cluster e o
Prometheus raspando métricas reais (via `k8s/monitoring/servicemonitors.yaml`),
este documento formaliza o que é "confiável" para cada um — base para os
alertas de burn rate da Fase 3 (auto-remediação) e para medir o impacto dos
experimentos de caos da Fase 2.

Metodologia: SLI como razão *eventos bons / eventos válidos*, SLO como alvo
sobre uma janela rolante, error budget como o inverso do SLO. Alertas de burn
rate seguem a abordagem multi-janela/multi-burn-rate do
[Google SRE Workbook, cap. 5](https://sre.google/workbook/alerting-on-slos/).

## Classificação de status HTTP: o que conta como falha

O `loadgen` (`k8s/monitoring/loadgen.yaml`) manda de propósito uma mistura de
pedidos válidos, `409` (estoque insuficiente) e `404` (produto inexistente) —
são **resultados de negócio esperados**, não falhas do serviço. Se entrassem
como "erro" no SLI, o error budget estouraria por design, sem nenhuma falha
real acontecer.

| Status | Classificação | Motivo |
|---|---|---|
| `2xx` | bom | sucesso |
| `404` | bom | produto não existe — resposta correta, não uma falha do serviço |
| `409` | bom | estoque insuficiente — resposta correta, não uma falha do serviço |
| `5xx` (inclui o `503` que `pedidos-service` devolve quando `estoque-service` está fora do ar) | **ruim** | falha real: bug, timeout, dependência indisponível |

Ou seja, o SLI de disponibilidade conta `status!~"5.."` como bom. Isso também
é o motivo pelo qual o cenário de caos mais natural para testar esse SLO é
derrubar o `estoque-service` (gera `503` real em `pedidos-service`), não
mandar produto inexistente.

## Cardinalidade do label `route`

`estoque-service` (`/estoque/verificar/{produto_id}`) e o `GET /pedidos/{id}`
de `pedidos-service` não têm o path templado no código — cada `produto_id`/
`pedido_id` vira uma série própria (`route="/estoque/verificar/produto-a"`,
etc.). As queries abaixo agregam com `route=~"/estoque/verificar/.*"` para não
depender de um produto específico. Não é bug bloqueante, mas fica registrado
como débito técnico para templar o path na app (`request.scope["route"].path`
no FastAPI) antes de crescer o catálogo de produtos.

## SLIs e SLOs por serviço

Janela do SLO: **30 dias rolantes**. Retenção do Prometheus hoje é 12h
(`k8s/monitoring/kube-prometheus-stack.values.yaml`, emptyDir sem
StorageClass) — ver [Limitações](#limitações-do-ambiente-atual).

### `pedidos-service` — caminho crítico, exposto ao usuário

| SLI | SLO | Query |
|---|---|---|
| Disponibilidade | 99.5% | `sum(rate(http_requests_total{job="pedidos-service", route="/pedidos", status!~"5.."}[5m])) / sum(rate(http_requests_total{job="pedidos-service", route="/pedidos"}[5m]))` |
| Latência P95 | < 300ms | `histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{job="pedidos-service", route="/pedidos"}[5m])) by (le))` |
| Latência P99 | < 800ms | `histogram_quantile(0.99, sum(rate(http_request_duration_seconds_bucket{job="pedidos-service", route="/pedidos"}[5m])) by (le))` |

### `estoque-service` — dependência interna, chamado por `pedidos-service`

| SLI | SLO | Query |
|---|---|---|
| Disponibilidade | 99.5% | `sum(rate(http_requests_total{job="estoque-service", route=~"/estoque/verificar/.*", status!~"5.."}[5m])) / sum(rate(http_requests_total{job="estoque-service", route=~"/estoque/verificar/.*"}[5m]))` |
| Latência P95 | < 200ms | `histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{job="estoque-service", route=~"/estoque/verificar/.*"}[5m])) by (le))` |

SLO de disponibilidade mais apertado que o de `pedidos-service` seria incoerente
(dependência não pode ser mais frágil que quem depende dela) — por isso os dois
usam 99.5%. Isso implica: se `estoque-service` cair sozinho, `pedidos-service`
esgota o próprio budget quase junto, porque toda falha de `estoque-service`
vira `503` em `pedidos-service`.

### `notificacao-service` — assíncrono, best-effort

| SLI | SLO | Query |
|---|---|---|
| Disponibilidade | 99% | `sum(rate(http_requests_total{job="notificacao-service", route="/notificar", status!~"5.."}[5m])) / sum(rate(http_requests_total{job="notificacao-service", route="/notificar"}[5m]))` |
| Latência P95 | < 500ms | `histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket{job="notificacao-service", route="/notificar"}[5m])) by (le))` |

SLO mais frouxo por design: `estoque-service` já trata falha de notificação
como não-crítica (`except httpx.RequestError` só loga um warning, não propaga
erro pro pedido).

## Error budget

Budget de erro = `1 - SLO`. Consumo do budget numa janela:

```
consumido = (1 - disponibilidade_observada) / (1 - SLO)
```

`consumido = 1.0` → budget zerado na janela. `consumido > 1.0` → estourou.
Exemplo para `pedidos-service` (SLO 99.5%, budget = 0.5%): se a disponibilidade
observada em 30 dias for 99.0%, `consumido = (1 - 0.99) / (1 - 0.995) = 2.0` →
gastou 2x o budget mensal.

## Alertas de burn rate (multi-janela/multi-burn-rate)

Referência para a Fase 3 — quando o Alertmanager for reativado
(`alertmanager.enabled: true` nos values) e apontado pro webhook do
`remediation-controller`. Combina uma janela curta (confirma que o burn é
atual) com uma longa (confirma que não é só um pico) para reduzir alerta falso
sem perder velocidade de detecção:

| Severidade | Burn rate | Janela curta | Janela longa | % do budget de 30d consumido | Ação |
|---|---|---|---|---|---|
| Crítica (page) | 14.4x | 5m | 1h | 2% em 1h | Remediação automática imediata |
| Alta (page) | 6x | 30m | 6h | 5% em 6h | Remediação automática |
| Baixa (ticket) | 1x | 2h | 3d | 10% em 3d | Investigar, sem ação automática |

Query genérica (trocar `job`/`route`/janela pelos da tabela):

```promql
(
  1 - (
    sum(rate(http_requests_total{job="pedidos-service", route="/pedidos", status!~"5.."}[1h]))
    /
    sum(rate(http_requests_total{job="pedidos-service", route="/pedidos"}[1h]))
  )
) > (14.4 * 0.005)
and
(
  1 - (
    sum(rate(http_requests_total{job="pedidos-service", route="/pedidos", status!~"5.."}[5m]))
    /
    sum(rate(http_requests_total{job="pedidos-service", route="/pedidos"}[5m]))
  )
) > (14.4 * 0.005)
```

## Limitações do ambiente atual

- **Retenção de 12h**: as janelas de 3d/30d da tabela de burn rate e do error
  budget não têm histórico suficiente para avaliar no cluster local hoje. Elas
  ficam documentadas como o alvo formal; virarão `PrometheusRule` executável
  na Fase 3, quando também vale revisitar `local-path-provisioner` (retenção
  maior) ou migrar a validação para o caminho Terraform/AWS.
- **Latência da API do control-plane** (`k8s/monitoring/README.md`): picos de
  300ms–1s no `fsync` do disco virtual não afetam o scrape (Prometheus raspa
  os pods pela rede), mas podem introduzir ruído se algum dia se monitorar a
  própria API do control-plane.

## Próximo passo

Fase 1 encerrada. Fase 2 do roadmap: instalar o Chaos Mesh e rodar o primeiro
Game Day derrubando `estoque-service` (`chaos-experiments/`, documentado em
`docs/game-days/`) para observar estes SLOs sendo consumidos de verdade.
