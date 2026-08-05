"""
Measures InMemoryCache hit rate and hit/miss latency using the project's own
golden essay set (tests/evals/goldens/essays.json).

Run (from ai-writing-platform-backend/):
    pip install -r backend/ai_inference/requirements.txt
    python scripts/resume_bench/bench_cache.py

Set DEEPSEEK_API_KEY to measure real "miss" latency against the live
DeepSeek API. Without it, misses fall back to the deterministic mock grader
(near-instant), so the hit/miss timing gap won't be representative of
production numbers -- but hit-rate numbers are still valid either way.
"""
import asyncio
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///./_bench_scratch.db")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "ai_inference"))

from core.cache import InMemoryCache  # noqa: E402
from core.grader import GradingEngine  # noqa: E402
from db.database import init_db  # noqa: E402

init_db()

GOLDENS = json.loads((ROOT / "tests" / "evals" / "goldens" / "essays.json").read_text(encoding="utf-8"))


def _by_id(doc_id: str) -> str:
    return next(e["text"] for e in GOLDENS if e["id"] == doc_id)


def _lightly_edited_en(text: str) -> str:
    """Simulate a student resubmitting with trivial paraphrasing (synonym swaps)."""
    return (
        text.replace(" good ", " positive ")
        .replace(" bad ", " negative ")
        .replace(" very ", " extremely ")
    )


def _lightly_edited_zh(text: str) -> str:
    """Same idea, but with real substrings from zh-strong-01 -- English word-boundary
    swaps are no-ops on Chinese text (no spaces, no " good "-style tokens), so this
    needs its own word list actually present in the source essay."""
    return (
        text.replace("迅猛", "快速")
        .replace("深刻", "显著")
        .replace("极大", "非常")
    )


# Ordered to exercise exact hit, fuzzy hit, and miss paths.
CASES = [
    ("en-strong: first submit", _by_id("en-strong-01")),
    ("en-strong: exact resubmit", _by_id("en-strong-01")),
    ("en-medium: first submit", _by_id("en-medium-01")),
    ("en-medium: lightly edited resubmit", _lightly_edited_en(_by_id("en-medium-01"))),
    ("en-weak: first submit", _by_id("en-weak-01")),
    ("zh-strong: first submit", _by_id("zh-strong-01")),
    ("zh-strong: lightly edited resubmit", _lightly_edited_zh(_by_id("zh-strong-01"))),
]


async def main() -> None:
    cache = InMemoryCache()
    grader = GradingEngine()  # DEEPSEEK_API_KEY optional; mock fallback otherwise

    rows = []
    for label, text in CASES:
        t0 = time.perf_counter()
        hit = cache.get(text)
        if hit:
            _, similarity = hit
            elapsed_ms = (time.perf_counter() - t0) * 1000
            rows.append((label, "HIT", similarity, elapsed_ms))
            continue

        result = await grader.grade(document_id=label, text=text)
        cache.set(text, result)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        rows.append((label, "MISS", None, elapsed_ms))

    print(f"{'case':40s} {'status':6s} {'similarity':10s} {'latency_ms':>10s}")
    for label, status, sim, ms in rows:
        sim_str = f"{sim:.3f}" if sim is not None else "-"
        print(f"{label:40s} {status:6s} {sim_str:10s} {ms:10.2f}")

    hits = [r for r in rows if r[1] == "HIT"]
    misses = [r for r in rows if r[1] == "MISS"]
    print()
    print(f"hit rate: {len(hits)}/{len(rows)} = {len(hits) / len(rows):.1%}")
    if hits:
        print(f"avg HIT latency:  {sum(r[3] for r in hits) / len(hits):.2f} ms")
    if misses:
        mode = "live DeepSeek call" if grader._client else "mock fallback -- set DEEPSEEK_API_KEY for real numbers"
        print(f"avg MISS latency: {sum(r[3] for r in misses) / len(misses):.2f} ms ({mode})")

    zh_edit_hit = any(r[0] == "zh-strong: lightly edited resubmit" and r[1] == "HIT" for r in rows)
    if not zh_edit_hit:
        print()
        print("NOTE: the lightly-edited Chinese case did NOT get a fuzzy cache hit.")
        print("cache.py's _jaccard() tokenizes with text.split(), which only splits on")
        print("whitespace -- Chinese text has none, so the whole string becomes a single")
        print("token and fuzzy matching degenerates to exact-match. Real gap: _jaccard")
        print("would need jieba tokenization (like doc_processor.py already uses) to")
        print("work for CJK near-duplicates.")


if __name__ == "__main__":
    asyncio.run(main())
