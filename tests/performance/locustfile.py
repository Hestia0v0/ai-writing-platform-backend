"""
Performance / load tests using Locust.

Run:
  locust -f tests/performance/locustfile.py --host http://localhost:8000 \
         --users 10 --spawn-rate 2 --run-time 60s --headless

SLA targets (checked at test_stop):
  inference /generate  p95 < 8 000 ms
  documents /process   p95 < 15 000 ms
"""

import io
import os

from locust import HttpUser, between, events, task

# ── Test credentials (override via env vars for CI) ───────────────────────────

_TEST_EMAIL    = os.getenv("LOCUST_EMAIL",    "perf@example.com")
_TEST_PASSWORD = os.getenv("LOCUST_PASSWORD", "perfpassword123")

# ── Sample payloads ───────────────────────────────────────────────────────────

_SAMPLE_ESSAY = (
    "The rapid expansion of artificial intelligence technologies has created both "
    "remarkable opportunities and profound challenges for contemporary society. Machine "
    "learning systems now influence decisions in healthcare, finance, criminal justice, "
    "and education, raising urgent questions about fairness, accountability, and "
    "transparency. Proponents argue that AI dramatically improves diagnostic accuracy "
    "and operational efficiency, while critics warn of entrenched biases and erosion "
    "of human agency. The tension between innovation and regulation defines the current "
    "policy landscape. Governments worldwide are drafting frameworks to govern AI, yet "
    "enforcement remains fragmented across jurisdictions. Meanwhile, private sector "
    "investment continues to accelerate, outpacing public oversight capacity. A balanced "
    "approach requires multistakeholder collaboration: technologists, ethicists, "
    "lawmakers, and affected communities must collectively shape norms that maximise "
    "benefit while mitigating harm. Without such cooperation, the transformative "
    "promise of artificial intelligence risks being undermined by inequality and mistrust."
)  # ~200 words

_SAMPLE_TXT = _SAMPLE_ESSAY.encode("utf-8")


# ── Locust user ───────────────────────────────────────────────────────────────

class APIGatewayUser(HttpUser):
    wait_time = between(1, 3)
    _token: str = ""
    _doc_counter: int = 0

    def on_start(self) -> None:
        """Register (if needed) then log in to obtain a JWT token."""
        self.client.post(
            "/api/v1/auth/register",
            json={"email": _TEST_EMAIL, "password": _TEST_PASSWORD},
        )
        resp = self.client.post(
            "/api/v1/auth/login",
            json={"email": _TEST_EMAIL, "password": _TEST_PASSWORD},
        )
        if resp.status_code == 200:
            self._token = resp.json().get("access_token", "")
        else:
            self._token = ""

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    @task(3)
    def inference_generate(self) -> None:
        doc_id = f"perf-{self.environment.runner.user_count}-{id(self)}"
        self.client.post(
            "/api/v1/inference/generate",
            json={"document_id": doc_id, "text": _SAMPLE_ESSAY},
            headers=self._auth_headers(),
            name="/api/v1/inference/generate",
        )

    @task(2)
    def document_process(self) -> None:
        self._doc_counter += 1
        filename = f"perf_essay_{self._doc_counter}.txt"
        self.client.post(
            "/api/v1/pipelines/documents/process",
            files={"file": (filename, io.BytesIO(_SAMPLE_TXT), "text/plain")},
            headers=self._auth_headers(),
            name="/api/v1/pipelines/documents/process",
        )

    @task(5)
    def list_documents(self) -> None:
        self.client.get(
            "/api/v1/pipelines/documents/",
            headers=self._auth_headers(),
            name="/api/v1/pipelines/documents/",
        )


# ── SLA check at test stop ────────────────────────────────────────────────────

_SLA: list[tuple[str, float]] = [
    ("/api/v1/inference/generate",          8_000.0),
    ("/api/v1/pipelines/documents/process", 15_000.0),
]


@events.test_stop.add_listener
def check_sla(environment, **_kwargs) -> None:
    print("\n" + "=" * 60)
    print("SLA REPORT")
    print("=" * 60)
    passed = True
    for endpoint, limit_ms in _SLA:
        stats = environment.stats.get(endpoint, "POST")
        if stats is None or stats.num_requests == 0:
            print(f"  SKIP  {endpoint} — no requests recorded")
            continue
        p95 = stats.get_response_time_percentile(0.95)
        status = "PASS" if p95 < limit_ms else "FAIL"
        if status == "FAIL":
            passed = False
        print(f"  {status}  {endpoint}  p95={p95:.0f}ms  limit={limit_ms:.0f}ms")
    print("=" * 60)
    print("Overall:", "PASS" if passed else "FAIL")
    print()
