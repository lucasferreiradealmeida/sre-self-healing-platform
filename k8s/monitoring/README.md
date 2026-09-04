# Observabilidade — kube-prometheus-stack

Prometheus + Grafana + Alertmanager (via `prometheus-operator`) instalados com
Helm, em configuração **enxuta** para caber no cluster local de VMs Multipass.

## Instalação

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

helm install monitoring prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace \
  -f k8s/monitoring/kube-prometheus-stack.values.yaml

kubectl apply -f k8s/monitoring/servicemonitors.yaml
```

## O que os values ajustam (`kube-prometheus-stack.values.yaml`)

| Ajuste | Motivo |
|---|---|
| `alertmanager.enabled: false` | Volta na Fase 3, roteando para o webhook do `remediation-controller`. |
| `kubeControllerManager/Scheduler/Proxy/Etcd.enabled: false` | No kubeadm esses componentes sobem com bind em `127.0.0.1`; o scrape falharia e sujaria os dashboards. |
| `retention: 12h`, sem PVC (emptyDir) | O cluster não tem StorageClass default. Retenção curta; perder histórico num restart é aceitável nesta fase. Para persistir: `local-path-provisioner` + `storageSpec`. |
| `*SelectorNilUsesHelmValues: false` | Faz o Prometheus descobrir **todos** os ServiceMonitor/PodMonitor/PrometheusRule, não só os com `release=monitoring`. |
| `resources.requests/limits` baixos em todos os componentes | Prometheus 300Mi/512Mi, Grafana 160Mi/400Mi, resto <128Mi. |

## ServiceMonitors (`servicemonitors.yaml`)

Um por serviço (`pedidos`/`estoque`/`notificacao`), scrape da porta `http` em
`/metrics` a cada 30s.

**`metricRelabelings`**: a app expõe o label `endpoint` (a rota HTTP), que
colide com o label `endpoint` que o ServiceMonitor injeta (nome da porta). O
Prometheus renomeia o da app para `exported_endpoint`; os relabelings copiam
esse valor para um label limpo **`route`** e descartam `exported_endpoint`.
Assim as queries de SLO ficam legíveis:

```promql
sum(rate(http_requests_total{job="pedidos-service", route="/pedidos", status=~"5.."}[5m]))
```

## Acesso

```bash
# Grafana (admin / prom-operator)
kubectl -n monitoring port-forward svc/monitoring-grafana 3000:80

# Prometheus
kubectl -n monitoring port-forward svc/monitoring-kube-prometheus-prometheus 9090:9090
```

## Gerador de carga

`loadgen` (Deployment em `sre-platform`) manda um mix contínuo de pedidos
válidos, `409` (estoque insuficiente) e `404` (produto inexistente) contra o
`pedidos-service`, para gerar SLI real. Escalar para 0 quando não precisar:

```bash
kubectl -n sre-platform scale deploy/loadgen --replicas=0
```

## Limitação conhecida do ambiente local

O `fsync` do disco virtual do Multipass no Windows é lento; com o
`prometheus-operator` (watch/list pesado de CRDs) a latência da API da
control-plane fica na casa de **300ms–1s** (picos ocasionais de alguns
segundos). Não afeta a coleta de SLI da app (o Prometheus raspa os pods pela
rede, não pela API). O caminho Terraform/AWS (`../../terraform/`), com disco
SSD real, não tem esse gargalo. Mitigações aplicadas: `etcd defrag`,
control-plane com 3 GiB, componentes de control-plane não-scrapeados.
