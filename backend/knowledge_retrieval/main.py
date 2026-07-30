import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from db import close_pool, get_pool
from embedder import embed_sync
from routers import health, retrieval

logger = logging.getLogger(__name__)
_STARTUP_DB_RETRIES = int(os.getenv("STARTUP_DB_RETRIES", "10"))
_STARTUP_DB_RETRY_DELAY_SECONDS = float(os.getenv("STARTUP_DB_RETRY_DELAY_SECONDS", "2"))
_WARMUP_ON_STARTUP = os.getenv("EMBEDDING_WARMUP_ON_STARTUP", "false").lower() == "true"


@asynccontextmanager
async def lifespan(app: FastAPI):
    for attempt in range(1, _STARTUP_DB_RETRIES + 1):
        try:
            await get_pool()
            break
        except Exception as exc:  # noqa: BLE001
            if attempt == _STARTUP_DB_RETRIES:
                logger.warning(
                    "Database pool initialization failed after %s attempts: %s",
                    _STARTUP_DB_RETRIES,
                    exc,
                )
                break
            logger.warning(
                "Database pool initialization attempt %s/%s failed: %s",
                attempt,
                _STARTUP_DB_RETRIES,
                exc,
            )
            await asyncio.sleep(_STARTUP_DB_RETRY_DELAY_SECONDS)

    if _WARMUP_ON_STARTUP:
        try:
            # Warmup is best-effort only. Startup must not fail if model download
            # is slow/unavailable in CI or restricted networks.
            await asyncio.to_thread(embed_sync, "warmup")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Embedding warmup skipped due to startup error: %s", exc)

    yield
    await close_pool()


app = FastAPI(title="Knowledge Retrieval Service", version="0.1.0", lifespan=lifespan)

app.include_router(health.router, prefix="/health", tags=["health"])
app.include_router(retrieval.router, prefix="/retrieval", tags=["retrieval"])


@app.get("/")
async def root():
    return {"service": "knowledge_retrieval", "status": "ok"}
