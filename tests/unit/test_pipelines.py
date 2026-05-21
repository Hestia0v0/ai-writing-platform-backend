"""
Pipelines HTTP / 组件级单测（FastAPI TestClient）

本文件保留需启动完整 FastAPI 应用与 lifespan 的路由用例；lifespan 内会 await get_pool()，
依赖 asyncpg + Postgres（默认 DSN 指向 docker 内的 postgres 主机名）。

纯文档处理逻辑（TextCleaner / DocumentProcessor + mock 等）已迁至
tests/unit/test_pipelines_doc_processor.py，并由 minimal CI 白名单单独执行。

本文件不纳入 backend-ci.yml 的 minimal job，避免在无 Postgres service 的 Runner 上失败。
"""
import io
import os
import sys

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../backend/pipelines"))

from main import app

# 与 test_pipelines_doc_processor 中一致，供 /documents/process 上传用例使用。
SAMPLE_TEXT = (
    "The quick brown fox jumps over the lazy dog. " * 200
)

client = TestClient(app)


def test_pipelines_root():
    assert client.get("/").json()["service"] == "pipelines"


def test_pipeline_health():
    assert client.get("/health/").json()["status"] == "healthy"


def test_documents_list_empty():
    response = client.get("/documents/")
    assert response.status_code == 200
    assert "document_ids" in response.json()


def test_process_endpoint_txt():
    content = SAMPLE_TEXT.encode("utf-8")
    response = client.post(
        "/documents/process",
        files={"file": ("test_essay.txt", io.BytesIO(content), "text/plain")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["scoring"]["score"] >= 0


def test_process_endpoint_unsupported_type():
    response = client.post(
        "/documents/process",
        files={"file": ("script.exe", io.BytesIO(b"data"), "application/octet-stream")},
    )
    assert response.status_code == 415


def test_process_endpoint_empty_file():
    response = client.post(
        "/documents/process",
        files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")},
    )
    assert response.status_code == 400
