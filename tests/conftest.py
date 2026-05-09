import pytest
import httpx


@pytest.fixture(scope="session")
def http_client():
    with httpx.Client(timeout=10.0) as client:
        yield client
