# Chaos Mesh

Motor de injeção de falhas da Fase 2. Os experimentos em si (os `PodChaos`,
`NetworkChaos` etc. que você realmente aplica) ficam em `../../chaos-experiments/`
— esta pasta só tem a instalação da plataforma.

## Instalação

O host Windows não tem `helm`; rode a partir da VM control-plane (que já tem
`helm`/`kubectl` configurados, ver `../../docs/local-cluster-setup.md`):

```bash
multipass transfer k8s/chaos-mesh/chaos-mesh.values.yaml k8s-control-plane:/tmp/chaos-mesh.values.yaml

multipass exec k8s-control-plane -- helm repo add chaos-mesh https://charts.chaos-mesh.org
multipass exec k8s-control-plane -- helm repo update
multipass exec k8s-control-plane -- helm install chaos-mesh chaos-mesh/chaos-mesh \
  -n chaos-mesh --create-namespace \
  --version 2.8.4 \
  -f /tmp/chaos-mesh.values.yaml
```

> No PowerShell/Git Bash, se `multipass exec`/`transfer` reclamar de um path
> tipo `C:/Users/.../Temp/...` em vez do path Linux esperado, é o MSYS
> convertendo o argumento antes de chamar o `multipass.exe`. Rode com
> `MSYS2_ARG_CONV_EXCL="*"` na frente do comando (bash) — ou use o PowerShell
> puro, que não tem esse problema.

## O que os values ajustam (`chaos-mesh.values.yaml`)

| Ajuste | Motivo |
|---|---|
| `chaosDaemon.runtime: containerd` | Nosso runtime é containerd, não docker (default do chart). |
| `controllerManager.replicaCount: 1` | Default é 3 (HA de produção); 1 basta pro laboratório e economiza ~512Mi de requests. |
| `dashboard.service.type: ClusterIP` | Default é `NodePort`; mesmo padrão do Grafana/Prometheus — acesso via `port-forward`, sem expor porta na VM. |

Pegada de memória real medida no cluster local (2 nós): foi de 42% para 54%
de requests no worker — ver `docs/game-days/` pra como validar antes de
instalar (`multipass info <vm>` / `kubectl describe nodes` | `Allocated
resources`).

## Acesso ao dashboard

```bash
kubectl -n chaos-mesh port-forward svc/chaos-dashboard 2333:2333
```

Abra `http://localhost:2333`. Autenticação usa um token de uma ServiceAccount
com RBAC pro Chaos Mesh — ver a doc oficial se for habilitar login; pra rodar
experimentos localmente, `kubectl apply -f chaos-experiments/*.yaml` já
funciona sem entrar no dashboard.

## Rodando um experimento

```bash
kubectl apply -f chaos-experiments/pod-kill-estoque.yaml
kubectl -n sre-platform get podchaos pod-kill-estoque -o yaml   # status/eventos
kubectl delete -f chaos-experiments/pod-kill-estoque.yaml       # limpa depois
```

Cada experimento documentado como Game Day em `../../docs/game-days/`, com
hipótese declarada antes de rodar e o resultado real (SLO aguentou ou não —
ver `../../docs/slo-definitions.md`).

## Limitação conhecida: `NetworkChaos` com `target` não funciona neste cluster

`delay`/`loss`/`corrupt`/`duplicate`/`bandwidth` com `target` dependem do
`chaos-daemon` marcar pacotes via `iptables` (tabela **legacy**). Este
Ubuntu 26.04 roda `kube-proxy`/Calico 100% em **nftables** — a regra do
Chaos Mesh existe, mas numa tabela que o kernel não usa pra filtrar o
tráfego real, então o efeito é zero (confirmado com `tc -s qdisc show`
mostrando `Sent 0 bytes 0 pkt`). Workaround em uso: `NetworkChaos` sem
`target` (afeta todo o egress do pod-fonte, não só o destino desejado) —
ver [Game Day 003](../../docs/game-days/2026-09-04-network-delay-estoque.md)
pra detalhes e o efeito colateral que isso causou (liveness probe também
atrasada → pods reiniciando sozinhos). Corrigir de verdade exigiria
`update-alternatives --set iptables /usr/sbin/iptables-legacy` nos dois nós
+ restart de `kube-proxy`/`calico-node` — não aplicado ainda (mexe no
dataplane do cluster inteiro).

## Próximo passo

Blast radius pequeno (`mode: one`) validado no
[Game Day 001](../../docs/game-days/2026-09-04-pod-kill-estoque.md); blast
radius total (`mode: all`, violação real do SLO) validado no
[Game Day 002](../../docs/game-days/2026-09-04-pod-kill-estoque-all.md) —
achou uma lacuna real nos alertas de burn rate multi-janela. Latência de
rede validada no [Game Day 003](../../docs/game-days/2026-09-04-network-delay-estoque.md)
— achou uma lacuna maior ainda: o SLI atual (`slo-definitions.md`) não
enxerga uma falha que mata o processo antes dele conseguir reportar o
próprio erro. Próximo: `StressChaos` (CPU/memória, não depende de
iptables — testa o HPA sob caos).
