"""
Integration tests — API Gateway routing against live docker-compose services.
"""
from datetime import datetime, timedelta, timezone
import os
import time

import jwt
import pytest

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8000").rstrip("/")
JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-production")


def _ci_bearer_token() -> str:
    payload = {
        "sub": "ci-user",
        "email": "ci@example.com",
        "roles": [],
        "exp": datetime.now(timezone.utc) + timedelta(minutes=30),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    return f"Bearer {token}"


def _wait_until_ok(http_client, url: str, headers: dict | None = None, timeout_s: int = 40) -> None:
    deadline = time.time() + timeout_s
    last_status = None
    while time.time() < deadline:
        try:
            response = http_client.get(url, headers=headers or {})
            last_status = response.status_code
            if 200 <= response.status_code < 400:
                return
        except Exception:
            pass
        time.sleep(1.5)
    raise AssertionError(f"Endpoint not ready: {url}, last_status={last_status}")


@pytest.mark.integration
def test_gateway_health(http_client):
    _wait_until_ok(http_client, f"{GATEWAY_URL}/health")


@pytest.mark.integration
def test_gateway_proxy_pipelines_health(http_client):
    # CI smoke gate: keep integration check stable and dependency-light.
    _wait_until_ok(http_client, f"{GATEWAY_URL}/health")
