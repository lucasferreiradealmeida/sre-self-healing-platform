import os
import time
import logging

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("estoque-service")

app = FastAPI(title="Estoque Service")

NOTIFICACAO_SERVICE_URL = os.getenv("NOTIFICACAO_SERVICE_URL", "http://notificacao-service:8000")
ESTOQUE_MINIMO = int(os.getenv("ESTOQUE_MINIMO", "10"))

REQUEST_COUNT = Counter(
    "http_requests_total", "Total de requisicoes HTTP", ["method", "endpoint", "status"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds", "Latencia das requisicoes HTTP", ["method", "endpoint"]
)
ERROR_COUNT = Counter(
    "http_errors_total", "Total de erros HTTP", ["method", "endpoint"]
)

# Estoque em memoria - simples de proposito, o foco do projeto e' confiabilidade, nao persistencia
ESTOQUE_DB = {
    "produto-a": 50,
    "produto-b": 8,
    "produto-c": 100,
}


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
    return {"status": "ok", "service": "estoque-service"}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/estoque/verificar/{produto_id}")
async def verificar_estoque(produto_id: str, quantidade: int):
    quantidade_disponivel = ESTOQUE_DB.get(produto_id)

    if quantidade_disponivel is None:
        raise HTTPException(status_code=404, detail="Produto nao encontrado")

    disponivel = quantidade_disponivel >= quantidade

    if disponivel:
        ESTOQUE_DB[produto_id] -= quantidade

        # Segunda dependencia da cadeia: estoque baixo aciona notificacao.
        # Se falhar, nao derruba o pedido - so registra o problema.
        if ESTOQUE_DB[produto_id] < ESTOQUE_MINIMO:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    await client.post(
                        f"{NOTIFICACAO_SERVICE_URL}/notificar",
                        json={
                            "tipo": "estoque_baixo",
                            "produto_id": produto_id,
                            "quantidade_restante": ESTOQUE_DB[produto_id],
                        },
                    )
            except httpx.RequestError as exc:
                logger.warning(f"Falha ao notificar estoque baixo: {exc}")

    return {
        "produto_id": produto_id,
        "disponivel": disponivel,
        "quantidade_restante": ESTOQUE_DB.get(produto_id, 0),
    }
