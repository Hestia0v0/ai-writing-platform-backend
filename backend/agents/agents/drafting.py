

from __future__ import annotations

import logging
import os
import re

from core.models import DraftRequest, DraftResult, Language, WritingFramework, WritingTechnique

logger = logging.getLogger(__name__)


def _count_words(text: str, language: Language) -> int:
    """
    Language-aware word counter.
    For Chinese text, count non-whitespace characters (each CJK character is
    effectively one "word"). For English and other Latin-script languages use
    the standard whitespace-split approach.
    """
    if language == Language.CHINESE:
        return len(re.sub(r"\s+", "", text))
    return len(re.findall(r"\w+", text))

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"

# ── Framework reference definitions (baked into the system prompt) ─────────────

_FRAMEWORK_DEFINITIONS = {
    WritingFramework.FIVE_PARAGRAPH: """\
FIVE-PARAGRAPH ESSAY FRAMEWORK:
  Paragraph 1 — Introduction: attention-grabbing hook (anecdote/statistic/question),
    background context, clear thesis statement that previews three main arguments.
  Paragraph 2 — Body 1: topic sentence (Argument 1), supporting evidence, analysis.
  Paragraph 3 — Body 2: topic sentence (Argument 2), supporting evidence, analysis.
  Paragraph 4 — Body 3: topic sentence (Argument 3), supporting evidence, analysis.
  Paragraph 5 — Conclusion: restate thesis in new words, synthesise arguments,
    closing thought (call to action / broader implication / memorable final sentence).
""",
    WritingFramework.PEEL: """\
PEEL FRAMEWORK (for each body paragraph):
  P — Point: state the main argument of this paragraph in one clear sentence.
  E — Evidence: provide a fact, statistic, quotation, or example that supports P.
  E — Explanation: explain HOW and WHY the evidence supports the point.
  L — Link: connect back to the essay title / thesis and transition to the next point.
Structure: brief intro (thesis) → 3–4 PEEL body paragraphs → brief conclusion.
""",
    WritingFramework.QI_CHENG_ZHUAN_HE: """\
起承转合 FRAMEWORK (Chinese classical essay structure):
  起 (Qǐ) — Opening: introduce the topic, set the scene or mood, draw the reader in.
    Avoid stating the main idea directly; hint at it through imagery or narrative.
  承 (Chéng) — Development: deepen and develop the opening idea; add detail, events,
    or supporting arguments. Maintain thematic consistency.
  转 (Zhuǎn) — Turn / Twist: introduce a contrasting viewpoint, a complication,
    an unexpected element, or a deepening of perspective. This is the emotional/
    intellectual peak of the essay.
  合 (Hé) — Closure: bring all threads together in a resonant conclusion that circles
    back to the opening image or idea, leaving the reader with a lasting impression.
""",
    WritingFramework.ARGUMENT_COUNTER: """\
ARGUMENT–COUNTERARGUMENT FRAMEWORK:
  Introduction: hook + context + thesis (state your position clearly).
  Argument Section: 2–3 paragraphs each making one strong argument for your position,
    with evidence and analysis.
  Counterargument Section: acknowledge the strongest opposing viewpoint(s) fairly,
    then systematically rebut each with evidence or logic.
  Conclusion: reinforce your original position in light of the counterargument rebuttal;
    end with a broader implication or call to action.
""",
}

# ── Technique-specific constraint blocks (US-6) ────────────────────────────────
# Each technique has an English and Chinese variant, selected at generation time.

