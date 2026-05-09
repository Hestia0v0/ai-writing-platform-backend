from contextlib import asynccontextmanager
from fastapi import FastAPI
from routers import health, workflows, documents
from db import get_pool, close_pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    await get_pool()
    yield
    await close_pool()


app = FastAPI(title="Pipelines Service", version="0.1.0", lifespan=lifespan)

app.include_router(health.router, prefix="/health", tags=["health"])
app.include_router(workflows.router, prefix="/workflows", tags=["workflows"])
app.include_router(documents.router, prefix="/documents", tags=["documents"])


@app.get("/")
async def root():
    return {"service": "pipelines", "status": "ok"}
