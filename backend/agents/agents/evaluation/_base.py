"""
Shared LLM client factory for all evaluation sub-agents.

Two factories live here:

  build_async_client()      — legacy raw AsyncOpenAI client. Still used by
                               agents/refinement.py; left untouched.

  build_structured_chain()  — LangChain-based factory used by the evaluation
                               panel (vocab/structure/style/master_judge/arbiter).
                               Wraps the DeepSeek call with:
                                 1. Structured output (Pydantic schema in, validated
                                    instance out) — no more manual json.loads() +
                                    markdown-fence stripping.
                                 2. Retry (transient network/rate-limit blips).
                                 3. Fallback chain: DeepSeek primary model →
                                    DeepSeek fallback model (different tier) →
                                    Anthropic (cross-provider, only if
                                    ANTHROPIC_API_KEY is set).
                               Returns None when no DEEPSEEK_API_KEY is configured
                               at all, same "None = mock mode" contract the callers
                               already rely on.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import TypeVar

from pydantic import BaseModel

logger = logging.getLogger(__name__)

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_EVAL_MODEL = "deepseek-chat"

# Fallback tier: a different DeepSeek model (catches capacity/quality issues on
# the primary tier) then, if configured, a different provider entirely (catches
# a DeepSeek-wide outage). Both are opt-in via env — missing keys just skip
# that link in the chain, they never turn the whole chain off.
FALLBACK_DEEPSEEK_MODEL = os.getenv("EVAL_FALLBACK_MODEL", "deepseek-reasoner")
FALLBACK_ANTHROPIC_MODEL = os.getenv("EVAL_FALLBACK_ANTHROPIC_MODEL", "claude-haiku-4-5")

T = TypeVar("T", bound=BaseModel)


# ── Legacy raw client (unchanged — used by agents/refinement.py) ──────────────

def build_async_client(api_key: str | None = None):
    """Return an AsyncOpenAI client configured for DeepSeek, or None in mock mode."""
    key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
    if not key:
        return None
    try:
        from openai import AsyncOpenAI  # noqa: PLC0415
        return AsyncOpenAI(api_key=key, base_url=DEEPSEEK_BASE_URL)
    except ImportError:
        logger.warning("openai package not installed — evaluation running in mock mode")
        return None


def parse_json_response(raw: str) -> dict:
    """Strip markdown fences and parse JSON from LLM response."""
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    return json.loads(raw)


# ── LangChain structured + fallback chain ──────────────────────────────────────

def _deepseek_chat(model: str, api_key: str, temperature: float):
    from langchain_openai import ChatOpenAI  # noqa: PLC0415
    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=DEEPSEEK_BASE_URL,
        temperature=temperature,
        timeout=20,
        max_retries=0,  # retry is handled by .with_retry() on the composed chain
    )


def _anthropic_chat(model: str, api_key: str, temperature: float):
    from langchain_anthropic import ChatAnthropic  # noqa: PLC0415
    return ChatAnthropic(model=model, api_key=api_key, temperature=temperature, timeout=20)


def build_structured_chain(
    output_schema: type[T],
    model: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.0,
):
    """
    Build a LangChain Runnable that takes a list of messages and returns a
    validated instance of *output_schema* — structured output + retry +
    cross-provider fallback all handled by LangChain's Runnable protocol.

    Returns None when DEEPSEEK_API_KEY is not configured (mock mode) or the
    required LangChain packages are not installed.
    """
    deepseek_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
    if not deepseek_key:
        return None

    try:
        primary_model = model or DEFAULT_EVAL_MODEL
        primary = _deepseek_chat(primary_model, deepseek_key, temperature)
        chain = primary.with_structured_output(output_schema, method="json_mode")
        chain = chain.with_retry(stop_after_attempt=2, wait_exponential_jitter=True)

        fallbacks = []

        if FALLBACK_DEEPSEEK_MODEL and FALLBACK_DEEPSEEK_MODEL != primary_model:
            secondary = _deepseek_chat(FALLBACK_DEEPSEEK_MODEL, deepseek_key, temperature)
            fallbacks.append(secondary.with_structured_output(output_schema, method="json_mode"))

        anthropic_key = os.getenv("ANTHROPIC_API_KEY", "")
        if anthropic_key and FALLBACK_ANTHROPIC_MODEL:
            try:
                tertiary = _anthropic_chat(FALLBACK_ANTHROPIC_MODEL, anthropic_key, temperature)
                fallbacks.append(tertiary.with_structured_output(output_schema))
            except ImportError:
                logger.warning("langchain-anthropic not installed — skipping cross-provider fallback")

        if fallbacks:
            chain = chain.with_fallbacks(fallbacks)

        return chain
    except ImportError as exc:
        logger.warning(
            "LangChain packages not installed (%s) — evaluation running in mock mode", exc
        )
        return None
