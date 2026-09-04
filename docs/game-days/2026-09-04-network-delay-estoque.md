# Game Day 003 — Latência de rede entre `pedidos-service` e `estoque-service`

**Data**: 2026-09-04 · **Ferramenta**: Chaos Mesh 2.8.4 (`NetworkChaos`) ·
**Ambiente**: cluster local Multipass (`docs/local-cluster-setup.md`) ·
**SLOs de referência**: [`../slo-definitions.md`](../slo-definitions.md) ·
**Continuação de**: [Game Day 002](2026-09-04-pod-kill-estoque-all.md)

## Objetivo e hipótese original

Os dois primeiros Game Days mataram pods. Este testa uma falha diferente:
`estoque-service` continua **de pé o tempo todo**, só fica lento. O
`pedidos-service` chama ele com `httpx.AsyncClient(timeout=5.0)`
(`app/pedidos-service/main.py`) — sem retry.

> Hipótese: com latência **abaixo** de 5s, `pedidos-service` deve ficar mais
> lento mas continuar respondendo (disponibilidade 100%). Com latência
> **acima** de 5s, a chamada deve estourar o timeout e virar `503` — sem
> retry, é falha na primeira tentativa.

O experimento acabou testando outra coisa, mais interessante: o que
aconteceu quando o mecanismo de injeção em si não funcionou como esperado.

## Problema 1: `target` não funciona neste cluster (nftables vs iptables-legacy)

A primeira tentativa usou `direction: to` + `target` (seletor pro
`estoque-service`), pra só afetar o tráfego `pedidos-service → estoque-service`.
O Chaos Mesh reportou sucesso, mas **nenhum pacote passou pela fila de
delay**: confirmei com `tc -s qdisc show` direto no netns do pod
(`multipass exec ... nsenter -t <pid> -n tc -s qdisc show dev eth0`) —
`qdisc netem ... Sent 0 bytes 0 pkt` do início ao fim.

Causa raiz: o `chaos-daemon` grava a regra de `iptables` que marca os
pacotes (`CLASSIFY --set-class 1:4`) na tabela **legacy**. Mas neste Ubuntu
26.04 o `kube-proxy` e o Calico usam **nftables** — confirmei que as chains
de DNAT do `kube-proxy` (`KUBE-SERVICES`) só existem em `iptables-nft`;
`iptables-legacy -t nat -S KUBE-SERVICES` retorna "No chain by that name".
A regra do Chaos Mesh existe, mas numa tabela que o kernel não usa pra
filtrar o tráfego real — é letra morta. Isso afeta qualquer `NetworkChaos`
com `target` (delay, loss, corrupt, duplicate, bandwidth); `pod-kill` e
`StressChaos` não dependem desse mecanismo.

**Workaround escolhido** (documentado em `chaos-experiments/network-delay-estoque*.yaml`):
tirar o `target`. Sem ele, o Chaos Mesh aplica o netem direto no dispositivo
(`tc qdisc add dev eth0 root netem ...`), sem CLASSIFY/iptables/ipset — mais
simples, mas afeta **todo** o egress do pod, não só o tráfego pro
`estoque-service`. Como `pedidos-service` só faz uma chamada de saída, o
efeito prático pretendido (atrasar a chamada ao estoque) é quase idêntico.
Alternativa mais correta mas mais arriscada (trocar os 2 nós pra
`iptables-legacy`, mexendo no dataplane do cluster inteiro) foi descartada
por ora — ver "Próximo experimento".

## Problema 2: o valor configurado não é o valor observado

Sem `target`, o delay pega **todo pacote** saindo do pod, inclusive o
handshake TCP (SYN/ACK) — não só o payload HTTP. Medido empiricamente
(chamada direta com `python3 -c` de dentro do pod, sem passar pelo timeout
de 5s da app): **2s configurado virou ~6.2s ponta-a-ponta**. Recalibrei pra
**500ms configurado ≈ 1.4-1.6s observado** (fase 1, abaixo do timeout) e
mantive **8s configurado** pra fase 2 (bem acima do timeout mesmo com a
margem de erro do multiplicador).

## Setup final

```yaml
# chaos-experiments/network-delay-estoque.yaml (fase 1)
spec:
  action: delay
  mode: all
  selector: {labelSelectors: {app: pedidos-service}}
  delay: {latency: "500ms", jitter: "50ms", correlation: "50"}
  direction: to
  duration: "60s"
# network-delay-estoque-timeout.yaml (fase 2): igual, latency: "8s"
```

## Linha do tempo

| Hora (UTC) | Evento |
|---|---|
| 22:41:11 | Fase 1 aplicada (500ms) |
| 22:41:38–22:41:41 | 3 "000" (conexão falhou) no `loadgen` — 1 pod reiniciou por falha de liveness |
| 22:42:17 | Fase 1 recuperada |
| 22:43:18 | Fase 2 aplicada (8s) |
| 22:43:42–22:44:20 | **38s contínuos de "000"** no `loadgen` — os dois pods reiniciando em cascata |
| 22:44:22 | Fase 2 recuperada, serviço volta a responder |

## Por que os pods reiniciaram: efeito colateral do workaround

