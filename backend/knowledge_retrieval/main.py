from contextlib import asynccontextmanager
from fastapi import FastAPI
from routers import health, retrieval
from db import get_pool, close_pool
from embedder import embed_sync


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_pool()
    embed_sync("warmup")  # pre-download the embedding model
    yield
    await close_pool()


app = FastAPI(title="Knowledge Retrieval Service", version="0.1.0", lifespan=lifespan)

app.include_router(health.router, prefix="/health", tags=["health"])
app.include_router(retrieval.router, prefix="/retrieval", tags=["retrieval"])


@app.get("/")
async def root():
    return {"service": "knowledge_retrieval", "status": "ok"}
