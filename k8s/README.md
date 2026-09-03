# Deploy no Kubernetes

Manifests dos três microsserviços, prontos para o cluster kubeadm.

## 1. Build e push das imagens

Antes de aplicar os manifests, as imagens precisam estar em um registry acessível
pelo cluster (seu cluster não tem acesso às imagens locais do seu Docker Desktop).

```bash
# Login no Docker Hub (uma vez só)
docker login

# A partir da pasta app/, build e push de cada serviço
cd app

docker build -t SEU_USUARIO_DOCKERHUB/pedidos-service:latest ./pedidos-service
docker push SEU_USUARIO_DOCKERHUB/pedidos-service:latest

docker build -t SEU_USUARIO_DOCKERHUB/estoque-service:latest ./estoque-service
docker push SEU_USUARIO_DOCKERHUB/estoque-service:latest

docker build -t SEU_USUARIO_DOCKERHUB/notificacao-service:latest ./notificacao-service
docker push SEU_USUARIO_DOCKERHUB/notificacao-service:latest
```

Depois, edite o campo `image:` nos três `deployment.yaml` (dentro de `k8s/*/`),
substituindo `SEU_USUARIO_DOCKERHUB` pelo seu usuário real do Docker Hub.

## 2. Aplicar os manifests

```bash
kubectl apply -f k8s/namespace.yaml

kubectl apply -f k8s/pedidos-service/
kubectl apply -f k8s/estoque-service/
kubectl apply -f k8s/notificacao-service/
```

## 3. Verificar

```bash
kubectl get pods -n sre-platform
kubectl get svc -n sre-platform
kubectl get hpa -n sre-platform
```

Todos os pods devem ficar `Running` com `READY 1/1`. Se algum ficar em
`CrashLoopBackOff` ou `ImagePullBackOff`, o mais comum é a imagem não estar
com o nome certo no Docker Hub — confira com `kubectl describe pod <nome>`.

## 4. Testar o fluxo dentro do cluster

```bash
# Cria um pod temporário só pra testar via curl dentro do cluster
kubectl run curl-test --image=curlimages/curl -n sre-platform --rm -it -- sh

# Dentro do pod:
curl -X POST "http://pedidos-service:8000/pedidos?produto_id=produto-a&quantidade=5"
```

## 5. Scrape pelo Prometheus

Os deployments já têm as annotations `prometheus.io/scrape`, `prometheus.io/path`
e `prometheus.io/port` nos pods. Se você estiver usando o **kube-prometheus-stack**
(via Helm, como no seu outro projeto de portfólio), configure o `scrape_config`
do Prometheus para descobrir pods por annotation — ou, se preferir o padrão do
Prometheus Operator, é só trocar as annotations por um `ServiceMonitor` apontando
pro `Service` de cada microsserviço.

## Próximo passo

Com os SLIs sendo coletados de verdade pelo Prometheus, o próximo passo do
roadmap é definir os SLOs formais (`docs/slo-definitions.md`) e partir para a
Fase 2 — instalar o Chaos Mesh e começar os primeiros Game Days.