Sem `target`, o delay se aplica a **toda** saída do pod — inclusive a
resposta que o próprio `pedidos-service` manda de volta pro `kubelet` quando
ele faz a liveness/readiness probe (`GET /health` via IP do pod, não
loopback — a resposta sai pelo `eth0`, então pega o delay igual qualquer
outro pacote). Com `timeout: 1s` na probe e respostas levando 1.4s+ (fase 1)
ou muito mais (fase 2), o `kubelet` viu falha e reiniciou o container:

```
Liveness probe failed: Get "http://192.168.230.41:8000/health":
  context deadline exceeded (Client.Timeout exceeded while awaiting headers)
Killing: Container pedidos-service failed liveness probe, will be restarted
```

Na fase 1 só 1 dos 2 pods reiniciou (a outra réplica absorveu o tráfego,
como no Game Day 001). Na fase 2 **os dois** reiniciaram de forma
correlacionada — mesma configuração de chaos, mesmo timing — deixando o
`Service` sem endpoint saudável por um tempo real.

## O achado principal: o SLI da aplicação não viu nada

Isso é mais importante que o resultado "esperado" do experimento.

**O que o Prometheus mediu** (via `http_requests_total` do próprio
`pedidos-service`, a mesma métrica usada em `slo-definitions.md`):
disponibilidade em **100%** nas duas fases, contador de `503` em **zero**.
Pela definição de SLO atual, nada de errado aconteceu.

**O que o `loadgen` viu de verdade** (cliente externo, fora do processo que
caiu — grep no log dele pelo código `000` do curl, que significa "não
conectou de jeito nenhum", nem chegou a ter um status HTTP):

| Fase | Janela | Requisições | `000` (conexão falhou) | Taxa de falha real |
|---|---|---|---|---|
| 1 (500ms) | 22:41:11–22:42:17 (66s) | 20 | 3 | 15% |
| 2 (8s) | 22:43:18–22:44:22 (64s) | 41 | **39** | **95%** |

Na fase 2, o `pedidos-service` ficou **efetivamente fora do ar por ~38s
contínuos** (confirmado também pelo `up{job="pedidos-service"}` do
Prometheus, que mostra as duas instâncias com `up=0` simultaneamente por
~45s — o scrape simplesmente não conseguiu alcançar nenhum dos dois pods).
E o SLI de disponibilidade definido em `slo-definitions.md` não registrou
**nenhuma** dessas 39 falhas, porque `http_requests_total` é incrementado
*dentro* do processo Python — um processo que estava sendo morto e
recriado não incrementa contador nenhum. A contagem reseta a cada restart;
o que aconteceu antes do crash nunca chega a ser raspado se acontecer
dentro da mesma janela de scrape de 30s.

## Conclusão

- **A hipótese original nem chegou a ser testada de verdade**: o efeito
  dominante não foi o timeout do `httpx` (o achado esperado), foi o
  `pedidos-service` derrubando a si mesmo via liveness probe.
- **SLO de disponibilidade "aguentou" segundo a métrica atual — e essa
  métrica está errada pra este cenário.** Toda a arquitetura de SLI deste
  projeto (`slo-definitions.md`) depende de contadores que o próprio
  processo expõe em `/metrics`. Isso é estruturalmente cego a qualquer
  falha que impeça o processo de responder ou de continuar vivo — exatamente
  o tipo de falha que mais importa detectar.
- **Efeito colateral vira achado de reliability**: um experimento de rede
  mal-escopado (sem `target`, por causa do problema de tooling) acabou
  expondo um problema de configuração real dos deployments — timeout de
  probe (`1s`) baixo demais pra qualquer cenário onde o pod fique
  momentaneamente lento por qualquer motivo (não só chaos: um pico de carga
  real teria o mesmo efeito).

## Ação de acompanhamento (pra Fase 3, não aplicada ainda)

1. **SLI de blackbox/sintético**: adicionar uma métrica medida de fora do
   processo (ex.: o próprio `loadgen` expondo um contador de sucesso/`000`,
   ou uma sonda dedicada) como complemento ao SLI atual em
   `slo-definitions.md`. Sem isso, qualquer incidente que mate o processo
   rápido o suficiente é invisível pro error budget.
2. **Revisitar `livenessProbe.timeoutSeconds`** dos três deployments
   (hoje `1s`) — já tinha aparecido como suspeita no Game Day 001 (flap de
   probe após um `pod-kill`) e aqui se confirma como um padrão, não um
   evento isolado.
3. Se um `NetworkChaos` com `target` (escopo fino, sem afetar a própria
   probe) for necessário no futuro, o único caminho é o mais arriscado que
   descartei aqui: `update-alternatives --set iptables /usr/sbin/iptables-legacy`
   nos dois nós + restart de `kube-proxy`/`calico-node`, unificando
   `kube-proxy`/Calico/`chaos-daemon` na mesma tabela de netfilter.

## Próximo experimento

`StressChaos` (CPU/memória) não depende de iptables — não deve ter o
problema 1 deste Game Day. Continua sendo o próximo passo natural pra ver o
HPA reagir sob caos.
