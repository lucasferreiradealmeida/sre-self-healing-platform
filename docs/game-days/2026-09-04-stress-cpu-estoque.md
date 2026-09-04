# Game Day 004 — Stress de CPU no `estoque-service` (o HPA reage sob caos?)

**Data**: 2026-09-04 · **Ferramenta**: Chaos Mesh 2.8.4 (`StressChaos`) ·
**Ambiente**: cluster local Multipass (`docs/local-cluster-setup.md`) ·
**SLOs de referência**: [`../slo-definitions.md`](../slo-definitions.md) ·
**Continuação de**: [Game Day 003](2026-09-04-network-delay-estoque.md)

## Objetivo

Os três primeiros Game Days testaram falha de disponibilidade (pod morto) e
de rede (latência). Este é diferente de propósito: não é ver se o serviço
aguenta, é ver se o **HPA** (`k8s/estoque-service/hpa.yaml`, min=2 max=8,
alvo 70% de CPU sobre o request) reage de verdade sob caos, e em quanto
tempo — sem depender de `iptables` (`StressChaos` roda `stress-ng` dentro do
cgroup do container, não devia ter o problema do Game Day 003).

## Hipótese

> Saturar a CPU dos 2 pods do `estoque-service` deve empurrar a utilização
> bem acima dos 70% e disparar o HPA a escalar. `pedidos-service` deve
> sentir pouco ou nada, porque o `Service` distribui as chamadas entre as
> réplicas — incluindo as novas, saudáveis, que o HPA for criando.

## Setup

```yaml
# chaos-experiments/stress-cpu-estoque.yaml
spec:
  mode: all
  selector: {labelSelectors: {app: estoque-service}}
  stressors:
    cpu: {workers: 2, load: 100}
  duration: "180s"
```

`workers: 2, load: 100` força os 2 pods (então existentes) a baterem no
`limit` de CPU do container (300m) — bem acima do `request` (100m) que o
HPA usa como base do alvo de 70%.

## Linha do tempo

| Hora (UTC) | Evento |
|---|---|
| 22:50:54 | Baseline: disponibilidade 100%, P95 ~38ms, HPA em 4%/70%, 2 réplicas |
| 22:51:01 | `StressChaos` aplicado nos 2 pods existentes |
| 22:51:32 | HPA lê 72%/70% — primeira leitura acima do alvo |
| 22:51:36 | **`SuccessfulRescale`: 2 → 4 réplicas** (35s depois do apply) |
| 22:51:51 | **`SuccessfulRescale`: 4 → 8 réplicas** (máximo do HPA, só 15s depois da primeira) |
| 22:51:57–22:52:09 | HPA lê 300-301%/70% — os 2 pods estressados continuam no teto |
| 22:52:22→22:53:47 | Utilização cai de 152% pra 76%, **estabiliza em ~76-77%** com 8 réplicas |
| 22:54:01 | `StressChaos` termina (duração de 180s) |
| 22:54:37 | CPU volta a 2%/70% — os 2 pods que estavam presos voltam ao normal quase na hora |
| — | 8 réplicas permanecem de pé (esperado: `HPA` tem `stabilizationWindow` de 5 min pra scale-down por padrão) |

## Resultado observado

**HPA reagiu rápido e corretamente**: de 2 pods estressados até 8 réplicas
(o máximo configurado) em **50 segundos**. Sem intervenção humana — exatamente
o tipo de recuperação automática que o roadmap do projeto quer medir.

**Impacto em `pedidos-service`: mínimo.** Disponibilidade ficou em **100%**
a janela inteira. P95 subiu de ~32-38ms (baseline) pra um pico de **~99ms**
por volta do momento mais crítico (22:51:50, quando só 2 réplicas existiam e
ambas estavam saturadas) — nada perto do SLO de 300ms, e caiu de volta pra
perto do baseline assim que as réplicas novas entraram. **Sem nenhum
reinício de pod** — diferente do Game Day 003, o stress de CPU não atrasou
a resposta da liveness probe o suficiente pra falhar (o request handler do
`/health` é trivial o bastante pra ainda responder dentro de `timeout: 1s`
mesmo com o processo competindo por CPU).

