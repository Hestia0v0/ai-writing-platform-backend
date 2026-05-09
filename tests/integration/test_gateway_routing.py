"""
Integration tests — API Gateway proxy routing.
Requires all services running (use docker-compose or pytest fixtures with httpx).
"""
import pytest

# TODO: replace with service URLs from env / docker-compose
GATEWAY_URL = "http://localhost:8000"


@pytest.mark.integration
def test_gateway_health(http_client):
    response = http_client.get(f"{GATEWAY_URL}/health/")
    assert response.status_code == 200


@pytest.mark.integration
def test_gateway_proxy_inference(http_client):
    response = http_client.get(f"{GATEWAY_URL}/api/v1/inference/stub")
    assert response.status_code == 200
