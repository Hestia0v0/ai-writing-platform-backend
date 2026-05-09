"""Unit tests — DocumentProcessor pipeline (no network, no real AI calls)"""
import asyncio
import io
import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../backend/pipelines"))

from doc_processor import (
    AIInferenceClient,
    DocumentProcessor,
    PipelineStage,
    TextCleaner,
    TextChunker,
    TxtParser,
    UnsupportedFormatError,
    FileSizeError,
    ParseError,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def make_processor(use_mock: bool = True) -> DocumentProcessor:
    return DocumentProcessor(
        inference_client=AIInferenceClient(use_mock=use_mock)
    )


SAMPLE_TEXT = (
    "The quick brown fox jumps over the lazy dog. " * 200
)  # ~1 400 words — enough to produce multiple chunks


# ── TextCleaner ────────────────────────────────────────────────────────────────

class TestTextCleaner:
    cleaner = TextCleaner()

    def test_strips_leading_trailing_whitespace(self):
        assert self.cleaner.clean("  hello  ") == "hello"

    def test_collapses_inline_spaces(self):
        result = self.cleaner.clean("word1   word2\tword3")
        assert "  " not in result

    def test_collapses_excess_blank_lines(self):
        result = self.cleaner.clean("para1\n\n\n\n\npara2")
        assert "\n\n\n" not in result

    def test_removes_control_characters(self):
        result = self.cleaner.clean("hello\x00\x01world")
        assert "\x00" not in result
        assert "\x01" not in result

    def test_preserves_intentional_newlines(self):
        result = self.cleaner.clean("line1\nline2")
        assert "\n" in result


# ── TextChunker ────────────────────────────────────────────────────────────────

class TestTextChunker:
    def test_single_chunk_when_text_fits(self):
        chunker = TextChunker(chunk_size=100, overlap=10)
        chunks = chunker.chunk("word " * 50)
        assert len(chunks) == 1
        assert chunks[0].chunk_index == 0

    def test_multiple_chunks_produced(self):
        chunker = TextChunker(chunk_size=10, overlap=2)
        chunks = chunker.chunk("word " * 30)
        assert len(chunks) > 1

    def test_overlap_is_respected(self):
        chunker = TextChunker(chunk_size=10, overlap=3)
        chunks = chunker.chunk("word " * 30)
        # Each chunk should be ≤ chunk_size words
        for c in chunks:
            assert c.word_count <= 10

    def test_empty_text_returns_no_chunks(self):
        chunker = TextChunker()
        assert chunker.chunk("") == []
        assert chunker.chunk("   ") == []

    def test_chunk_indices_are_sequential(self):
        chunker = TextChunker(chunk_size=5, overlap=1)
        chunks = chunker.chunk("a b c d e f g h i j k l m n o p")
        for i, chunk in enumerate(chunks):
            assert chunk.chunk_index == i

    def test_overlap_must_be_less_than_chunk_size(self):
        with pytest.raises(ValueError):
            TextChunker(chunk_size=10, overlap=10)


# ── TxtParser ─────────────────────────────────────────────────────────────────

class TestTxtParser:
    parser = TxtParser()

    def test_can_parse_txt(self):
        assert self.parser.can_parse(".txt") is True
        assert self.parser.can_parse(".pdf") is False

    def test_utf8_decode(self):
        content = "Hello, world!".encode("utf-8")
        text, pages = self.parser.parse(content, "test.txt")
        assert text == "Hello, world!"
        assert pages == 1

    def test_latin1_fallback(self):
        content = "caf\xe9".encode("latin-1")
        text, _ = self.parser.parse(content, "test.txt")
        assert "caf" in text

    def test_raises_parse_error_on_unreadable_bytes(self):
        with pytest.raises(ParseError):
            # Bytes that fail all supported encodings
            self.parser.parse(b"\x80\x81\x82\x83" * 100, "bad.txt")


# ── Full Pipeline (mock inference) ────────────────────────────────────────────

class TestDocumentProcessor:
    def test_successful_txt_pipeline(self):
        processor = make_processor()
        content = SAMPLE_TEXT.encode("utf-8")
        result = asyncio.run(processor.process("essay.txt", content))

        assert result.status == "success"
        assert result.stage_reached == PipelineStage.COMPLETE
        assert result.word_count > 0
        assert result.chunk_count > 0
        assert result.scoring is not None
        assert 0 <= result.scoring.score <= 100
        assert result.scoring.grade in ("A", "B", "C", "D", "F")
        assert len(result.scoring.feedback) > 0
        assert result.processing_time_ms > 0

    def test_unsupported_format_fails_at_upload(self):
        processor = make_processor()
        result = asyncio.run(processor.process("file.exe", b"data"))
        assert result.status == "failed"
        assert result.stage_reached == PipelineStage.UPLOAD

    def test_empty_content_fails_at_parse(self):
        processor = make_processor()
        result = asyncio.run(processor.process("empty.txt", b"   "))
        assert result.status == "failed"
        assert result.stage_reached == PipelineStage.PARSE

    def test_document_id_is_derived_when_not_provided(self):
        processor = make_processor()
        content = SAMPLE_TEXT.encode()
        result = asyncio.run(processor.process("doc.txt", content))
        assert result.document_id != ""
        assert "_" in result.document_id   # stem_hash format

    def test_explicit_document_id_is_preserved(self):
        processor = make_processor()
        content = SAMPLE_TEXT.encode()
        result = asyncio.run(processor.process("doc.txt", content, document_id="my-id-123"))
        assert result.document_id == "my-id-123"

    def test_mock_score_is_deterministic(self):
        processor = make_processor()
        content = SAMPLE_TEXT.encode()
        r1 = asyncio.run(processor.process("doc.txt", content, document_id="fixed-id"))
        r2 = asyncio.run(processor.process("doc.txt", content, document_id="fixed-id"))
        assert r1.scoring.score == r2.scoring.score

    def test_file_size_limit_enforced(self):
        processor = make_processor()
        big_content = b"x" * (21 * 1024 * 1024)   # 21 MB
        result = asyncio.run(processor.process("large.txt", big_content))
        assert result.status == "failed"
        assert result.stage_reached == PipelineStage.UPLOAD


# ── FastAPI router smoke test ──────────────────────────────────────────────────

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_pipelines_root():
    assert client.get("/").json()["service"] == "pipelines"


def test_pipeline_health():
    assert client.get("/health/").json()["status"] == "healthy"


def test_documents_list_empty():
    response = client.get("/documents/")
    assert response.status_code == 200
    assert "document_ids" in response.json()


def test_process_endpoint_txt():
    content = SAMPLE_TEXT.encode("utf-8")
    response = client.post(
        "/documents/process",
        files={"file": ("test_essay.txt", io.BytesIO(content), "text/plain")},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["scoring"]["score"] >= 0


def test_process_endpoint_unsupported_type():
    response = client.post(
        "/documents/process",
        files={"file": ("script.exe", io.BytesIO(b"data"), "application/octet-stream")},
    )
    assert response.status_code == 415


def test_process_endpoint_empty_file():
    response = client.post(
        "/documents/process",
        files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")},
    )
    assert response.status_code == 400
