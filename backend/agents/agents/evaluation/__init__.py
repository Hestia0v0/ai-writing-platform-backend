"""
Evaluation Panel — orchestrates the three specialist sub-agents in parallel
then hands results to the Master Judge.

Concurrency model:
  asyncio.gather runs VocabGrammarAgent, StructureLogicAgent, and StyleAgent
  simultaneously so all three LLM calls happen at the same time, keeping the
  total latency close to that of a single call (< 8 s target).

Caching (US-17):
  Before running the full pipeline the panel checks EvalCacheService for a
  previously computed result using a SHA-256 hash of the essay + metadata.
  On a hit the cached result is returned immediately with cache_hit=True,
  saving the LLM cost and the full < 8 s latency.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from core.models import EvaluationRequest, EvaluationResult
from core.cache import EvalCacheService
from agents.evaluation.vocab_grammar import VocabGrammarAgent
from agents.evaluation.structure_logic import StructureLogicAgent
from agents.evaluation.style import StyleAgent
from agents.evaluation.master_judge import MasterJudgeAgent

logger = logging.getLogger(__name__)


class EvaluationPanel:
    """
    Singleton-safe orchestrator.  Instantiate once at startup.
    All sub-agents share the same api_key / model config.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        cache: Optional[EvalCacheService] = None,
    ) -> None:
        self._vocab = VocabGrammarAgent(api_key=api_key, model=model)
        self._structure = StructureLogicAgent(api_key=api_key, model=model)
        self._style = StyleAgent(api_key=api_key, model=model)
        self._judge = MasterJudgeAgent(api_key=api_key, model=model)
        self._cache = cache or EvalCacheService()

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        """
        Full evaluation pipeline:
          Phase 0 — cache lookup (US-17)
          Phase 1 — three sub-agents run CONCURRENTLY via asyncio.gather
            • framework is forwarded to StructureLogicAgent so Chinese 起承转合
              essays are assessed against the correct four-stage rubric.
          Phase 2 — Master Judge aggregates and produces the final EvaluationResult
          Phase 3 — store result in cache
        """
        cache_key = self._cache.make_key(
            text=request.text,
            language=request.language.value,
            framework=request.framework.value if request.framework else None,
            technique=request.technique.value if request.technique else None,
        )

        cached = await self._cache.get(cache_key)
        if cached is not None:
            logger.info(
                "EvaluationPanel: cache hit for document_id=%s (key=%.12s…)",
                request.document_id,
                cache_key,
            )
            result = EvaluationResult(**{**cached, "document_id": request.document_id, "cache_hit": True})
            return result

        start = time.perf_counter()

        vocab_result, structure_result, style_result = await asyncio.gather(
            self._vocab.analyse(request.text, request.language),
            self._structure.analyse(request.text, request.language, request.framework),
            self._style.analyse(request.text, request.language),
        )

        result = await self._judge.judge(
            request,
            vocab_result,
            structure_result,
            style_result,
            start_time=start,
        )

        await self._cache.set(cache_key, result.model_dump())
        return result
