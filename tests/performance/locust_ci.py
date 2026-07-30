"""
CI-friendly load/stress tests for API Gateway.

This profile intentionally avoids email-code registration and LLM-dependent
paths, so it can run reliably in GitHub Actions on every dispatch/PR.
"""
from datetime import datetime, timedelta, timezone
import os

import jwt
from locust import HttpUser, between, task

JWT_SECRET = os.getenv("JWT_SECRET", "change-me-in-production")


def _auth_header() -> dict[str, str]:
    payload = {
        "sub": "locust-ci-user",
        "email": "locust-ci@example.com",
        "roles": [],
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    token = jwt.encode(payload, JWT_SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {token}"}


class GatewaySmokeUser(HttpUser):
    wait_time = between(1, 2)
    _headers = _auth_header()

    @task(4)
    def gateway_health(self) -> None:
        self.client.get("/health/", name="GET /health/")

    @task(3)
    def inference_health_via_gateway(self) -> None:
        self.client.get(
            "/api/v1/inference/health/",
            headers=self._headers,
            name="GET /api/v1/inference/health/",
        )

    @task(3)
    def pipelines_health_via_gateway(self) -> None:
        self.client.get(
            "/api/v1/pipelines/health/",
            headers=self._headers,
            name="GET /api/v1/pipelines/health/",
        )
