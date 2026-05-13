"""
Style & Show-Don't-Tell Detector Sub-Agent (US-9)

Hunts for "tell" sentences — flat, bare statements of emotion or scene
("I was happy", "It was dark") — and flags them with a suggested rewrite
that uses sensory detail, action, or imagery instead.

Returns a StyleAnalysis with a raw_score out of 25.
"""
from __future__ import annotations

import logging
import os

from core.models import Language, StyleAnalysis
from agents.evaluation._base import (
    DEFAULT_EVAL_MODEL,
    build_async_client,
    parse_json_response,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_EN = """\
You are an expert creative writing coach specialising in the Show-Don't-Tell technique \
and emotion expression analysis.

Your tasks:

TASK 1 — TELL SENTENCE DETECTION:
Find every sentence in the essay that "tells" rather than "shows". Classify each by type:
  • "direct_emotion"  — States an emotion directly: "She was sad", "I felt scared".
  • "bare_adjective"  — Bare scene description: "It was dark", "The food was delicious".
  • "adverb_verb"     — Adverb + weak verb: "He ran quickly", "She spoke angrily".

For each tell sentence provide a "show" rewrite using sensory detail, physical reaction, \
action/dialogue, or concrete imagery.

TASK 2 — EMOTION PATTERN CLASSIFICATION:
Identify the emotional words/phrases that are told rather than shown. For each, record:
  • The exact phrase (e.g. "was terrified")
  • The emotion it names (e.g. "fear")
  • Its tell_type (direct_emotion | bare_adjective | adverb_verb)
  • A suggested descriptive replacement

Respond with ONLY a valid JSON object (no markdown, no explanation):
{
  "tell_count": <integer>,
  "tell_sentences": [
    {
      "original": "<exact tell sentence>",
      "suggestion": "<show rewrite>",
      "tell_type": "direct_emotion" | "bare_adjective" | "adverb_verb"
    }
  ],
  "tell_type_counts": {
    "direct_emotion": <integer>,
    "bare_adjective": <integer>,
    "adverb_verb": <integer>
  },
  "emotion_patterns": [
    {
      "phrase": "<exact emotion phrase>",
      "emotion": "<emotion label, e.g. sadness | anger | joy | fear | surprise>",
      "tell_type": "direct_emotion" | "bare_adjective" | "adverb_verb",
      "suggestion": "<descriptive replacement phrase or sentence>"
    }
  ],
  "descriptive_quality": "weak" | "adequate" | "strong",
  "raw_score": <float 0–25>
}

Scoring guide for raw_score (out of 25):
  22–25: Predominantly shows, rare tells, vivid sensory writing.
  17–21: Mostly shows with a few tell sentences.
  11–16: Mix of show and tell; room for improvement.
  6–10:  Mostly tell sentences, lacks vivid description.
  0–5:   Almost entirely bare, flat telling with no imagery.
"""

_SYSTEM_PROMPT_ZH = """\
你是一位专门指导"Show-Don't-Tell（写而不说）"写作技巧和情感表达分析的创意写作教练。

你的任务：

任务1 — 直白叙述（tell）句子检测：
找出作文中所有"直白叙述（tell）"的句子，并按以下类型分类：
  • "direct_emotion"  — 直接陈述情绪：「她很悲伤」「我感到害怕」
  • "bare_adjective"  — 干瘪形容词描写：「天很黑」「食物很好吃」
  • "adverb_verb"     — 副词+普通动词：「他跑得很快」「她生气地说」

对每一个"tell"句子，提供一个"show"式的改写，使用感官细节、身体反应、动作/对话或具体意象。

任务2 — 情感表达模式分类（情感深度评估）：
识别作文中所有以直白方式陈述而非通过细节呈现的情感词语/短语。对每个，记录：
  • 原文短语（如「十分害怕」）
  • 所表达的情感（如「恐惧」）
  • 其 tell_type（direct_emotion | bare_adjective | adverb_verb）
  • 建议的描写性替换

请仅以有效的 JSON 对象回复（不加 Markdown 格式，不加任何解释）：
{
  "tell_count": <整数>,
  "tell_sentences": [
    {
      "original": "<原文tell句子>",
      "suggestion": "<show式改写>",
      "tell_type": "direct_emotion" | "bare_adjective" | "adverb_verb"
    }
  ],
  "tell_type_counts": {
    "direct_emotion": <整数>,
    "bare_adjective": <整数>,
    "adverb_verb": <整数>
  },
  "emotion_patterns": [
    {
      "phrase": "<原文情感短语>",
      "emotion": "<情感标签，如 悲伤 | 愤怒 | 喜悦 | 恐惧 | 惊讶>",
      "tell_type": "direct_emotion" | "bare_adjective" | "adverb_verb",
      "suggestion": "<描写性替换短语或句子>"
    }
  ],
  "descriptive_quality": "weak" | "adequate" | "strong",
  "raw_score": <浮点数 0–25>
}

raw_score 评分标准（满分25）：
  22–25：以"show"为主，极少"tell"，感官描写生动。
  17–21：以"show"为主，少量"tell"句子。
  11–16："show"与"tell"混合，有较大改进空间。
  6–10： 以"tell"为主，描写缺乏生动性。
  0–5：  几乎全为干瘪叙述，无任何意象。
"""


class StyleAgent:
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self._client = build_async_client(api_key)
        self._model = model or os.getenv("EVAL_MODEL", DEFAULT_EVAL_MODEL)

    async def analyse(self, text: str, language: Language) -> StyleAnalysis:
        if self._client is None:
            return self._mock()
        return await self._llm_analyse(text, language)

    async def _llm_analyse(self, text: str, language: Language) -> StyleAnalysis:
        system = _SYSTEM_PROMPT_ZH if language == Language.CHINESE else _SYSTEM_PROMPT_EN
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                max_tokens=1500,
                temperature=0.0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Essay to analyse:\n\n{text[:6000]}"},
                ],
            )
            data = parse_json_response(resp.choices[0].message.content or "{}")

            # Normalise tell_type_counts — fill missing keys with 0
            raw_counts: dict = data.get("tell_type_counts", {})
            tell_type_counts = {
                "direct_emotion": int(raw_counts.get("direct_emotion", 0)),
                "bare_adjective": int(raw_counts.get("bare_adjective", 0)),
                "adverb_verb": int(raw_counts.get("adverb_verb", 0)),
            }

            return StyleAnalysis(
                tell_count=int(data.get("tell_count", 0)),
                tell_sentences=data.get("tell_sentences", []),
                descriptive_quality=data.get("descriptive_quality", "adequate"),
                raw_score=float(data.get("raw_score", 15.0)),
                tell_type_counts=tell_type_counts,
                emotion_patterns=data.get("emotion_patterns", []),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("StyleAgent LLM call failed: %s", exc)
            return self._mock()

    @staticmethod
    def _mock() -> StyleAnalysis:
        return StyleAnalysis(
            tell_count=0,
            tell_sentences=[],
            descriptive_quality="adequate",
            raw_score=15.0,
            tell_type_counts={"direct_emotion": 0, "bare_adjective": 0, "adverb_verb": 0},
            emotion_patterns=[],
        )
