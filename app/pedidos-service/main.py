import os
import time
import logging

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pedidos-service")

app = FastAPI(title="Pedidos Service")

ESTOQUE_SERVICE_URL = os.getenv("ESTOQUE_SERVICE_URL", "http://estoque-service:8000")

# Métricas Prometheus - o mesmo padrão em todos os serviços,
# pra dar pra comparar SLIs entre eles no Grafana depois.
REQUEST_COUNT = Counter(
    "http_requests_total", "Total de requisicoes HTTP", ["method", "endpoint", "status"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds", "Latencia das requisicoes HTTP", ["method", "endpoint"]
)
ERROR_COUNT = Counter(
    "http_errors_total", "Total de erros HTTP", ["method", "endpoint"]
)

PEDIDOS_DB = {}
PEDIDO_ID_COUNTER = 0


@app.middleware("http")
async def metrics_middleware(request: Request, call_next):
    start_time = time.time()
    endpoint = request.url.path
    method = request.method
    try:
        response = await call_next(request)
        status = response.status_code
    except Exception:
        ERROR_COUNT.labels(method=method, endpoint=endpoint).inc()
        raise
    duration = time.time() - start_time
    REQUEST_LATENCY.labels(method=method, endpoint=endpoint).observe(duration)
    REQUEST_COUNT.labels(method=method, endpoint=endpoint, status=status).inc()
    return response


@app.get("/health")
async def health():
    return {"status": "ok", "service": "pedidos-service"}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/pedidos")
async def criar_pedido(produto_id: str, quantidade: int):
    global PEDIDO_ID_COUNTER

    # Aqui entra a dependencia real entre servicos: pedidos precisa
    # confirmar estoque antes de fechar. E' esse acoplamento que da
    # "carne" pro chaos engineering depois (se estoque cair, pedidos sente).
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{ESTOQUE_SERVICE_URL}/estoque/verificar/{produto_id}",
                params={"quantidade": quantidade},
            )
            resp.raise_for_status()
            estoque_data = resp.json()
    except httpx.RequestError as exc:
        logger.error(f"Erro ao chamar estoque-service: {exc}")
        raise HTTPException(status_code=503, detail="estoque-service indisponivel") from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail="Erro no estoque-service") from exc

    if not estoque_data.get("disponivel"):
        raise HTTPException(status_code=409, detail="Produto sem estoque suficiente")

    PEDIDO_ID_COUNTER += 1
    pedido_id = PEDIDO_ID_COUNTER
    PEDIDOS_DB[pedido_id] = {
        "produto_id": produto_id,
        "quantidade": quantidade,
        "status": "confirmado",
    }

    return {"pedido_id": pedido_id, "status": "confirmado", "estoque": estoque_data}


@app.get("/pedidos/{pedido_id}")
async def obter_pedido(pedido_id: int):
    pedido = PEDIDOS_DB.get(pedido_id)
    if not pedido:
        raise HTTPException(status_code=404, detail="Pedido nao encontrado")
    return pedido
