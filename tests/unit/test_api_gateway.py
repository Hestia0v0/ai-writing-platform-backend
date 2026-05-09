"""Unit tests — API Gateway"""
import pytest
from fastapi.testclient import TestClient
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../backend/api_gateway"))
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
