"""
Pipelines 纯逻辑单测（DocumentProcessor 及相关类型）

本文件刻意：
  • 只 import backend/pipelines/doc_processor.py 中的类型与函数；
  • 不 import main、不使用 TestClient；
  • 从而在 pytest collect/import 阶段不会加载 FastAPI lifespan，也不会执行 asyncpg get_pool。

因此属于「纯逻辑 / 领域单元测试」：在进程内用 AIInferenceClient(use_mock=True) 跑完整流水线，
无真实外呼、无数据库。与带 HTTP + Postgres 的 tests/unit/test_pipelines.py 分离，
以便 minimal CI 在无 Docker Postgres 的环境下稳定执行。
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../backend/pipelines"))

from doc_processor import (
    AIInferenceClient,
    DocumentProcessor,
    PipelineStage,
    TextCleaner,
    TextChunker,
    TxtParser,
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
#
# TxtParser 采用「先成功先返回」的编码 fallback：依次尝试 utf-8 → utf-16 → latin-1 → cp1252，
# 任一 decode 成功即返回，不探测「最可能」编码。因此：
#   • 偶数长度且能被 utf-16 吃下的字节串会先于 latin-1 成功（可能与真实意图不符）；
#   • latin-1 对任意单字节永不抛 UnicodeDecodeError，链中靠后的编码几乎总能兜底；
#   • 与「容错型」语义一致：尽量不 ParseError，而非对随机二进制报错。
# 以下用例按上述真实行为编写，不假设 latin-1 一定排在 utf-16 之前被尝试。

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
        # 使用「奇数长度」Latin-1 负载：utf-16 对奇数长度会失败，从而不会抢在 latin-1 之前成功。
        # utf-8 对该序列亦失败，最终由 latin-1 解码，结果应与 bytes.decode("latin-1") 一致。
        content = "caf\xe9".encode("latin-1") + b"\xff"  # 5 字节，café + 0xFF（ÿ）
        text, pages = self.parser.parse(content, "latin1-ish.txt")
        assert pages == 1
        assert text == content.decode("latin-1")
        assert "caf" in text

    def test_arbitrary_bytes_decode_tolerant_no_parse_error(self):
        # 高熵字节流：utf-8 常失败，但 utf-16 或 latin-1 往往仍能 decode，符合容错语义，不应抛 ParseError。
        content = b"\x80\x81\x82\x83" * 100
        text, pages = self.parser.parse(content, "binary-ish.txt")
        assert pages == 1
        assert isinstance(text, str)
        assert len(text) > 0


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
