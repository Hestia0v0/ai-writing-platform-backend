"""Unit tests — AI Inference Service"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

# 将 ai_inference 包加入 sys.path，供 fixture 内延迟 import main 使用。
# （仅改路径，不加载 FastAPI 应用，故不会触发 lifespan / init_db。）
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../backend/ai_inference"))


@pytest.fixture
def client():
    """
    延迟创建 TestClient：仅在某个用例真正需要 HTTP 客户端时才执行。

    说明：
    - `from main import app` 放在此处，避免在 pytest 收集本模块时加载整个应用栈。
    - `TestClient(app)` 会触发 ASGI lifespan startup（含 init_db），故必须与收集阶段解耦。
    """
    from main import app  # noqa: PLC0415 — 故意延迟导入，见模块顶部中文说明

    with TestClient(app) as test_client:
        yield test_client


def test_root(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["service"] == "ai_inference"


def test_health(client):
    response = client.get("/health/")
    assert response.status_code == 200


def test_generate_stub(client):
    # InferenceRequest 使用字段 text（非 prompt），且 text 有最小长度校验。
    response = client.post(
        "/inference/generate",
        json={
            "document_id": "doc-001",
            "text": "Grade this essay with sufficient length for the schema.",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["document_id"] == "doc-001"
    assert "score" in data


def test_hitl_queue(client):
    response = client.get("/hitl/queue")
    assert response.status_code == 200


def test_batch_submit(client):
    # BatchSubmitRequest 需要 compositions: list[CompositionItem]，每项含 text。
    response = client.post(
        "/batch/submit",
        json={
            "job_id": "job-001",
            "compositions": [
                {
                    "composition_id": "c1",
                    "document_id": "d1",
                    "text": "First composition body with enough characters.",
                },
                {
                    "composition_id": "c2",
                    "document_id": "d2",
                    "text": "Second composition body with enough characters.",
                },
            ],
        },
    )
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
