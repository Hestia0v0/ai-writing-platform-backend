"""
GradingClient 三层降级链路的纯逻辑单测（doc_processor.py）。

覆盖两类行为，均不依赖真实网络调用：
  1. 字段映射 —— agents(EvaluationResult) / ai_inference(GradingResult) 的原始 JSON
     如何被规整为统一的 ScoringResult（防止改字段名时静默丢数据/映射错维度）。
  2. 三层降级控制流 —— agents 失败 -> ai_inference；ai_inference 也失败 -> mock。
     用 unittest.mock 直接打桩 GradingClient._call_agents / _call_ai_inference，
     不引入额外的 HTTP mock 依赖，保持和 CI 白名单里其它纯逻辑测试一致的零外部依赖风格。
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../backend/pipelines"))

from doc_processor import FeedbackItem, GradingClient, ScoringResult, TextChunk


# ── Fixtures ─────────────────────────────────────────────────────────────────

def _stub_result(source_tier: str) -> ScoringResult:
    return ScoringResult(
        document_id="doc-1",
        score=80.0,
        grade="B",
        feedback=[FeedbackItem(category="evidence", severity="info", message="ok", suggestion="ok")],
        summary="stub",
        model_used="stub-model",
        source_tier=source_tier,
    )


AGENTS_RESPONSE = {
    "document_id": "doc-1",
    "total_score": 82.0,
    "grade": "B",
    "vocab_grammar": {
        "error_count": 2,
        "errors": [],
        "vocabulary_richness": "high",
        "vocabulary_notes": "Good range of academic vocabulary.",
        "raw_score": 20.0,
    },
    "structure_logic": {
        "has_clear_intro": True,
        "has_clear_conclusion": True,
        "intro_conclusion_echo": True,
        "on_topic": True,
        "paragraph_structure_ok": True,
        "issues": ["Paragraph 3 drifts off-topic."],
        "raw_score": 18.0,
        "coherence_score": 7.0,
    },
    "style": {
        "tell_count": 1,
        "tell_sentences": [],
        "descriptive_quality": "adequate",
        "raw_score": 17.0,
        "tell_type_counts": {},
        "emotion_patterns": [],
    },
    "content_score": 22.0,
    "creativity_score": 6.0,
    "chinese_dimensions": None,
    "strengths": ["Clear thesis statement."],
    "weaknesses": ["Weak transitions between paragraphs."],
    "evidence": [],
    "evidence_positions": [],
    "suggestions": [
        "Add a concrete example to support the thesis.",
        "Tighten paragraph 3 back to the main topic.",
        "Proofread for the two flagged grammar issues.",
        "Introduce one or two more advanced synonyms.",
        "Show the character's reaction instead of stating it.",
    ],
    "model_used": "deepseek-v4-pro",
    "latency_ms": 4200,
    "cache_hit": False,
    "flagged_for_review": False,
    "flag_reason": None,
}

AI_INFERENCE_RESPONSE = {
    "document_id": "doc-1",
    "score": 74.0,
    "grade": "C",
    "confidence": 0.81,
    "rubric": [
        {"dimension": "content", "score": 18.0, "max_score": 25.0, "feedback": "Solid but generic evidence."},
        {"dimension": "organization", "score": 20.0, "max_score": 25.0, "feedback": "Clear structure."},
        {"dimension": "language", "score": 17.0, "max_score": 25.0, "feedback": "Some awkward phrasing."},
        {"dimension": "conventions", "score": 19.0, "max_score": 25.0, "feedback": "Minor punctuation slips."},
    ],
    "overall_feedback": "A competent essay that would benefit from sharper evidence.",
    "improvement_tips": [
        "Cite a specific example per body paragraph.",
        "Vary sentence openings.",
        "Reread for comma splices.",
    ],
    "model_used": "deepseek-v4-flash",
}


# ── Field mapping: agents -> ScoringResult ────────────────────────────────────

class TestParseAgentsResponse:
    def _parse(self, data: dict) -> ScoringResult:
        return GradingClient()._parse_agents_response("doc-1", data)

    def test_score_and_grade_pass_through(self):
        result = self._parse(AGENTS_RESPONSE)
        assert result.score == 82.0
        assert result.grade == "B"
        assert result.source_tier == "agents"
        assert result.model_used == "deepseek-v4-pro"

    def test_all_five_categories_present(self):
        result = self._parse(AGENTS_RESPONSE)
        categories = {item.category for item in result.feedback}
        assert categories == {"evidence", "structure", "grammar", "vocabulary", "clarity"}

    def test_structure_issues_surface_in_message(self):
        result = self._parse(AGENTS_RESPONSE)
        structure_item = next(i for i in result.feedback if i.category == "structure")
        assert "off-topic" in structure_item.message

    def test_vocabulary_notes_surface_separately_from_grammar(self):
        result = self._parse(AGENTS_RESPONSE)
        grammar_item = next(i for i in result.feedback if i.category == "grammar")
        vocab_item = next(i for i in result.feedback if i.category == "vocabulary")
        assert "2" in grammar_item.message
        assert vocab_item.message == "Good range of academic vocabulary."

    def test_summary_includes_strengths_and_weaknesses(self):
        result = self._parse(AGENTS_RESPONSE)
        assert "Clear thesis statement" in result.summary
        assert "Weak transitions" in result.summary

    def test_missing_optional_fields_do_not_raise(self):
        minimal = {
            "document_id": "doc-2",
            "total_score": 50.0,
            "grade": "F",
        }
        result = self._parse(minimal)
        assert result.score == 50.0
        assert len(result.feedback) == 5


# ── Field mapping: ai_inference -> ScoringResult ──────────────────────────────

class TestParseLiveResponse:
    def _parse(self, data: dict) -> ScoringResult:
        return GradingClient()._parse_live_response("doc-1", word_count=500, data=data)

    def test_score_and_grade_pass_through(self):
        result = self._parse(AI_INFERENCE_RESPONSE)
        assert result.score == 74.0
        assert result.grade == "C"
        assert result.source_tier == "ai_inference"

    def test_dimension_to_category_mapping(self):
        result = self._parse(AI_INFERENCE_RESPONSE)
        categories = [item.category for item in result.feedback]
        assert categories == ["evidence", "structure", "clarity", "grammar"]


# ── Three-tier fallback control flow ──────────────────────────────────────────

class TestScoreDocumentFallbackChain:
    """
    Uses asyncio.run() rather than pytest-asyncio markers, matching the
    dependency-free style of test_pipelines_doc_processor.py — this file is
    intended to run in the minimal CI job, which only installs pytest.
    """

    CHUNKS = [TextChunk(chunk_index=0, text="Sample essay text.", word_count=3, char_count=19)]

    def test_uses_agents_result_when_healthy(self):
        async def run():
            client = GradingClient()
            with patch.object(client, "_call_agents", AsyncMock(return_value=_stub_result("agents"))) as agents_mock, \
                 patch.object(client, "_call_ai_inference", AsyncMock()) as ai_inference_mock:
                result = await client.score_document("doc-1", self.CHUNKS, word_count=3)
            return result, agents_mock, ai_inference_mock

        result, agents_mock, ai_inference_mock = asyncio.run(run())
        assert result.source_tier == "agents"
        agents_mock.assert_awaited_once()
        ai_inference_mock.assert_not_awaited()

    def test_falls_back_to_ai_inference_when_agents_fails(self):
        async def run():
            client = GradingClient()
            with patch.object(
                client, "_call_agents", AsyncMock(side_effect=httpx.RequestError("connection refused"))
            ), patch.object(
                client, "_call_ai_inference", AsyncMock(return_value=_stub_result("ai_inference"))
            ) as ai_inference_mock:
                result = await client.score_document("doc-1", self.CHUNKS, word_count=3)
            return result, ai_inference_mock

        result, ai_inference_mock = asyncio.run(run())
        assert result.source_tier == "ai_inference"
        ai_inference_mock.assert_awaited_once()

    def test_falls_back_to_ai_inference_on_agents_http_error(self):
        async def run():
            client = GradingClient()
            request = httpx.Request("POST", "http://agents:8004/agent/evaluate")
            response = httpx.Response(500, request=request)
            with patch.object(
                client, "_call_agents",
                AsyncMock(side_effect=httpx.HTTPStatusError("500", request=request, response=response)),
            ), patch.object(
                client, "_call_ai_inference", AsyncMock(return_value=_stub_result("ai_inference"))
            ) as ai_inference_mock:
                result = await client.score_document("doc-1", self.CHUNKS, word_count=3)
            return result, ai_inference_mock

        result, ai_inference_mock = asyncio.run(run())
        assert result.source_tier == "ai_inference"
        ai_inference_mock.assert_awaited_once()

    def test_falls_back_to_mock_when_both_tiers_fail(self):
        async def run():
            client = GradingClient()
            with patch.object(
                client, "_call_agents", AsyncMock(side_effect=httpx.RequestError("down"))
            ), patch.object(
                client, "_call_ai_inference", AsyncMock(side_effect=httpx.RequestError("down"))
            ):
                return await client.score_document("doc-1", self.CHUNKS, word_count=3)

        result = asyncio.run(run())
        assert result.source_tier == "mock"

    def test_use_mock_flag_skips_both_tiers(self):
        async def run():
            client = GradingClient(use_mock=True)
            with patch.object(client, "_call_agents", AsyncMock()) as agents_mock:
                result = await client.score_document("doc-1", self.CHUNKS, word_count=3)
            return result, agents_mock

        result, agents_mock = asyncio.run(run())
        assert result.source_tier == "mock"
        agents_mock.assert_not_awaited()
