"""
Shared LLM client factory for all evaluation sub-agents.
"""
from __future__ import annotations

import json
import logging
import os
import re

logger = logging.getLogger(__name__)

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_EVAL_MODEL = "deepseek-chat"


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
