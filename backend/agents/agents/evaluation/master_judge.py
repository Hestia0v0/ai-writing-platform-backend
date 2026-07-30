"""
Master Judge Agent — aggregates sub-agent results and produces the final verdict.

Inputs:
  - Essay text
  - VocabGrammarAnalysis
  - StructureLogicAnalysis
  - StyleAnalysis

Output: EvaluationResult (total_score, grade, strengths, weaknesses, evidence, suggestions)

The fourth dimension (Content / Ideas, worth 25 points) is assessed by this agent
directly, so the total is always out of 100.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

from pydantic import BaseModel, Field

from core.models import (
    EvaluationRequest,
    EvaluationResult,
    EvidencePosition,
    Language,
    StructureLogicAnalysis,
    StyleAnalysis,
    VocabGrammarAnalysis,
)
from agents.evaluation._base import DEFAULT_EVAL_MODEL, build_structured_chain
from agents.evaluation.rubric_client import get_content_max_score

logger = logging.getLogger(__name__)


class _MasterJudgeOutput(BaseModel):
    """
    Structured-output schema for the Master Judge's LLM call.

    content_score's upper bound is intentionally generous (100, not the
    "25" the prompt asks for) because the prompt's actual ceiling is the
    admin-configured content weight (see get_content_max_score) — a fixed
    Pydantic Field bound can't vary per-request, so the real bound is
    enforced by clamping in _llm_judge()/_mock_verdict() instead.
    """
    content_score: float = Field(default=20.0, ge=0.0, le=100.0)
    creativity_score: float = Field(default=5.0, ge=0.0, le=10.0)
    chinese_dimensions: Optional[dict[str, float]] = None
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


def _system_prompt_en(content_max: float) -> str:
    # The 22-25/17-21/... bands below are written for the historical 25-point
    # scale; when an admin reconfigures the content weight, we tell the model
    # to scale them proportionally rather than re-deriving every band number
    # per-request (the other three sub-agents' 25-point scales are unchanged).
    scale_note = (
        "" if content_max == 25
        else f"\nNote: the point range above is {content_max:g}, not the 25 used in the "
             f"bands below — scale each band proportionally (e.g. a band written as "
             f"\"22–25\" becomes the top {25 - 22}/25 of {content_max:g}).\n"
    )
    return f"""\
You are the Master Judge of an AI writing evaluation panel for students aged 10–18.

You have already received detailed reports from three specialist sub-agents:
  1. Vocabulary & Grammar Agent
  2. Structure & Logic Agent (includes coherence sub-score)
  3. Style / Show-Don't-Tell Agent (includes emotion pattern analysis)

Your job is to:
1. Assess the **Content & Ideas** dimension (0–{content_max:g} points):
   - Idea development: originality, depth, relevance to the topic
   - Quality of arguments or narrative arc
   - Idea coherence with the essay's structure
2. Assess **Creativity** (0–10 sub-score, informational only — does NOT add to total):
   - Originality of ideas, unexpected angles, imaginative language
3. Generate 2–3 specific **strengths** (things the student did well; quote evidence).
4. Generate 2–3 specific **weaknesses** (areas to improve; quote evidence from the text).
5. Extract 2–3 **evidence** sentences — copy them VERBATIM, character-for-character from the essay.
6. Write 3–5 concrete **suggestions** the student can act on immediately.

Respond with ONLY a valid JSON object (no markdown, no explanation):
{{
  "content_score": <float 0–{content_max:g}>,
  "creativity_score": <float 0–10>,
  "strengths": ["<strength with evidence quote>", ...],
  "weaknesses": ["<weakness with evidence quote>", ...],
  "evidence": ["<verbatim sentence from essay>", ...],
  "suggestions": ["<actionable suggestion>", ...]
}}
{scale_note}
Content & Ideas scoring guide (out of 25):
  22–25: Original, insightful ideas; well-developed argument or narrative; fully on-topic.
  17–21: Clear ideas with adequate development; mostly relevant.
  11–16: Some ideas but underdeveloped; or partially off-topic.
  6–10:  Minimal idea development; largely irrelevant or generic.
  0–5:   No discernible ideas or argument.

Creativity sub-score (out of 10):
  9–10: Genuinely surprising, imaginative approach; memorable phrasing.
  7–8:  Some creative touches; personal voice evident.
  5–6:  Mostly predictable; some moments of originality.
  3–4:  Generic and formulaic throughout.
  0–2:  No creative effort detectable.
"""


def _system_prompt_zh(content_max: float) -> str:
    scale_note = (
        "" if content_max == 25
        else f"\n注意：上面给出的满分是{content_max:g}分，不是下方评分标准使用的25分——请将各档位按比例换算"
             f"（例如\"22–25\"档位换算为{content_max:g}分制的最高 3/25 区间）。\n"
    )
    return f"""\
