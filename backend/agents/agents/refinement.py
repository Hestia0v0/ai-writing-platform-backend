"""
Refinement & Polishing Agent (US-13)

Role: an essay-tutoring assistant, NOT a ghostwriter.

Core constraints:
  - NEVER rewrite the essay from scratch.
  - Preserve the student's voice, tone, and story direction.
  - Fix grammar errors and upgrade weak vocabulary.
  - Expand "tell" sentences into "show" passages.
  - Return the polished text AND a sentence-level diff so the student
    can see exactly what changed and why.
"""
from __future__ import annotations

import json
import logging
import os
import re

from core.models import DiffHunk, ImprovementSummary, Language, RefinementRequest, RefinementResult  # noqa: E501
from agents.evaluation._base import build_async_client

logger = logging.getLogger(__name__)

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"

_SYSTEM_PROMPT_EN = """\
You are a writing tutor helping a student aged 10–18 improve their essay.

ABSOLUTE RULES (never break these):
1. Do NOT rewrite the essay from scratch.
2. Preserve the student's voice, perspective, narrative arc, and overall meaning.
3. Keep the student's structural choices (paragraph order, topic choices).

You MUST address these three improvement areas explicitly:

GRAMMAR (category: "grammar")
  • Fix every spelling error, typo, and punctuation mistake.
  • Correct grammatical errors: subject-verb agreement, wrong tense, missing articles,
    run-on sentences, sentence fragments.

FLOW (category: "flow")
  • Smooth out awkward phrasing — keep the original idea, just make it read better.
  • Add or improve transitions between sentences and paragraphs where they are abrupt.
  • Vary sentence length: break monotonous same-length sentences.

DESCRIPTION QUALITY (category: "description")
  • Convert flat "tell" sentences into vivid "show" passages:
    - Use sensory detail (sight, sound, smell, touch, taste).
    - Replace bare emotion statements with physical reactions or actions.
    - Swap adverb + weak verb for a precise, vivid verb.

VOCABULARY (category: "vocabulary")
  • Replace one or two repeated or basic words with more precise/advanced alternatives.

Output a single valid JSON object (no markdown, no preamble):
{
  "refined_text": "<full polished essay as a single string>",
  "diff_hunks": [
    {
      "original": "<exact original sentence or phrase>",
      "revised": "<revised version>",
      "reason": "<brief explanation>",
      "category": "grammar" | "flow" | "description" | "vocabulary" | "other"
    }
  ],
  "improvement_summary": {
    "grammar_fixes": <integer count of grammar/spelling corrections>,
    "flow_improvements": <integer count of flow/phrasing improvements>,
    "description_upgrades": <integer count of tell→show upgrades>,
    "vocabulary_upgrades": <integer count of vocabulary replacements>,
    "overall_notes": "<one sentence summarising the main improvements made>"
  }
}

Only include hunks that were actually changed. Unchanged sentences must NOT appear.
"""

_SYSTEM_PROMPT_ZH = """\
你是一位辅导10–18岁学生的作文老师，帮助学生改进作文。

绝对不允许违反的规则：
1. 不得推翻原文重写。
2. 必须保留学生的写作风格、叙述视角、故事走向和整体含义。
3. 保持学生的结构选择（段落顺序、主题选择）。

你必须明确针对以下三个改进方向进行修改：

语法（category: "grammar"）
  • 改正所有错别字、笔误和标点错误。
  • 修正语法错误：主谓不一致、时态混乱、成分缺失、标点滥用、病句等。

行文流畅度（category: "flow"）
  • 润色表述不通顺的地方，保持原意的同时使句子更流畅自然。
  • 在段落或句子之间添加或改进过渡语，避免生硬跳跃。
  • 变化句子长度，打破单调的同长度句型。

描写质量（category: "description"）
  • 将干瘪的"直白叙述（tell）"句子改写为生动的"细节呈现（show）"：
    - 使用感官细节（视觉、听觉、嗅觉、触觉、味觉）。
    - 用具体的动作或身体反应替代情绪的直白陈述。
    - 用精准生动的动词替代"副词+普通动词"的组合。

词汇（category: "vocabulary"）
  • 将一两处重复或过于简单的词汇替换为更精准、更高级的表达。

仅返回一个有效的 JSON 对象（不加 Markdown，不加前言）：
{
  "refined_text": "<完整润色后的作文，整段文字>",
  "diff_hunks": [
    {
      "original": "<被修改的原句或原词组>",
      "revised": "<修改后的版本>",
      "reason": "<简短说明修改原因>",
      "category": "grammar" | "flow" | "description" | "vocabulary" | "other"
    }
  ],
  "improvement_summary": {
    "grammar_fixes": <语法/拼写修正数量>,
    "flow_improvements": <行文流畅度改进数量>,
    "description_upgrades": <描写质量升级数量（tell→show）>,
    "vocabulary_upgrades": <词汇替换数量>,
    "overall_notes": "<一句话总结主要改进内容>"
  }
}

diff_hunks 中只包含实际被修改的内容。未修改的句子不得列入。
"""