_TECHNIQUE_BLOCKS_EN = {
    WritingTechnique.SHOW_DONT_TELL: """\
TECHNIQUE — SHOW DON'T TELL (apply throughout every paragraph):
• NEVER state emotions directly. Instead, reveal them through physical reaction,
  action, or dialogue.  BAD: "She was sad."  GOOD: "She pressed her lips together
  and stared at the empty chair."
• Replace bare adjectives with concrete sensory details
  (sight, sound, smell, taste, touch).
  BAD: "The food was delicious."  GOOD: "The fragrance of garlic and butter drifted
  through the kitchen, and she spooned the first mouthful before the bowl hit the table."
• Swap adverb+verb for a more vivid, specific verb.
  BAD: "He ran quickly."  GOOD: "He sprinted."
• Every emotion the reader should feel must be earned through specific detail,
  never declared.
""",
    WritingTechnique.NARRATIVE: """\
TECHNIQUE — NARRATIVE STORYTELLING (apply throughout):
• Build a clear story arc: inciting incident → rising tension → climax → resolution.
• Develop at least one character with a discernible motivation and emotional change
  across the essay.
• Include at least two lines of natural dialogue that reveal character and
  advance the plot — punctuate correctly.
• Open with a vivid, specific setting that grounds the reader immediately.
• Control pacing: slow down (longer sentences, sensory detail) at emotional peaks;
  speed up (short, punchy sentences) during action.
""",
    WritingTechnique.ARGUMENTATIVE: """\
TECHNIQUE — ARGUMENTATIVE WRITING (apply throughout):
• State your thesis in the last sentence of the introduction — position must be
  crystal-clear and debatable.
• Back every argument with at least one concrete piece of evidence: a statistic,
  a real-world example, or a credible expert view.
• Devote one full paragraph to fairly presenting the strongest counterargument,
  then systematically rebut it with logic or evidence.
• Maintain a formal, objective tone — avoid personal anecdotes and emotional
  language; use hedging where appropriate ("Research suggests…").
• Close with a call to action or a broader societal implication.
""",
}

_TECHNIQUE_BLOCKS_ZH = {
    WritingTechnique.SHOW_DONT_TELL: """\
写作技巧 — 以景写情（Show Don't Tell，贯穿全文）：
• 绝对不允许直接陈述情绪。应通过人物的动作、身体反应或对话来传达情感。
  错误示例：「她很悲伤。」　正确示例：「她抿紧嘴唇，目光落在那把空椅子上，久久不愿移开。」
• 用具体感官细节（视觉、听觉、嗅觉、触觉、味觉）替代干瘪的形容词。
  错误示例：「食物很好吃。」　正确示例：「锅里飘出蒜香与黄油融合的气息，她还没落座便舀起一勺送入口中。」
• 用精准生动的动词替代"副词+普通动词"。
  错误示例：「他跑得很快。」　正确示例：「他猛地冲出去。」
• 所有需要读者感受到的情绪，都必须通过具体细节来传递，而非直白宣告。
""",
    WritingTechnique.NARRATIVE: """\
写作技巧 — 叙事写作（贯穿全文）：
• 构建清晰的故事弧线：起因（触发事件）→ 矛盾升级 → 高潮转折 → 结局收束。
• 加入至少两句自然的对话，用以展现人物性格并推动情节发展，并正确使用标点。
• 以一个具体、生动的场景开篇，立刻将读者带入故事情境。
• 控制叙事节奏：情感高点放缓节奏（长句 + 感官细节），动作段落加快节奏（短句 + 简洁动词）。
""",
    WritingTechnique.ARGUMENTATIVE: """\
写作技巧 — 议论文写作（贯穿全文）：
• 在开头段最后一句明确表达论点——立场必须清晰。
• 每个分论点至少提供一项具体证据：统计数据、真实案例或权威观点。
• 用一整段公正呈现最有力的对立观点，然后以逻辑或证据逐一驳斥。
• 保持正式、客观的语气——避免个人经历和情绪化措辞；适当使用委婉表达（如「研究表明……」）。
• 以呼吁行动或对社会的宏观影响作为结尾。
""",
}

# Legacy alias kept for any external references — points to English blocks
_TECHNIQUE_BLOCKS = _TECHNIQUE_BLOCKS_EN

# ── Main system prompt template ────────────────────────────────────────────────

_DRAFTING_SYSTEM_PROMPT_TEMPLATE = """\
You are an expert bilingual writing coach and ghostwriter fluent in both Mandarin Chinese \
and English academic and creative prose.

Your task is to write a complete, high-quality essay according to the exact specifications \
provided. You MUST follow the structural framework below precisely.

=== FRAMEWORK YOU MUST FOLLOW ===
{framework_definition}
=================================

General writing standards:
• Every sentence must serve a purpose — eliminate padding and filler.
• Use precise, varied vocabulary appropriate to the target audience (secondary school level).
• Apply Show-Don't-Tell where possible: show emotions through actions, dialogue, and \
  sensory detail rather than stating them ("She slammed the door" not "She was angry").
• Vary sentence length: short punchy sentences for impact, longer ones for description.
• Transitions between paragraphs must be smooth and logical.

Output format:
• Write ONLY the essay itself — no preamble, no meta-commentary, no word count label.
• If the language is Chinese (zh), write entirely in Simplified Chinese.
• If the language is English (en), write entirely in English.
• Aim for the requested word count (±10 %).
"""


