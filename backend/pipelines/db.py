import os
import asyncpg

POSTGRES_DSN = os.getenv("POSTGRES_DSN", "postgresql://platform:platform@postgres:5432/platform")

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(POSTGRES_DSN)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
