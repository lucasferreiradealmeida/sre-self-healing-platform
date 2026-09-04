# Game Day 002 — `pod-kill` nas 2 réplicas do `estoque-service` (`mode: all`)

**Data**: 2026-09-04 · **Ferramenta**: Chaos Mesh 2.8.4 · **Ambiente**: cluster
local Multipass (`docs/local-cluster-setup.md`) · **SLOs de referência**:
[`../slo-definitions.md`](../slo-definitions.md) · **Continuação de**:
[Game Day 001](2026-09-04-pod-kill-estoque.md)

## Objetivo

Game Day 001 matou 1 de 2 réplicas do `estoque-service` (blast radius
pequeno) e o SLO de disponibilidade aguentou — a réplica sobrevivente
absorveu tudo. Este experimento aumenta o blast radius pra **as duas
réplicas de uma vez** (`mode: all`), sem sobrevivente, pra gerar uma
violação real de disponibilidade — o tipo de sinal que a Fase 3
(auto-remediação) vai precisar pra ter algo de verdade pra reagir.

## Hipótese (declarada antes de rodar)

> Sem nenhuma réplica saudável do `estoque-service`, toda chamada de
> `pedidos-service` a ele vai falhar (`httpx.RequestError` → `503`) até o
> ReplicaSet recriar e um pod novo ficar pronto. Isso **deve violar o SLO de
> 99.5%** de disponibilidade — diferente do Game Day 001. Espero um MTTR
> parecido ou maior que os ~70s observados no Game Day 001 (lá, o pod de
> reposição flapou uma vez antes de ficar pronto).

## Setup

```yaml
# chaos-experiments/pod-kill-estoque-all.yaml
apiVersion: chaos-mesh.org/v1alpha1
kind: PodChaos
metadata:
  name: pod-kill-estoque-all
  namespace: sre-platform
spec:
  action: pod-kill
  mode: all          # as duas réplicas, sem sobrevivente
  selector:
    namespaces: [sre-platform]
    labelSelectors:
      app: estoque-service
```

O cluster tinha acabado de passar por um restart das VMs (Multipass) pouco
antes deste Game Day — esperei o `estoque-replenisher` (CronJob que roda
`kubectl rollout restart` a cada 10 min) disparar e assentar antes de
começar, pra não confundir o resultado com o rollout dele. Confirmei
disponibilidade 100% e 2/2 pods estáveis por ~3 min antes de aplicar.

## Linha do tempo

| Hora (UTC) | Evento |
|---|---|
| 22:03:02 | Baseline: disponibilidade 100%, P95 ~38ms, 2/2 pods `estoque-service` |
| 22:03:19 | `PodChaos` aplicado → mata **as duas** réplicas simultaneamente |
| 22:03:19–~22:03:31 | `estoque-service` com **zero** pods prontos (`Service` sem endpoints saudáveis) |
| ~22:03:25 | 2 pods novos em `ContainerCreating` (ReplicaSet já reagiu) |
| ~22:03:31 | Ambos os pods novos em `1/1 Running` — **desta vez sem flap** de probe (diferente do Game Day 001) |
| 22:03:35→22:03:40 (1 scrape de 30s) | Contador de `503` em `pedidos-service` pula de 1 (residual, sem relação) para 10 — **9 falhas novas** |
| 22:04:15 | Disponibilidade de volta a 100% |

## Resultado observado

**Disponibilidade — SLO violado, como previsto.** Nas amostras de
disponibilidade em janela de 1 minuto, dois pontos consecutivos caíram pra
**84.4%** e **80.0%** (bem abaixo dos 99.5% do SLO) exatamente na janela da
falta de pods. Em contadores brutos: **9 de 28 requisições** a `/pedidos`
falharam com `503` no intervalo de scrape que cobriu o incidente (~32% de
erro pontual). Fora dessa janela de ~12s, disponibilidade ficou em 100% —
diferente do Game Day 001, aqui o SLO realmente foi consumido.