# Minimum paragraph counts required by each framework (used by quality filter)
_MIN_PARAGRAPHS: dict[WritingFramework, int] = {
    WritingFramework.FIVE_PARAGRAPH: 5,
    WritingFramework.PEEL: 4,           # intro + ≥3 PEEL paragraphs
    WritingFramework.QI_CHENG_ZHUAN_HE: 4,
    WritingFramework.ARGUMENT_COUNTER: 4,
}


class DraftingAgent:
    """
    Drafting & Generation Agent.
    Instantiate once at startup.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self._api_key = api_key or os.getenv("DEEPSEEK_API_KEY", "")
        self._model = model or os.getenv("DRAFTING_MODEL", DEFAULT_MODEL)
        self._client = None
        if self._api_key:
            try:
                from openai import AsyncOpenAI  # noqa: PLC0415
                self._client = AsyncOpenAI(
                    api_key=self._api_key,
                    base_url=DEEPSEEK_BASE_URL,
                )
            except ImportError:
                logger.warning("openai not installed — drafting mock mode active")

    # ── Public entry point ─────────────────────────────────────────────────────

    async def draft(self, request: DraftRequest) -> DraftResult:
        if self._client is None:
            return self._mock_draft(request)
        return await self._live_draft(request)

    # ── Quality filter ─────────────────────────────────────────────────────────

    @staticmethod
    def _quality_filter(
        essay: str,
        request: DraftRequest,
        actual_words: int,
    ) -> list[str]:
        """
        Post-generation quality checks.  Returns a (possibly empty) list of
        warning strings.  Empty → essay passed all checks.
        """
        warnings: list[str] = []

        # 1. Word-count within ±10 % of target
        low = int(request.word_count * 0.9)
        high = int(request.word_count * 1.1)
        if actual_words < low:
            warnings.append(
                f"word_count_low: generated {actual_words} words, "
                f"target {request.word_count} (acceptable ≥{low})"
            )
        elif actual_words > high:
            warnings.append(
                f"word_count_high: generated {actual_words} words, "
                f"target {request.word_count} (acceptable ≤{high})"
            )

        # 2. Paragraph structure — count double-newline-separated blocks
        paragraphs = [p.strip() for p in essay.split("\n\n") if p.strip()]
        required = _MIN_PARAGRAPHS.get(request.framework, 3)
        if len(paragraphs) < required:
            warnings.append(
                f"paragraph_count_low: {len(paragraphs)} paragraphs detected, "
                f"expected at least {required} for '{request.framework.value}'"
            )

        # 3. Minimum content sanity
        if len(essay.strip()) < 80:
            warnings.append("essay_too_short: generated essay is too short or empty")

        return warnings

    # ── Prompt-building helpers ────────────────────────────────────────────────

    @staticmethod
    def _build_technique_block(request: DraftRequest) -> str:
        if not request.technique:
            return ""
        lang_blocks = (
            _TECHNIQUE_BLOCKS_ZH
            if request.language == Language.CHINESE
            else _TECHNIQUE_BLOCKS_EN
        )
        block = lang_blocks.get(request.technique)
        if not block:
            return ""
        if request.language == Language.CHINESE:
            return "\n\n=== 必须应用的写作技巧 ===\n" + block + "========================\n"
        return "\n\n=== MANDATORY WRITING TECHNIQUE ===\n" + block + "====================================\n"

    @staticmethod
    def _build_phrase_block(request: DraftRequest) -> str:
        """Inject Knowledge-RAG phrase hints into the user message (US-6)."""
        hints = request.phrase_hints
        if not hints:
            return ""
        items = "\n".join(f"• {p}" for p in hints)
        if request.language == Language.CHINESE:
            return (
                "\n\n请在适当位置自然地融入以下词汇或短语"
                "（不必全部使用，但至少使用其中三个，确保语境自然）：\n" + items
            )
        return (
            "\n\nNaturally incorporate the following vocabulary/phrases where appropriate "
            "(use at least three; do not force all of them — only where they fit naturally):\n"
            + items
        )

    def _build_user_message(self, request: DraftRequest, word_count_override: int | None = None) -> str:
        lang_label = "Chinese (Simplified)" if request.language == Language.CHINESE else "English"
        target_wc = word_count_override or request.word_count
        technique_block = self._build_technique_block(request)
        phrase_block = self._build_phrase_block(request)
        extra = f"\nAdditional instructions: {request.extra_instructions}" if request.extra_instructions else ""
        return (
            f"Please write an essay with the following specifications:\n"
            f"- Title: {request.title}\n"
            f"- Language: {lang_label}\n"
            f"- Target word count: approximately {target_wc} words\n"
            f"- Framework: {request.framework.value}"
            f"{technique_block}{phrase_block}{extra}"
        )

    # ── Core LLM call ──────────────────────────────────────────────────────────

    async def _call_model(
        self,
        system_prompt: str,
        user_message: str,
        model: str,
        word_count: int,
    ) -> tuple[str, int]:
        """Make one LLM API call; returns (essay_text, total_tokens)."""
        response = await self._client.chat.completions.create(  # type: ignore[union-attr]
            model=model,
            max_tokens=min(4096, word_count * 6),
            temperature=0.85,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
        )
        essay = (response.choices[0].message.content or "").strip()
        tokens = response.usage.prompt_tokens + response.usage.completion_tokens
        return essay, tokens

    # ── Live drafting with quality-filter + single retry ──────────────────────

    async def _live_draft(self, request: DraftRequest) -> DraftResult:
        framework_def = _FRAMEWORK_DEFINITIONS[request.framework]
        system_prompt = _DRAFTING_SYSTEM_PROMPT_TEMPLATE.format(
            framework_definition=framework_def
        )
        model = request.model or self._model

        # First attempt
        user_message = self._build_user_message(request)
        essay, tokens = await self._call_model(system_prompt, user_message, model, request.word_count)
        actual_words = _count_words(essay, request.language)

        # Quality filter — retry once if word count is off by more than 20 %
        retry_low = int(request.word_count * 0.80)
        retry_high = int(request.word_count * 1.20)
        if actual_words < retry_low or actual_words > retry_high:
            logger.info(
                "Quality filter: word count %d outside [%d, %d] — retrying with adjusted target",
                actual_words, retry_low, retry_high,
            )
            # Guide the model toward the target count explicitly
            adjusted_wc = request.word_count
            if actual_words < retry_low:
                adjusted_wc = int(request.word_count * 1.15)
            else:
                adjusted_wc = int(request.word_count * 0.88)

            retry_message = self._build_user_message(request, word_count_override=adjusted_wc)
            retry_essay, retry_tokens = await self._call_model(
                system_prompt, retry_message, model, adjusted_wc
            )
            retry_words = _count_words(retry_essay, request.language)

            # Accept the retry only if it is closer to the target
            if abs(retry_words - request.word_count) < abs(actual_words - request.word_count):
                essay, tokens, actual_words = retry_essay, retry_tokens, retry_words
                logger.info("Quality filter: retry accepted (word count now %d)", actual_words)
            else:
                logger.info(
                    "Quality filter: retry not better (%d vs %d), keeping original",
                    retry_words, actual_words,
                )

        quality_warnings = self._quality_filter(essay, request, actual_words)
        if quality_warnings:
            logger.warning("Quality warnings: %s", quality_warnings)

        return DraftResult(
            title=request.title,
            language=request.language,
            framework=request.framework,
            technique=request.technique,
            essay=essay,
            word_count_actual=actual_words,
            model_used=model,
            tokens_used=tokens,
            quality_warnings=quality_warnings,
            phrases_applied=request.phrase_hints or [],
        )

    def _mock_draft(self, request: DraftRequest) -> DraftResult:
        if request.language == Language.CHINESE:
            essay = (
                f"【{request.title}】\n\n"
                "[模拟模式] 请配置 DEEPSEEK_API_KEY 以启用真实 AI 写作功能。\n\n"
                "这是一篇按照所选框架生成的模拟范文。正式环境中，AI 将根据您的"
                "标题、字数要求和写作框架生成一篇完整的高质量作文。\n\n"
                "框架: " + request.framework.value
            )
        else:
            essay = (
                f"[mock] Essay: {request.title}\n\n"
                "This is a placeholder generated in mock mode "
                "(no DEEPSEEK_API_KEY configured). "
                "In production, the AI will generate a full "
                f"{request.word_count}-word essay following the "
                f"{request.framework.value} framework."
            )
        return DraftResult(
            title=request.title,
            language=request.language,
            framework=request.framework,
            technique=request.technique,
            essay=essay,
            word_count_actual=len(essay.split()),
            model_used="mock",
            tokens_used=0,
            quality_warnings=[],
            phrases_applied=request.phrase_hints or [],
        )
