"""
Vocabulary & Grammar Checker Sub-Agent (US-7)

Detects:
  - Typos and spelling errors
  - Grammar / syntax issues (subject-verb agreement, tense, punctuation)
  - Vocabulary richness (range and sophistication of word choice)

Returns a VocabGrammarAnalysis with a raw_score out of 25.
"""
from __future__ import annotations

import logging
import os

from core.models import Language, VocabGrammarAnalysis
from agents.evaluation._base import (
    DEFAULT_EVAL_MODEL,
    build_async_client,
    parse_json_response,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_EN = """\
You are a meticulous English language editor specialising in student essays (ages 10–18).

Your ONLY job is to analyse the essay for:
1. Spelling errors and typos — list each one with the correct form.
2. Grammar errors — subject-verb agreement, wrong tense, missing articles, \
   comma splices, run-on sentences, sentence fragments.
3. Vocabulary richness — assess whether the student uses varied, age-appropriate \
   academic vocabulary or mostly basic/repetitive words.

Respond with ONLY a valid JSON object (no markdown, no explanation):
{
  "error_count": <integer>,
  "errors": [
    {"sentence": "<exact sentence with the error>", "issue": "<description>", "suggestion": "<corrected form>"}
  ],
  "vocabulary_richness": "low" | "medium" | "high",
  "vocabulary_notes": "<one or two sentences of overall vocabulary assessment>",
  "raw_score": <float 0–25>
}

Scoring guide for raw_score (out of 25):
  22–25: Near-perfect grammar, rich vocabulary.
  17–21: 1–3 minor errors, decent vocabulary.
  11–16: Several errors or repetitive word choice.
  6–10:  Frequent errors that impede clarity.
  0–5:   Pervasive errors throughout.
"""

_SYSTEM_PROMPT_ZH = """\
你是一位严格的中文语言编辑，专门评改10–18岁学生的作文。

你的唯一职责是分析以下作文中：
1. 错别字和笔误 — 列出每一处并给出正确写法。
2. 语法错误 — 主谓不一致、时态混乱、成分缺失、标点滥用、病句等。
3. 词汇丰富度 — 评估学生是否使用多样、恰当的词汇，还是大量重复使用简单词汇。

请仅以有效的 JSON 对象回复（不加 Markdown 格式，不加任何解释）：
{
  "error_count": <整数>,
  "errors": [
    {"sentence": "<含错误的原句>", "issue": "<问题描述>", "suggestion": "<修正后的形式>"}
  ],
  "vocabulary_richness": "low" | "medium" | "high",
  "vocabulary_notes": "<一到两句对整体词汇水平的评价>",
  "raw_score": <浮点数 0–25>
}

raw_score 评分标准（满分25）：
  22–25：语法近乎完美，词汇丰富多样。
  17–21：1–3处细小错误，词汇尚可。
  11–16：数处错误或用词重复单调。
  6–10： 频繁错误影响表达清晰度。
  0–5：  全文充斥错误。
"""


class VocabGrammarAgent:
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self._client = build_async_client(api_key)
        self._model = model or os.getenv("EVAL_MODEL", DEFAULT_EVAL_MODEL)

    async def analyse(self, text: str, language: Language) -> VocabGrammarAnalysis:
        if self._client is None:
            return self._mock(text)
        return await self._llm_analyse(text, language)

    async def _llm_analyse(self, text: str, language: Language) -> VocabGrammarAnalysis:
        system = _SYSTEM_PROMPT_ZH if language == Language.CHINESE else _SYSTEM_PROMPT_EN
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                max_tokens=1024,
                temperature=0.0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Essay to analyse:\n\n{text[:6000]}"},
                ],
            )
            data = parse_json_response(resp.choices[0].message.content or "{}")
            return VocabGrammarAnalysis(
                error_count=int(data.get("error_count", 0)),
                errors=data.get("errors", []),
                vocabulary_richness=data.get("vocabulary_richness", "medium"),
                vocabulary_notes=data.get("vocabulary_notes", ""),
                raw_score=float(data.get("raw_score", 15.0)),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("VocabGrammarAgent LLM call failed: %s", exc)
            return self._mock(text)

    @staticmethod
    def _mock(text: str) -> VocabGrammarAnalysis:
        word_count = len(text.split())
        return VocabGrammarAnalysis(
            error_count=0,
            errors=[],
            vocabulary_richness="medium",
            vocabulary_notes="[Mock] Vocabulary analysis unavailable — no API key configured.",
            raw_score=18.0 if word_count > 100 else 12.0,
        )
