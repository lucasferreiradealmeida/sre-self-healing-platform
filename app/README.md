# Microsserviços — Fase 1

Três serviços com dependência real entre si, simulando um fluxo de e-commerce simplificado:

```
pedidos-service → estoque-service → notificacao-service
```

- **pedidos-service**: recebe o pedido, consulta o estoque antes de confirmar
- **estoque-service**: valida disponibilidade, dispara notificação se o estoque ficar baixo
- **notificacao-service**: recebe e registra a notificação

Cada serviço expõe `/health` e `/metrics` (formato Prometheus) nativamente.

## Rodando localmente

```bash
cd app
docker compose up --build
```

Serviços disponíveis em:
- `http://localhost:8001` — pedidos-service
- `http://localhost:8002` — estoque-service
- `http://localhost:8003` — notificacao-service

## Testando o fluxo

```bash
# Pedido de sucesso (estoque suficiente)
curl -X POST "http://localhost:8001/pedidos?produto_id=produto-a&quantidade=5"

# Pedido que aciona notificação de estoque baixo
curl -X POST "http://localhost:8001/pedidos?produto_id=produto-b&quantidade=3"

# Pedido que falha por falta de estoque
curl -X POST "http://localhost:8001/pedidos?produto_id=produto-b&quantidade=999"

# Ver métricas Prometheus de qualquer serviço
curl http://localhost:8001/metrics
```

## Próximo passo

Deploy no Kubernetes (manifests em `../k8s/`) com HPA configurado, e scrape das métricas pelo Prometheus para começar a definir os SLOs de cada serviço.