**Latência — não teve pico desta vez.** P95 ficou estável entre 24-40ms
durante todo o incidente. Isso é coerente com a causa: enquanto no Game Day
001 a réplica sobrevivente ficava *sobrecarregada e lenta*, aqui não existe
réplica nenhuma — a chamada falha rápido (`connection refused`) em vez de
enfileirar. **Falha total gera erro rápido; falha parcial gera latência.**

**MTTR menor que o Game Day 001**: o pod de reposição não flapou desta vez —
ficou pronto em ~12s após ser criado (`ContainerCreating` → `1/1 Running`),
bem mais rápido que os ~70s do Game Day 001 (lá, a liveness probe matou o
primeiro pod novo antes dele ficar pronto). Confirma que o flap anterior foi
um evento de latência da VM (não determinístico), não algo que este
experimento reproduz sempre.

## Error budget e o limite do alerta de burn rate

Usando a fórmula de `slo-definitions.md` (`consumido = (1 -
disponibilidade)/(1 - SLO)`) no pior ponto observado (janela de 1m,
disponibilidade 80.0%): `(1 - 0.80)/(1 - 0.995) = 40x` o budget mensal — só
que sustentado por apenas ~12 segundos, não o mês inteiro. É exatamente o
motivo de a tabela de burn rate ser multi-janela: um burn rate instantâneo
gigante em ~12s é irrelevante pro budget de 30 dias se o volume total de
tráfego do mês for alto — o que importa é ele se sustentar.

Testei isso: recalculando a disponibilidade com a janela de **5 minutos**
(a janela curta real do alerta "crítico" da tabela), o resultado foi
**97.87%**, ou seja, burn rate de `(1-0.9787)/0.005 ≈ 4.3x`. **Abaixo dos
14.4x que disparariam o alerta crítico, e abaixo dos 6x do alerta alto.**
Com os thresholds definidos hoje, uma falha total de ~12s que se
autorrecupera **não dispararia nenhum alerta de burn rate** — ela é boa
demais em sumir sozinha antes da janela de 5 minutos conseguir "sentir" o
impacto.

Isso não é necessariamente um problema (evita alerta por um blip que o
próprio Kubernetes já resolveu), mas é uma lacuna real pra Fase 3: se o
objetivo for **auto-remediação mais rápida que isso**, o gatilho não pode
depender só de burn rate sobre taxa de erro — precisa de algo mais direto,
tipo "endpoints prontos do `estoque-service` caiu pra zero"
(`kube_endpoint_address_available` do `kube-state-metrics`, que já está
raspado pelo `monitoring`). Fica registrado como requisito de design pra
quando o `remediation-controller` for desenhado.

## Conclusão

- **Hipótese confirmada**: `mode: all` gerou uma violação real e mensurável
  do SLO de disponibilidade — o gatilho que faltava depois do Game Day 001.
- **Padrão de falha diferente do Game Day 001**: falha total → erro rápido
  (sem latência); falha parcial → latência alta (sem erro). Os dois SLIs
  capturam problemas complementares, nenhum sozinho seria suficiente.
- **MTTR de ~12s neste caso**, sem intervenção humana — mas variável (~70s
  no Game Day 001 quando o pod de reposição flapou).
- **Achado de design pra Fase 3**: os alertas de burn rate multi-janela
  (como definidos) não pegam uma falha total que se resolve em segundos.
  Auto-remediação de latência sub-minuto precisa de um sinal mais direto
  que taxa de erro agregada em 5m.

## Próximo experimento

Com falhas totais e parciais de pod já cobertas, os candidatos naturais são
`NetworkChaos` (latência/perda de pacote entre `pedidos-service` e
`estoque-service`, sem matar pod — testa timeout e retry, não só
disponibilidade) e `StressChaos` (CPU/memória, pra ver o HPA reagir sob
caos, não só o ReplicaSet). Qualquer um dos dois já serve de base concreta
pra desenhar o `remediation-controller` da Fase 3.
