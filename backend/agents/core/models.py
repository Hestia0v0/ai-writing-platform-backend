"""
Domain models shared across all agent routers.
All models use Pydantic v2.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ── Shared enums ───────────────────────────────────────────────────────────────

class Language(str, Enum):
    CHINESE = "zh"
    ENGLISH = "en"


class WritingFramework(str, Enum):
    FIVE_PARAGRAPH = "five_paragraph"   # Hook → Thesis → 3 body → Conclusion
    PEEL = "peel"                       # Point → Evidence → Explanation → Link
    QI_CHENG_ZHUAN_HE = "qczh"         # 起承转合 (Chinese traditional structure)
    ARGUMENT_COUNTER = "argument_counter"  # Argument + Counterargument + Rebuttal


class WritingTechnique(str, Enum):
    """Explicit writing technique applied during generation or evaluated for (US-6)."""
    SHOW_DONT_TELL = "show_dont_tell"   # Sensory detail, no bare emotion telling
    NARRATIVE = "narrative"             # Story arc with character & dialogue
    ARGUMENTATIVE = "argumentative"     # Thesis-driven with evidence & rebuttal


class RiskLevel(str, Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ── 1. Guardrail Agent ─────────────────────────────────────────────────────────

class GuardrailRequest(BaseModel):
    text: str = Field(min_length=1, description="User input to screen (prompt or essay text)")
    context: Optional[str] = Field(
        default=None,
        description="Optional context hint: 'prompt' or 'essay'",
    )


class GuardrailResult(BaseModel):
    passed: bool
    risk_level: RiskLevel
    categories: list[str] = Field(
        default_factory=list,
        description="Triggered categories: prompt_injection | jailbreak | inappropriate_content",
    )
    reason: str = Field(default="", description="Rejection reason, empty string if passed")


# ── 2. Drafting Agent ──────────────────────────────────────────────────────────

class DraftRequest(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    language: Language = Language.ENGLISH
    word_count: int = Field(default=400, ge=100, le=2000)
    framework: WritingFramework = WritingFramework.FIVE_PARAGRAPH
    technique: Optional[WritingTechnique] = Field(
        default=None,
        description="Writing technique to enforce: show_dont_tell | narrative | argumentative",
    )
    extra_instructions: Optional[str] = Field(
        default=None,
        description="Any additional free-text instructions beyond the selected technique",
    )
    model: Optional[str] = None


class DraftResult(BaseModel):
    title: str
    language: Language
    framework: WritingFramework
    technique: Optional[WritingTechnique] = Field(
        default=None,
        description="Writing technique that was applied during generation, if any",
    )
    essay: str
    word_count_actual: int
    model_used: str
    tokens_used: int


# ── 3. Evaluation Agent ────────────────────────────────────────────────────────

class VocabGrammarAnalysis(BaseModel):
    """Output from the Vocabulary & Grammar checker sub-agent."""
    error_count: int
    errors: list[dict] = Field(
        default_factory=list,
        description="List of {sentence, issue, suggestion} dicts",
    )
    vocabulary_richness: str = Field(
        description="low | medium | high"
    )
    vocabulary_notes: str
    raw_score: float = Field(ge=0.0, le=25.0, description="Contribution to final score")


class StructureLogicAnalysis(BaseModel):
    """Output from the Structure & Logic checker sub-agent."""
    has_clear_intro: bool
    has_clear_conclusion: bool
    intro_conclusion_echo: bool
    on_topic: bool
    paragraph_structure_ok: bool
    issues: list[str] = Field(default_factory=list)
    raw_score: float = Field(ge=0.0, le=25.0)
    coherence_score: float = Field(
        default=0.0, ge=0.0, le=10.0,
        description=(
            "Sub-dimension: how well ideas and sentences connect across paragraphs (0–10). "
            "Contributes to reader experience but does not add to raw_score total."
        ),
    )


class StyleAnalysis(BaseModel):
    """Output from the Show-Don't-Tell style detector sub-agent (US-9)."""
    tell_count: int
    tell_sentences: list[dict] = Field(
        default_factory=list,
        description="List of {original, suggestion, tell_type} sentence objects",
    )
    descriptive_quality: str = Field(description="weak | adequate | strong")
    raw_score: float = Field(ge=0.0, le=25.0)
    tell_type_counts: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Breakdown of detected tell types: "
            "direct_emotion | bare_adjective | adverb_verb. "
            "Supports pattern recognition reporting (US-9)."
        ),
    )
    emotion_patterns: list[dict] = Field(
        default_factory=list,
        description=(
            "Classified emotion expression patterns: "
            "list of {phrase, emotion, tell_type, suggestion}. "
            "Powers the emotion expression classifier (US-9)."
        ),
    )


