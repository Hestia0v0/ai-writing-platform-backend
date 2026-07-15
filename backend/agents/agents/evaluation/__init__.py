"""
Evaluation Panel — LangGraph-orchestrated multi-agent essay evaluation.

Graph:

    START ─┬─> vocab ─────┐
           ├─> structure ─┼─> judge ─┬─(sub-scores agree)──> END
           └─> style ─────┘          └─(sub-scores disagree)─> arbiter ─> END

vocab / structure / style share no dependency on each other, so LangGraph
schedules all three in the same super-step — they run concurrently, keeping
total latency close to a single LLM call rather than the sum of three
(target: < 8 s, documented in routers/evaluate.py).

judge always runs once all three sub-agents have returned. The conditional
edge after judge computes the population stdev across the four rubric
sub-scores (vocab/structure/style/content, all 0-25); when it exceeds
ARBITRATION_STDEV_THRESHOLD the graph routes to a fifth LLM call
(ConsistencyArbiterAgent) that re-checks the total against the essay and
flags the result for human follow-up. Most essays never take this branch.

Caching (US-17):
  Before invoking the graph, EvalCacheService is checked for a previously
  computed result keyed on a SHA-256 hash of the essay + metadata. On a hit
  the cached result is returned immediately with cache_hit=True, skipping
  the graph (and its LLM cost) entirely.
"""
from __future__ import annotations

import logging
import time
from typing import Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from core.models import (
    EvaluationRequest,
    EvaluationResult,
    StructureLogicAnalysis,
    StyleAnalysis,
    VocabGrammarAnalysis,
)
from core.cache import EvalCacheService
from agents.evaluation.vocab_grammar import VocabGrammarAgent
from agents.evaluation.structure_logic import StructureLogicAgent
from agents.evaluation.style import StyleAgent
from agents.evaluation.master_judge import MasterJudgeAgent
from agents.evaluation.arbiter import ARBITRATION_STDEV_THRESHOLD, ConsistencyArbiterAgent

logger = logging.getLogger(__name__)


class _PanelState(TypedDict, total=False):
    request: EvaluationRequest
    start_time: float
    vocab: VocabGrammarAnalysis
    structure: StructureLogicAnalysis
    style: StyleAnalysis
    result: EvaluationResult


class EvaluationPanel:
    """
    Singleton-safe orchestrator. Instantiate once at startup.
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
        self._arbiter = ConsistencyArbiterAgent(api_key=api_key, model=model)
        self._cache = cache or EvalCacheService()
        self._graph = self._build_graph()

    # ── Graph construction ────────────────────────────────────────────────────

    def _build_graph(self):
        graph = StateGraph(_PanelState)

        async def vocab_node(state: _PanelState) -> dict:
            req = state["request"]
            return {"vocab": await self._vocab.analyse(req.text, req.language)}

        async def structure_node(state: _PanelState) -> dict:
            req = state["request"]
            return {
                "structure": await self._structure.analyse(req.text, req.language, req.framework)
            }

        async def style_node(state: _PanelState) -> dict:
            req = state["request"]
            return {"style": await self._style.analyse(req.text, req.language)}

        async def judge_node(state: _PanelState) -> dict:
            result = await self._judge.judge(
                state["request"],
                state["vocab"],
                state["structure"],
                state["style"],
                start_time=state["start_time"],
            )
            return {"result": result}

        async def arbiter_node(state: _PanelState) -> dict:
            result = await self._arbiter.arbitrate(state["request"].text, state["result"])
            return {"result": result}

        def route_after_judge(state: _PanelState) -> str:
            r = state["result"]
            scores = [
                r.vocab_grammar.raw_score,
                r.structure_logic.raw_score,
                r.style.raw_score,
                r.content_score,
            ]
            mean = sum(scores) / len(scores)
            stdev = (sum((s - mean) ** 2 for s in scores) / len(scores)) ** 0.5
            return "arbiter" if stdev > ARBITRATION_STDEV_THRESHOLD else "end"

        graph.add_node("vocab", vocab_node)
        graph.add_node("structure", structure_node)
        graph.add_node("style", style_node)
        graph.add_node("judge", judge_node)
        graph.add_node("arbiter", arbiter_node)

        graph.add_edge(START, "vocab")
        graph.add_edge(START, "structure")
        graph.add_edge(START, "style")
        graph.add_edge("vocab", "judge")
        graph.add_edge("structure", "judge")
        graph.add_edge("style", "judge")
        graph.add_conditional_edges("judge", route_after_judge, {"arbiter": "arbiter", "end": END})
        graph.add_edge("arbiter", END)

        return graph.compile()

    # ── Public API ─────────────────────────────────────────────────────────────

    async def evaluate(self, request: EvaluationRequest) -> EvaluationResult:
        """
        Full evaluation pipeline:
          Phase 0 — cache lookup (US-17)
          Phase 1 — vocab/structure/style run CONCURRENTLY (LangGraph super-step)
          Phase 2 — Master Judge aggregates into the final EvaluationResult
          Phase 3 — Consistency Arbiter, only when sub-scores disagree sharply
          Phase 4 — store result in cache
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
            return EvaluationResult(
                **{**cached, "document_id": request.document_id, "cache_hit": True}
            )

        start = time.perf_counter()
        final_state = await self._graph.ainvoke({"request": request, "start_time": start})
        result: EvaluationResult = final_state["result"]

        await self._cache.set(cache_key, result.model_dump())
        return result
