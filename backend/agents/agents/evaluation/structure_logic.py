"""
Structure & Logic Checker Sub-Agent (US-7, US-8)

Analyses:
  - Whether the essay has a clear introduction and conclusion
  - Whether intro and conclusion echo each other (circular closure)
  - Whether the essay stays on topic (no tangents or off-topic paragraphs)
  - Whether paragraph structure is logical and well-ordered
  - For Chinese 起承转合 framework: completeness of 起/承/转/合 four stages

Returns a StructureLogicAnalysis with a raw_score out of 25.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

from core.models import Language, StructureLogicAnalysis, WritingFramework
from agents.evaluation._base import (
    DEFAULT_EVAL_MODEL,
    build_async_client,
    parse_json_response,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_EN = """\
You are a writing structure analyst specialising in student essays (ages 10–18).

Analyse the essay STRICTLY for structural, logical, and coherence qualities:
1. Does it have a clear introduction that sets up the topic/thesis?
2. Does it have a clear conclusion that wraps up the essay?
3. Do the introduction and conclusion echo each other (circular closure)?
4. Does the essay remain on-topic throughout, or are there tangents?
5. Is the paragraph structure logical (each paragraph has a clear focus)?
6. COHERENCE: Do ideas flow smoothly from sentence to sentence and paragraph to paragraph?
   Are transitions explicit and logical? Does the argument or narrative build progressively?

List any structural or coherence issues you find.

Respond with ONLY a valid JSON object (no markdown, no explanation):
{
  "has_clear_intro": true | false,
  "has_clear_conclusion": true | false,
  "intro_conclusion_echo": true | false,
  "on_topic": true | false,
  "paragraph_structure_ok": true | false,
  "issues": ["<issue description>", ...],
  "raw_score": <float 0–25>,
  "coherence_score": <float 0–10>
}

Scoring guide for raw_score (out of 25):
  22–25: Excellent structure, clear flow, circular closure achieved.
  17–21: Good structure with minor gaps.
  11–16: Some structural issues (e.g. weak intro or conclusion).
  6–10:  Noticeable structural problems that confuse the reader.
  0–5:   No discernible structure.

Scoring guide for coherence_score (out of 10, informational — does NOT add to raw_score):
  9–10: Ideas connect seamlessly; transitions are smooth and varied.
  7–8:  Ideas connect well with occasional abrupt jumps.
  5–6:  Some transitions missing; reader must infer connections.
  3–4:  Frequent abrupt shifts; ideas feel disconnected.
  0–2:  No apparent logical thread between sentences or paragraphs.
"""

_SYSTEM_PROMPT_ZH = """\
你是一位专门分析10–18岁学生作文结构与逻辑的评改专家。

请从结构、逻辑及连贯性角度分析作文：
1. 是否有清晰的开头，引出主题或论点？
2. 是否有清晰的结尾，总结全文？
3. 开头与结尾是否首尾呼应？
4. 全文是否始终紧扣主题，没有跑题段落？
5. 段落结构是否合理，每段是否有明确中心句？
6. 连贯性（Coherence）：句子与句子之间、段落与段落之间，过渡是否自然流畅？
   逻辑推进是否清晰，读者能否顺畅地跟上作者的思路？

请列出所有结构性及连贯性问题。

请仅以有效的 JSON 对象回复（不加 Markdown 格式，不加任何解释）：
{
  "has_clear_intro": true | false,
  "has_clear_conclusion": true | false,
  "intro_conclusion_echo": true | false,
  "on_topic": true | false,
  "paragraph_structure_ok": true | false,
  "issues": ["<问题描述>", ...],
  "raw_score": <浮点数 0–25>,
  "coherence_score": <浮点数 0–10>
}

raw_score 评分标准（满分25）：
  22–25：结构优秀，逻辑清晰，首尾呼应。
  17–21：结构良好，有细小不足。
  11–16：存在部分结构问题（如开头或结尾薄弱）。
  6–10： 结构问题较明显，影响读者理解。
  0–5：  缺乏明显结构。

coherence_score 评分标准（满分10，仅供参考，不计入总分）：
  9–10：过渡自然流畅，逻辑层层递进，读者完全无需猜测。
  7–8： 大体连贯，偶有跳跃感。
  5–6： 部分缺乏过渡，读者需自行推断联系。
  3–4： 段落间跳跃明显，逻辑感弱。
  0–2： 各段之间缺乏明显逻辑线索。
"""

# Specialised prompt for Chinese 起承转合 framework (used when framework == qczh)
_SYSTEM_PROMPT_ZH_QCZH = """\
你是一位专门评改10–18岁学生「起承转合」结构作文的专家。