class EvaluationRequest(BaseModel):
    document_id: str
    text: str = Field(min_length=10)
    language: Language = Language.ENGLISH
    framework: Optional[WritingFramework] = Field(
        default=None,
        description="Writing framework of the submitted essay (used for framework-aware scoring)",
    )
    technique: Optional[WritingTechnique] = Field(
        default=None,
        description="Writing technique the essay was intended to demonstrate (informs feedback quality)",
    )
    model: Optional[str] = None


class EvidencePosition(BaseModel):
    """A verbatim text segment with its approximate character offsets (US-8 visualization)."""
    text: str
    start: int = Field(description="Approximate start character offset in the original essay")
    end: int = Field(description="Approximate end character offset in the original essay")


class EvaluationResult(BaseModel):
    document_id: str
    total_score: float = Field(ge=0.0, le=100.0)
    grade: str
    vocab_grammar: VocabGrammarAnalysis
    structure_logic: StructureLogicAnalysis
    style: StyleAnalysis
    content_score: float = Field(
        default=0.0, ge=0.0, le=25.0,
        description="Content & Ideas dimension score (0–25)",
    )
    creativity_score: float = Field(
        default=0.0, ge=0.0, le=10.0,
        description=(
            "Sub-dimension of Content & Ideas: originality and creative expression (0–10). "
            "Informational only — does not add to total_score."
        ),
    )
    chinese_dimensions: Optional[dict[str, float]] = Field(
        default=None,
        description=(
            "Chinese-specific scoring dimensions (only populated for language=zh). "
            "Keys: 字词运用 (character expression), 情感深度 (emotional depth), "
            "描写质量 (description quality). Each scored 0–10."
        ),
    )
    strengths: list[str]
    weaknesses: list[str]
    evidence: list[str] = Field(
        default_factory=list,
        description="Verbatim sentences from the essay supporting the assessment (US-8)",
    )
    evidence_positions: list[EvidencePosition] = Field(
        default_factory=list,
        description=(
            "Character-offset positions of each evidence quote in the original essay text. "
            "Used by the frontend visualization layer (US-8 text segment mapping)."
        ),
    )
    suggestions: list[str]
    model_used: str
    latency_ms: int = 0
    cache_hit: bool = Field(
        default=False,
        description="True when this result was served from the evaluation cache (US-17)",
    )


# ── 4. Refinement Agent ────────────────────────────────────────────────────────

class DiffHunk(BaseModel):
    original: str
    revised: str
    reason: str
    category: str = Field(
        default="other",
        description="Change category: grammar | flow | description | vocabulary | other",
    )


class ImprovementSummary(BaseModel):
    """High-level breakdown of changes made by the Refinement Agent (US-13)."""
    grammar_fixes: int = Field(
        default=0,
        description="Number of spelling / grammar corrections applied",
    )
    flow_improvements: int = Field(
        default=0,
        description="Number of phrasing / sentence-flow improvements applied",
    )
    description_upgrades: int = Field(
        default=0,
        description="Number of tell→show / description-quality upgrades applied",
    )
    vocabulary_upgrades: int = Field(
        default=0,
        description="Number of vocabulary replacements with more precise/advanced words",
    )
    overall_notes: str = Field(
        default="",
        description="One-sentence plain-language summary of what was improved overall",
    )


class RefinementRequest(BaseModel):
    document_id: str
    original_text: str = Field(min_length=10)
    weaknesses: list[str] = Field(
        default_factory=list,
        description="Weakness list from the Evaluation Agent",
    )
    suggestions: list[str] = Field(default_factory=list)
    language: Language = Language.ENGLISH
    model: Optional[str] = None


class RefinementResult(BaseModel):
    document_id: str
    original_text: str = Field(
        description="The student's original, unmodified composition (US-13 side-by-side display)",
    )
    refined_text: str = Field(
        description="The fully polished version with grammar, flow, and description improvements",
    )
    diff_hunks: list[DiffHunk] = Field(
        default_factory=list,
        description="Sentence-level diff showing every change and its category",
    )
    improvement_summary: ImprovementSummary = Field(
        default_factory=ImprovementSummary,
        description="Structured breakdown of grammar / flow / description / vocabulary changes (US-13)",
    )
    model_used: str
    tokens_used: int


# ── 5. Knowledge RAG Agent ─────────────────────────────────────────────────────

class RecommendRequest(BaseModel):
    paragraph: str = Field(min_length=5, description="Student paragraph to enrich")
    language: Language = Language.ENGLISH
    top_k: int = Field(default=5, ge=1, le=20)


class VocabRecommendation(BaseModel):
    term: str
    type: str = Field(description="idiom | advanced_word | example_sentence")
    example: str
    relevance_score: float = Field(ge=0.0, le=1.0)


class RecommendResult(BaseModel):
    recommendations: list[VocabRecommendation]
    retrieval_source: str = Field(description="pgvector | mock")