class RefinementAgent:
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self._client = build_async_client(api_key)
        self._model = model or os.getenv("REFINEMENT_MODEL", DEFAULT_MODEL)

    async def refine(self, request: RefinementRequest) -> RefinementResult:
        if self._client is None:
            return self._mock(request)
        return await self._llm_refine(request)

    async def _llm_refine(self, request: RefinementRequest) -> RefinementResult:
        system = _SYSTEM_PROMPT_ZH if request.language == Language.CHINESE else _SYSTEM_PROMPT_EN
        model = request.model or self._model

        feedback_block = ""
        if request.weaknesses:
            feedback_block += "\n=== WEAKNESSES IDENTIFIED BY EVALUATOR ===\n"
            feedback_block += "\n".join(f"- {w}" for w in request.weaknesses)
        if request.suggestions:
            feedback_block += "\n\n=== SUGGESTED IMPROVEMENTS ===\n"
            feedback_block += "\n".join(f"- {s}" for s in request.suggestions)

        user_content = (
            f"Please refine this essay:{feedback_block}\n\n"
            f"=== ORIGINAL ESSAY ===\n{request.original_text}"
        )

        try:
            resp = await self._client.chat.completions.create(
                model=model,
                max_tokens=4096,
                temperature=0.4,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content[:8000]},
                ],
            )
            raw = resp.choices[0].message.content or "{}"
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
            data = json.loads(raw)

            diff_hunks = [
                DiffHunk(
                    original=h.get("original", ""),
                    revised=h.get("revised", ""),
                    reason=h.get("reason", ""),
                    category=h.get("category", "other"),
                )
                for h in data.get("diff_hunks", [])
                if h.get("original") and h.get("revised")
            ]

            raw_summary = data.get("improvement_summary", {})
            improvement_summary = ImprovementSummary(
                grammar_fixes=int(raw_summary.get("grammar_fixes", 0)),
                flow_improvements=int(raw_summary.get("flow_improvements", 0)),
                description_upgrades=int(raw_summary.get("description_upgrades", 0)),
                vocabulary_upgrades=int(raw_summary.get("vocabulary_upgrades", 0)),
                overall_notes=str(raw_summary.get("overall_notes", "")),
            )

            return RefinementResult(
                document_id=request.document_id,
                original_text=request.original_text,
                refined_text=data.get("refined_text", request.original_text),
                diff_hunks=diff_hunks,
                improvement_summary=improvement_summary,
                model_used=model,
                tokens_used=(
                    resp.usage.prompt_tokens + resp.usage.completion_tokens
                    if resp.usage else 0
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("RefinementAgent LLM call failed: %s", exc)
            return self._mock(request)

    @staticmethod
    def _mock(request: RefinementRequest) -> RefinementResult:
        return RefinementResult(
            document_id=request.document_id,
            original_text=request.original_text,
            refined_text=(
                request.original_text
                + "\n\n[Mock] Configure DEEPSEEK_API_KEY to enable real refinement."
            ),
            diff_hunks=[],
            improvement_summary=ImprovementSummary(
                grammar_fixes=0,
                flow_improvements=0,
                description_upgrades=0,
                vocabulary_upgrades=0,
                overall_notes="[Mock] No improvements applied — API key not configured.",
            ),
            model_used="mock",
            tokens_used=0,
        )
