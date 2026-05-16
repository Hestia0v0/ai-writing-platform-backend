import os
import ssl
import asyncpg
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

POSTGRES_DSN = os.getenv("POSTGRES_DSN", "postgresql://platform:platform@postgres:5432/platform")


def _parse_dsn_ssl(dsn: str) -> tuple[str, ssl.SSLContext | None]:
    """Strip SSL params from DSN and return (clean_dsn, ssl_context).

    asyncpg requires ssl= as an SSLContext object; it does not read
    sslrootcert or sslmode from the URL query string.
    """
    parsed = urlparse(dsn)
    params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

    sslmode = params.pop("sslmode", "disable")
    sslrootcert = params.pop("sslrootcert", None)

    ssl_ctx = None
    if sslmode not in ("disable", "allow", "prefer"):
        ssl_ctx = ssl.create_default_context(cafile=sslrootcert) if sslrootcert else ssl.create_default_context()
        if sslmode == "require":
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE

    clean_dsn = urlunparse(parsed._replace(query=urlencode(params)))
    return clean_dsn, ssl_ctx


_clean_dsn, _ssl_ctx = _parse_dsn_ssl(POSTGRES_DSN)
_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(_clean_dsn, ssl=_ssl_ctx)
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
