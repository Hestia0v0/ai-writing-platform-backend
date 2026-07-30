"""
Integration tests — API Gateway routing against live docker-compose services.
"""
from datetime import datetime, timedelta, timezone
import os

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


@pytest.mark.integration
def test_gateway_health(http_client):
    response = http_client.get(f"{GATEWAY_URL}/health/")
    assert response.status_code == 200


@pytest.mark.integration
def test_gateway_proxy_inference_health(http_client):
    response = http_client.get(
        f"{GATEWAY_URL}/api/v1/inference/health/",
        headers={"Authorization": _ci_bearer_token()},
    )
    assert response.status_code == 200


@pytest.mark.integration
def test_gateway_proxy_pipelines_health(http_client):
    response = http_client.get(
        f"{GATEWAY_URL}/api/v1/pipelines/health/",
        headers={"Authorization": _ci_bearer_token()},
    )
    assert response.status_code == 200
