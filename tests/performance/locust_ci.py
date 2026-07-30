"""
CI-friendly load/stress tests for API Gateway.

This profile intentionally targets a stable public health endpoint so it can
run reliably in GitHub Actions on every dispatch/PR.
"""
from locust import HttpUser, between, task


class GatewaySmokeUser(HttpUser):
    wait_time = between(1, 2)

    @task
    def gateway_health(self) -> None:
        self.client.get("/health", name="GET /health")
