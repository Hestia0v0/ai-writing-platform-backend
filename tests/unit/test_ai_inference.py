"""Unit tests — AI Inference Service"""
import pytest
from fastapi.testclient import TestClient
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../backend/ai_inference"))
from main import app

client = TestClient(app)


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["service"] == "ai_inference"


def test_health():
    response = client.get("/health/")
    assert response.status_code == 200


def test_generate_stub():
    response = client.post(
        "/inference/generate",
        json={"document_id": "doc-001", "prompt": "Grade this essay."},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["document_id"] == "doc-001"
    assert "result" in data


def test_hitl_queue():
    response = client.get("/hitl/queue")
    assert response.status_code == 200


def test_batch_submit():
    response = client.post(
        "/batch/submit",
        json={"job_id": "job-001", "document_ids": ["d1", "d2"], "operation": "grade"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "queued"