你是一个面向10–18岁学生的AI作文评分委员会的主审裁判。

你已经收到来自三位专项评审助理的详细报告：
  1. 词汇与语法评审
  2. 结构与逻辑评审（含连贯性子分）
  3. 风格与描写技巧评审（含情感表达模式分析）

你的职责是：
1. 评估**内容与思想**维度（0–{content_max:g}分）：
   - 思想发展：创意性、思想深度、是否切题
   - 论点或叙述质量
   - 思想与作文结构的契合度
2. 评估**创意性**（0–10分，仅供参考，不计入总分）：
   - 立意是否新颖独特，有无出人意料的视角，语言是否富有想象力
3. 评估以下**中文作文专项维度**（各 0–10 分，仅供参考，不计入总分）：
   - 字词运用（character_expression）：用词是否准确、丰富、精练？是否善用四字成语或典故？
   - 情感深度（emotional_depth）：情感表达是否真实深刻？是否通过细节与情境传递出真情实感？
   - 描写质量（description_quality）：景物、人物、场景的描写是否生动细腻、有画面感？
4. 列出2–3条具体**优点**（学生做得好的地方，并引用原文证据）。
5. 列出2–3条具体**不足**（需改进之处，并附原文证据）。
6. 从作文中逐字引用2–3句**证据**原句（必须与原文完全一致，不得改动任何字）。
7. 写出3–5条学生可以立刻付诸行动的具体**改进建议**。

请仅以有效的 JSON 对象回复（不加 Markdown 格式，不加任何解释）：
{{
  "content_score": <浮点数 0–{content_max:g}>,
  "creativity_score": <浮点数 0–10>,
  "chinese_dimensions": {{
    "字词运用": <浮点数 0–10>,
    "情感深度": <浮点数 0–10>,
    "描写质量": <浮点数 0–10>
  }},
  "strengths": ["<优点及原文引用>", ...],
  "weaknesses": ["<不足及原文引用>", ...],
  "evidence": ["<作文原句，必须逐字照抄>", ...],
  "suggestions": ["<可操作的建议>", ...]
}}
{scale_note}
内容与思想评分标准（满分25）：
  22–25：立意新颖，思想深刻，论点或叙述充分展开，完全切题。
  17–21：思想清晰，展开较充分，基本切题。
  11–16：有一定思想但展开不足，或部分跑题。
  6–10： 思想贫乏，内容单薄或大量跑题。
  0–5：  几乎无思想内容或论点。