## O achado principal: o HPA dilui o problema, não resolve ele

Depois do scale-up pra 8 réplicas, a utilização média de CPU caiu de 300%
pra ~77% — abaixo do alvo de 70%+margem, aparentemente "resolvido". **Mas
os 2 pods originais continuaram presos no limite de CPU o experimento
inteiro** (confirmado com `kubectl top pods`: as 6 réplicas novas ficavam
em 2-3m de CPU, saudáveis; presumo — pela matemática da média — que os 2
originais se mantiveram perto do `limit` de 300m até o `StressChaos`
acabar). A média caiu porque **6 réplicas saudáveis novas entraram na
conta**, não porque os 2 pods doentes melhoraram.

Isso é um ponto real de design pra Fase 3: **autoscaling não é
auto-remediação de pod doente.** Um HPA baseado em CPU agregada:

- não identifica *quais* réplicas especificamente estão com problema;
- não as substitui — só adiciona capacidade ao redor delas;
- "esconde" o sintoma (a média volta pro alvo) sem tocar na causa.

Num cenário real onde a causa fosse um bug (não um experimento de caos que
termina sozinho em 180s), os 2 pods doentes ficariam consumindo CPU no teto
**indefinidamente**, e o HPA nunca reagiria de novo a esse sintoma
específico porque a média agregada já está "ok". Um `remediation-controller`
de verdade (Fase 3) precisa de sinal **por pod** (ex.: CPU individual
sustentado no limite, ou `container_cpu_cfs_throttled_periods_total` do
cAdvisor, já raspado pelo `kube-prometheus-stack`) pra identificar e
reciclar a réplica específica — não só observar a média do Deployment.

## Nota operacional

O scale-up pra 8 réplicas empurrou o `worker` pra **95% de CPU requests**
alocados (`kubectl describe nodes`) — perto do limite, mas sem gerar
`Pending`. Deixei o `HPA` fazer o scale-down sozinho (janela padrão de 5 min
de estabilização) em vez de forçar com `kubectl scale`, pra observar o
comportamento real; não fez parte da janela de observação deste Game Day.

## Conclusão

- **Hipótese confirmada**: HPA reagiu em 50s, disponibilidade não foi
  afetada, latência subiu pouco e voltou rápido. Comparado aos Game Days
  001-003, foi o experimento com **menor blast radius percebido pelo
  usuário final** — justamente porque testou o mecanismo de auto-scaling
  que existe pra absorver esse tipo de problema.
- **Achado de design pra Fase 3**: a métrica agregada do HPA pode mascarar
  réplicas individualmente doentes. Auto-remediação de verdade precisa de
  granularidade por pod, não só por Deployment.
- **Sem cascata de reinício** (diferente do Game Day 003) — stress de CPU
  isolado, sem tocar rede, não interfere com liveness probe neste caso.

## Encerrando a Fase 2

Com os quatro experimentos rodados — `pod-kill` pequeno (001), `pod-kill`
total (002), latência de rede (003) e stress de CPU (004) — os candidatos
de Chaos Engineering do roadmap inicial estão cobertos. Achados acumulados
que viram requisito de design pra Fase 3 (auto-remediação):

1. MTTR de recuperação de pod é variável (~12s a ~70s) — não dá pra assumir
   um valor fixo (Game Days 001/002).
2. Alertas de burn rate multi-janela não pegam falhas totais que se
   autorrecuperam em segundos (Game Day 002).
3. O SLI atual (contador dentro do processo) é cego a qualquer falha que
   mate o processo rápido — precisa de sinal de blackbox/sintético
   (Game Day 003).
4. HPA dilui problemas de pod individual em vez de resolvê-los — precisa de
   sinal por pod, não só agregado por Deployment (Game Day 004).

Próximo passo do roadmap: desenhar o `remediation-controller` (Fase 3) já
incorporando esses quatro requisitos, não só o caminho feliz de "alerta
disparou → reinicia o Deployment".
