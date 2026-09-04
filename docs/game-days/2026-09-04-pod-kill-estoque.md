# Game Day 001 — `pod-kill` em 1 de 2 réplicas do `estoque-service`

**Data**: 2026-09-04 · **Ferramenta**: Chaos Mesh 2.8.4 · **Ambiente**: cluster
local Multipass (`docs/local-cluster-setup.md`) · **SLOs de referência**:
[`../slo-definitions.md`](../slo-definitions.md)

## Objetivo

Primeiro experimento de caos da Fase 2. Blast radius pequeno de propósito:
validar o mecanismo (Chaos Mesh + observabilidade dos SLOs), não já testar o
pior caso. `estoque-service` foi o alvo porque é a única dependência que, ao
falhar, gera `503` real em `pedidos-service` (ver "Classificação de status
HTTP" em `slo-definitions.md`) — os demais erros do fluxo (`404`, `409`) são
resultado de negócio, não sinal de indisponibilidade.

## Hipótese (declarada antes de rodar)

> Matar 1 dos 2 pods do `estoque-service` **não deve violar o SLO de
> disponibilidade** (99.5%) — o `Service` tem a outra réplica saudável pra
> absorver o tráfego. Pode gerar um **pico breve de latência** durante o
> failover de conexões (enquanto o `kube-proxy` remove o endpoint morto),
> mas o P95 deve voltar perto do baseline em menos de 1 minuto.

## Setup

```yaml
# chaos-experiments/pod-kill-estoque.yaml
apiVersion: chaos-mesh.org/v1alpha1
kind: PodChaos
metadata:
  name: pod-kill-estoque
  namespace: sre-platform
spec:
  action: pod-kill
  mode: one          # só 1 dos 2 pods — blast radius pequeno
  selector:
    namespaces: [sre-platform]
    labelSelectors:
      app: estoque-service
```

Antes de rodar, confirmei que o `estoque-service` estava com 2/2 réplicas
estáveis e que a janela não colidia com o `CronJob estoque-replenisher`
(`*/10 * * * *`, roda `kubectl rollout restart deployment/estoque-service`
pra resetar o estoque em memória — um rollout dele por perto teria
contaminado a leitura). Último disparo do CronJob: 17:10:00Z. Rodei o
experimento em 17:12:41Z, com ~7 minutos de folga antes do próximo.

## Linha do tempo

| Hora (UTC) | Evento |
|---|---|
| 17:12:01 | Baseline: disponibilidade 100%, P95 `pedidos-service` ~230ms, 2/2 pods `estoque-service` |
| 17:12:41 | `PodChaos` aplicado → mata `estoque-service-694688694c-vrgs7` |
| 17:12:41 | ReplicaSet cria o pod de reposição (`mkl84`) |
| ~17:13:05 | **Achado não previsto**: `mkl84` sobe (`Uvicorn running`, log confirma), mas a *liveness probe* recebe `connection refused` 3x seguidas → kubelet mata e reinicia o container |
| ~17:13:51 | `mkl84` passa nas probes, 2/2 `Running` de novo (~70s desde a morte do pod, não os ~15s esperados) |
| 17:13:15–17:13:30 | Pico de latência: P95 `pedidos-service` chega a **1.67s** |
| 17:16:28 | P95 em 0.44s, convergindo de volta pro baseline (~230ms) |

## Resultado observado

**Disponibilidade — SLO aguentou.** `sum(rate(..., status!~"5..")[1m]) / sum(rate(...)[1m]))`
ficou em **1.0 (100%) do início ao fim da janela** (17:11:30–17:16:30). O
contador bruto de `503` em `pedidos-service` não se moveu (ficou em 5, o
mesmo valor de antes do experimento — resíduo de outra causa, não deste
teste). Error budget mensal consumido nesta janela: **0%**. A hipótese
principal se confirmou: a réplica sobrevivente absorveu o tráfego sem gerar
erro.

**Latência — SLO violado.** O P95 de `pedidos-service` saiu de ~230ms
(baseline) para um pico de **1.67s** — mais de 5x o alvo de 300ms definido em
`slo-definitions.md` — e levou **~3-4 minutos** pra convergir de volta perto
do baseline. Isso não aparece no SLI de disponibilidade (não é erro, é
latência), mas é uma violação real do SLO de latência que passaria batida se
só se olhasse taxa de erro. Ponto pra reforçar: os dois SLIs (disponibilidade
e latência) precisam ser olhados juntos.

**`estoque-service` (o alvo direto)**: disponibilidade também ficou em 100%
durante toda a janela — o tráfego de `pedidos-service` foi todo pro pod
sobrevivente, sem fila/timeout visível no SLI dele.

## Achado inesperado: o pod de reposição flapou sozinho

O pod que o ReplicaSet criou pra repor o que foi morto **não subiu limpo**: o
container iniciou (log mostra `Application startup complete`), mas a
liveness probe (`timeout=1s`, `period=10s`, `failure=3`) recebeu `connection
refused` três vezes seguidas e o kubelet matou e recriou o container antes
dele ficar pronto. Isso dobrou o tempo de recuperação da redundância (~70s em
vez dos ~15s esperados: `initialDelaySeconds=5` + 1 período de 10s).

Suspeita de causa: a mesma limitação já documentada em
`k8s/monitoring/README.md` — o `fsync` lento do disco virtual do Multipass
gera picos de latência na VM, e aqui provavelmente atrasou o bind da porta ou
a resposta ao probe o suficiente pra estourar o timeout de 1s, mesmo com a
aplicação já "de pé" segundo o próprio log dela. Não é causado pelo Chaos
Mesh — teria acontecido em qualquer reinício de pod nesse ambiente (inclusive
nos rollouts do `estoque-replenisher`), só que aqui apareceu porque o
experimento forçou uma recriação de pod bem na hora de observar.

**Ação de acompanhamento** (não aplicada ainda, registrando pra não perder):
considerar aumentar `livenessProbe.timeoutSeconds` (1s → 2-3s) nos três
deployments antes do próximo Game Day, ou aceitar o flap como característica
conhecida do ambiente local (não seria um problema no caminho Terraform/AWS,
com disco real). Decisão fica pra quando isso voltar a aparecer — um único
evento não é sinal suficiente pra mudar o probe.

## Conclusão

- **Blast radius pequeno cumpriu o objetivo**: validou o Chaos Mesh
  ponta-a-ponta (containerd runtime, seletor por label, dashboard) sem
  derrubar disponibilidade de verdade.
- **SLO de disponibilidade**: aguentou, 0% de error budget consumido.
- **SLO de latência**: violado por ~3-4 min — o primeiro dado real de que os
  dois SLIs não andam sempre juntos neste sistema.
- **Efeito colateral observado, não causado pelo experimento**: pods novos
  neste ambiente podem flapar uma vez antes de ficar prontos, quase dobrando
  o MTTR de um `pod-kill` simples. Vira insumo pra Fase 3 (o
  `remediation-controller` não pode assumir que "pod recriado" = "pod
  saudável" imediatamente).

## Próximo experimento

Com o mecanismo validado, o próximo passo natural é aumentar o blast radius:
`mode: all` no `estoque-service` (mata as duas réplicas de uma vez, sem
sobrevivente) — aí sim deve estourar o SLO de disponibilidade de
`pedidos-service` de verdade, e serve de base pra Fase 3 (o gatilho que o
`remediation-controller` vai precisar reagir).