"""

_GRADE_THRESHOLDS = [
    (90, "A+"), (85, "A"), (80, "A-"),
    (75, "B+"), (70, "B"), (65, "B-"),
    (60, "C+"), (55, "C"), (50, "C-"),
    (40, "D"), (0, "F"),
]


def _score_to_grade(score: float) -> str:
    for threshold, grade in _GRADE_THRESHOLDS:
        if score >= threshold:
            return grade
    return "F"


def _find_evidence_positions(text: str, evidence: list[str]) -> list[EvidencePosition]:
    """
    Locate each evidence quote inside *text* and return EvidencePosition objects.
    Uses a simple linear scan; falls back gracefully when a quote is not found
    (e.g. the LLM paraphrased rather than quoting verbatim).
    """
    positions: list[EvidencePosition] = []
    search_from = 0
    for quote in evidence:
        if not quote:
            continue
        idx = text.find(quote, search_from)
        if idx == -1:
            # Try case-insensitive from the beginning
            lower_text = text.lower()
            lower_quote = quote.lower()
            idx = lower_text.find(lower_quote)
        if idx != -1:
            positions.append(
                EvidencePosition(text=quote, start=idx, end=idx + len(quote))
            )
            search_from = idx + len(quote)
        else:
            # Quote not found verbatim — record with sentinel offsets
            positions.append(EvidencePosition(text=quote, start=-1, end=-1))
    return positions


class MasterJudgeAgent:
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self._model = model or os.getenv("EVAL_MODEL", DEFAULT_EVAL_MODEL)
        self._chain = build_structured_chain(
            _MasterJudgeOutput, model=self._model, api_key=api_key, temperature=0.3,
        )

    async def judge(
        self,
        request: EvaluationRequest,
        vocab: VocabGrammarAnalysis,
        structure: StructureLogicAnalysis,
        style: StyleAnalysis,
        start_time: float,
    ) -> EvaluationResult:
        if self._chain is None:
            return await self._mock_verdict(request, vocab, structure, style, start_time)
        return await self._llm_judge(request, vocab, structure, style, start_time)

    async def _llm_judge(
        self,
        request: EvaluationRequest,
        vocab: VocabGrammarAnalysis,
        structure: StructureLogicAnalysis,
        style: StyleAnalysis,
        start_time: float,
    ) -> EvaluationResult:
        from langchain_core.messages import HumanMessage, SystemMessage  # noqa: PLC0415

        content_max = await get_content_max_score()
        system = (
            _system_prompt_zh(content_max) if request.language == Language.CHINESE
            else _system_prompt_en(content_max)
        )
        sub_report = self._build_sub_report(vocab, structure, style)
        try:
            output: _MasterJudgeOutput = await self._chain.ainvoke([
                SystemMessage(content=system),
                HumanMessage(
                    content=(
                        f"=== SUB-AGENT REPORTS ===\n{sub_report}\n\n"
                        f"=== ESSAY TEXT ===\n{request.text[:4000]}"
                    ),
                ),
            ])
        except Exception as exc:  # noqa: BLE001
            logger.error("MasterJudgeAgent LLM call failed: %s", exc)
            output = _MasterJudgeOutput(content_score=min(20.0, content_max))

        # Clamp defensively — the LLM is instructed with content_max but
        # Pydantic's Field bound is intentionally generous (see _MasterJudgeOutput).
        content_score = max(0.0, min(output.content_score, content_max))

        # vocab/structure/style stay on their fixed 25-point scales (see module
        # docstring for why full multi-agent reweighting is out of scope);
        # normalize the total proportionally so a non-default content_max
        # still always yields a 0-100 total_score.
        raw_total = vocab.raw_score + structure.raw_score + style.raw_score + content_score
        max_possible = 75.0 + content_max
        total = round(raw_total / max_possible * 100.0, 1) if max_possible else 0.0
        total = max(0.0, min(100.0, total))
        latency = int((time.perf_counter() - start_time) * 1000)

        evidence_positions = _find_evidence_positions(request.text, output.evidence)

        # Chinese-specific dimensions — only meaningful for ZH essays
        chinese_dimensions = (
            output.chinese_dimensions if request.language == Language.CHINESE else None
        )

        return EvaluationResult(
            document_id=request.document_id,
            total_score=total,
            grade=_score_to_grade(total),
            vocab_grammar=vocab,
            structure_logic=structure,
            style=style,
            content_score=round(content_score, 1),
            creativity_score=output.creativity_score,
            chinese_dimensions=chinese_dimensions,
            strengths=output.strengths or ["Good effort overall."],
            weaknesses=output.weaknesses or ["See sub-agent reports for details."],
            evidence=output.evidence,
            evidence_positions=evidence_positions,
            suggestions=output.suggestions or ["Review grammar and add more sensory detail."],
            model_used=self._model,
            latency_ms=latency,
        )

    @staticmethod
    def _build_sub_report(
        vocab: VocabGrammarAnalysis,
        structure: StructureLogicAnalysis,
        style: StyleAnalysis,
    ) -> str:
        return (
            f"[Vocab & Grammar] errors={vocab.error_count}, "
            f"richness={vocab.vocabulary_richness}, score={vocab.raw_score}/25\n"
            f"  Notes: {vocab.vocabulary_notes}\n\n"
            f"[Structure & Logic] intro={structure.has_clear_intro}, "
            f"conclusion={structure.has_clear_conclusion}, "
            f"echo={structure.intro_conclusion_echo}, on_topic={structure.on_topic}, "
            f"score={structure.raw_score}/25\n"
            f"  Issues: {'; '.join(structure.issues) or 'none'}\n\n"
            f"[Style / Show-Don't-Tell] tell_count={style.tell_count}, "
            f"quality={style.descriptive_quality}, score={style.raw_score}/25"
        )

    async def _mock_verdict(
        self,
        request: EvaluationRequest,
        vocab: VocabGrammarAnalysis,
        structure: StructureLogicAnalysis,
        style: StyleAnalysis,
        start_time: float,
    ) -> EvaluationResult:
        content_max = await get_content_max_score()
        content_score = min(20.0, content_max)
        raw_total = vocab.raw_score + structure.raw_score + style.raw_score + content_score
        max_possible = 75.0 + content_max
        total = round(raw_total / max_possible * 100.0, 1) if max_possible else 0.0
        total = max(0.0, min(100.0, total))
        chinese_dimensions = (
            {"字词运用": 7.0, "情感深度": 6.0, "描写质量": 7.0}
            if request.language == Language.CHINESE
            else None
        )
        return EvaluationResult(
            document_id=request.document_id,
            total_score=total,
            grade=_score_to_grade(total),
            vocab_grammar=vocab,
            structure_logic=structure,
            style=style,
            content_score=content_score,
            creativity_score=5.0,
            chinese_dimensions=chinese_dimensions,
            strengths=["[Mock] Configure DEEPSEEK_API_KEY for real evaluation."],
            weaknesses=["[Mock] No weaknesses identified in mock mode."],
            evidence=[],
            evidence_positions=[],
            suggestions=["[Mock] Add DEEPSEEK_API_KEY to enable full AI evaluation."],
            model_used="mock",
            latency_ms=int((time.perf_counter() - start_time) * 1000),
        )
