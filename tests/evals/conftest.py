"""
Shared fixtures for the deepeval suite.

These tests call the real agents (8004) and ai_inference (8001) services —
docker compose must be up and DEEPSEEK_API_KEY / ANTHROPIC_API_KEY set. They
are NOT part of the minimal unit-test CI gate (see backend-ci.yml); run
explicitly with:

    pytest tests/evals -v -m eval
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../backend/pipelines"))

from doc_processor import GradingClient, TextChunk  # noqa: E402

GOLDENS_PATH = Path(__file__).parent / "goldens" / "essays.json"

# Loose gross-sanity bands per tier — not a precision calibration target.
# Tightening these is the next step once real teacher-labeled essays (or
# HITL-overridden results) replace this hand-written seed set.
TIER_SANITY_BANDS = {
    "strong": (55.0, 100.0),
    "medium": (30.0, 90.0),
    "weak": (0.0, 65.0),
}


def load_goldens() -> list[dict]:
    return json.loads(GOLDENS_PATH.read_text(encoding="utf-8"))


def essay_chunks(text: str) -> list[TextChunk]:
    return [TextChunk(chunk_index=0, text=text, word_count=len(text.split()), char_count=len(text))]


@pytest.fixture(scope="session")
def goldens() -> list[dict]:
    return load_goldens()


@pytest.fixture
def grading_client() -> GradingClient:
    return GradingClient(
        base_url=os.getenv("AI_INFERENCE_URL", "http://localhost:8001"),
        agents_base_url=os.getenv("AGENTS_URL", "http://localhost:8004"),
    )
