"""Unit tests — API Gateway"""
import pytest
from fastapi.testclient import TestClient
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../backend/api_gateway"))
import main as gateway_main
from main import app

client = TestClient(app)


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["service"] == "api_gateway"


def test_health():
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_unhandled_error_response_includes_cors_headers(monkeypatch):
    origin = "http://localhost:5173"

    def fail_to_connect():
        raise RuntimeError("database failure")

    monkeypatch.setattr(gateway_main.auth, "db_conn", fail_to_connect)
    error_client = TestClient(app, raise_server_exceptions=False)
    response = error_client.post(
        "/auth/email-code/send",
        json={"email": "test@example.com"},
        headers={"Origin": origin},
    )

    assert response.status_code == 500
    assert response.headers["access-control-allow-origin"] == origin
    assert response.json() == {"detail": "Internal server error."}
