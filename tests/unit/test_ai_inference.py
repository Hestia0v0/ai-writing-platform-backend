"""Unit tests — AI Inference Service"""
import importlib.util
import os
from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

AI_INFERENCE_DIR = Path(__file__).resolve().parents[2] / "backend" / "ai_inference"
AI_INFERENCE_MAIN = AI_INFERENCE_DIR / "main.py"


def _purge_conflicting_modules() -> None:
    """
    Keep tests deterministic when another service test has already imported
    top-level modules with the same names (main/auth/db/routers/dependencies).
    """
    prefixes = ("main", "auth", "db", "routers", "dependencies")
    for module_name in list(sys.modules):
        if module_name in prefixes or module_name.startswith(
            tuple(f"{prefix}." for prefix in prefixes)
        ):
            sys.modules.pop(module_name, None)


@pytest.fixture
def client():
    """
    延迟创建 TestClient：仅在某个用例真正需要 HTTP 客户端时才执行。

    说明：
    - `from main import app` 放在此处，避免在 pytest 收集本模块时加载整个应用栈。
    - `TestClient(app)` 会触发 ASGI lifespan startup（含 init_db），故必须与收集阶段解耦。
    """
    _purge_conflicting_modules()
    ai_inference_path = str(AI_INFERENCE_DIR)
    path_inserted = False
    if ai_inference_path not in sys.path:
        sys.path.insert(0, ai_inference_path)
        path_inserted = True

    spec = importlib.util.spec_from_file_location("ai_inference_test_main", AI_INFERENCE_MAIN)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    try:
        with TestClient(module.app) as test_client:
            yield test_client
    finally:
        if path_inserted:
            sys.path.remove(ai_inference_path)


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


def test_hitl_queue_requires_reviewer_role(client):
    response = client.get("/hitl/queue")
    assert response.status_code == 403


def test_hitl_queue(client):
    response = client.get("/hitl/queue", headers={"X-User-Role": "reviewer"})
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
