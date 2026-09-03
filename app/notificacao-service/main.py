import time
import logging

from fastapi import FastAPI, Request
from fastapi.responses import Response
from prometheus_client import Counter, Histogram, generate_latest, CONTENT_TYPE_LATEST

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("notificacao-service")

app = FastAPI(title="Notificacao Service")

REQUEST_COUNT = Counter(
    "http_requests_total", "Total de requisicoes HTTP", ["method", "endpoint", "status"]
)
REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds", "Latencia das requisicoes HTTP", ["method", "endpoint"]
)
ERROR_COUNT = Counter(
    "http_errors_total", "Total de erros HTTP", ["method", "endpoint"]
)

NOTIFICACOES_ENVIADAS = []


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
    return {"status": "ok", "service": "notificacao-service"}


@app.get("/metrics")
async def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/notificar")
async def notificar(payload: dict):
    logger.info(f"Notificacao recebida: {payload}")
    NOTIFICACOES_ENVIADAS.append(payload)
    return {"status": "notificado", "total_notificacoes": len(NOTIFICACOES_ENVIADAS)}