请严格按照起承转合四段式结构分析这篇作文，逐一检验：
1. 起（开篇）：是否有清晰的开篇？是否通过情景、意象或叙述自然引入主题，而非开篇即直白点题？
2. 承（承接）：是否有充分的展开？是否在开篇基础上深入描写、叙述或论证，且与主题保持一致性？
3. 转（转折）：是否有有效的转折？是否引入了对比视角、情感转变、出人意料的元素或思想升华？转折是否自然有力？
4. 合（收结）：是否有呼应开头的结尾？是否做到首尾圆合，留有余韵而非仓促收尾？
5. 连贯性（Coherence）：四段之间的过渡是否流畅？起承转合各段之间衔接是否自然？

对字段的映射说明：
  has_clear_intro         → 起：开篇是否清晰到位
  has_clear_conclusion    → 合：结尾是否呼应收束
  intro_conclusion_echo   → 首尾是否圆合呼应
  paragraph_structure_ok  → 起承转合四段是否均完整存在
  on_topic                → 全文是否紧扣主题

请列出所有结构性及连贯性问题。

请仅以有效的 JSON 对象回复（不加 Markdown 格式，不加任何解释）：
{
  "has_clear_intro": true | false,
  "has_clear_conclusion": true | false,
  "intro_conclusion_echo": true | false,
  "on_topic": true | false,
  "paragraph_structure_ok": true | false,
  "issues": ["<问题描述>", ...],
  "raw_score": <浮点数 0–25>,
  "coherence_score": <浮点数 0–10>
}

raw_score 评分标准（满分25）：
  22–25：起承转合四段完整，结构精妙，首尾圆合，转折有力。
  17–21：结构基本完整，某一段（尤其是"转"）略显薄弱。
  11–16：缺少明显的转折（转）或结尾呼应（合），结构较平。
  6–10： 起承转合结构意识不清晰，多段混乱或缺失。
  0–5：  几乎没有起承转合的结构意识。

coherence_score 评分标准（满分10，仅供参考，不计入总分）：
  9–10：起承转合四段衔接天衣无缝，过渡语言自然优美。
  7–8： 段落间衔接基本流畅，偶有生硬处。
  5–6： 部分段落衔接薄弱，过渡感突兀。
  3–4： 段落跳跃明显，缺乏有效的衔接词或句。
  0–2： 各段几乎无法自然衔接，读者难以跟上逻辑。
"""


class StructureLogicAgent:
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self._client = build_async_client(api_key)
        self._model = model or os.getenv("EVAL_MODEL", DEFAULT_EVAL_MODEL)

    async def analyse(
        self,
        text: str,
        language: Language,
        framework: Optional[WritingFramework] = None,
    ) -> StructureLogicAnalysis:
        if self._client is None:
            return self._mock()
        return await self._llm_analyse(text, language, framework)

    async def _llm_analyse(
        self,
        text: str,
        language: Language,
        framework: Optional[WritingFramework] = None,
    ) -> StructureLogicAnalysis:
        if language == Language.CHINESE and framework == WritingFramework.QI_CHENG_ZHUAN_HE:
            system = _SYSTEM_PROMPT_ZH_QCZH
        elif language == Language.CHINESE:
            system = _SYSTEM_PROMPT_ZH
        else:
            system = _SYSTEM_PROMPT_EN
        try:
            resp = await self._client.chat.completions.create(
                model=self._model,
                max_tokens=512,
                temperature=0.0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": f"Essay to analyse:\n\n{text[:6000]}"},
                ],
            )
            data = parse_json_response(resp.choices[0].message.content or "{}")
            return StructureLogicAnalysis(
                has_clear_intro=bool(data.get("has_clear_intro", True)),
                has_clear_conclusion=bool(data.get("has_clear_conclusion", True)),
                intro_conclusion_echo=bool(data.get("intro_conclusion_echo", False)),
                on_topic=bool(data.get("on_topic", True)),
                paragraph_structure_ok=bool(data.get("paragraph_structure_ok", True)),
                issues=data.get("issues", []),
                raw_score=float(data.get("raw_score", 15.0)),
                coherence_score=float(data.get("coherence_score", 6.0)),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("StructureLogicAgent LLM call failed: %s", exc)
            return self._mock()

    @staticmethod
    def _mock() -> StructureLogicAnalysis:
        return StructureLogicAnalysis(
            has_clear_intro=True,
            has_clear_conclusion=True,
            intro_conclusion_echo=False,
            on_topic=True,
            paragraph_structure_ok=True,
            issues=["[Mock] Structure analysis unavailable — no API key configured."],
            raw_score=18.0,
            coherence_score=6.0,
        )
