"""
Dependency injection — all agents are instantiated once (singleton via lru_cache)
and injected into router handlers via FastAPI's Depends().
"""
from __future__ import annotations

import os
from functools import lru_cache

from agents.drafting import DraftingAgent
from agents.evaluation import EvaluationPanel
from agents.guardrail import GuardrailAgent
from agents.knowledge_rag import KnowledgeRAGAgent
from agents.refinement import RefinementAgent
from core.cache import EvalCacheService


@lru_cache(maxsize=1)
def get_guardrail() -> GuardrailAgent:
    return GuardrailAgent(
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        model=os.getenv("GUARDRAIL_MODEL"),
    )


@lru_cache(maxsize=1)
def get_drafting() -> DraftingAgent:
    return DraftingAgent(
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        model=os.getenv("DRAFTING_MODEL"),
    )


@lru_cache(maxsize=1)
def get_eval_cache() -> EvalCacheService:
    return EvalCacheService(redis_url=os.getenv("REDIS_URL"))


@lru_cache(maxsize=1)
def get_evaluation_panel() -> EvaluationPanel:
    return EvaluationPanel(
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        model=os.getenv("EVAL_MODEL"),
        cache=get_eval_cache(),
    )


@lru_cache(maxsize=1)
def get_refinement() -> RefinementAgent:
    return RefinementAgent(
        api_key=os.getenv("DEEPSEEK_API_KEY"),
        model=os.getenv("REFINEMENT_MODEL"),
    )


@lru_cache(maxsize=1)
def get_knowledge_rag() -> KnowledgeRAGAgent:
    return KnowledgeRAGAgent(
        retrieval_url=os.getenv("KNOWLEDGE_RETRIEVAL_URL"),
    )
