"""Unit tests — Knowledge Retrieval Service"""
import pytest
from fastapi.testclient import TestClient
import sys, os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../backend/knowledge_retrieval"))
from main import app

client = TestClient(app)


def test_root():
    response = client.get("/")
    assert response.status_code == 200


def test_index_document():
    response = client.post(
        "/retrieval/index",
        json={"document_id": "doc-001", "content": "Sample document text."},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "indexed"


def test_semantic_search():
    response = client.post(
        "/retrieval/search",
        json={"query": "essay writing", "top_k": 3},
    )
    assert response.status_code == 200
    assert isinstance(response.json(), list)
