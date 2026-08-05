"""
Compares BatchEngine throughput at concurrency=1 (serial) vs concurrency=10
using the project's own golden essay set, replicated to build a bigger batch.
Exercises the real BatchEngine class from core/batch_engine.py, not a
reimplementation.

Run (from ai-writing-platform-backend/):
    pip install -r backend/ai_inference/requirements.txt
    python scripts/resume_bench/bench_concurrency.py

Set DEEPSEEK_API_KEY for real DeepSeek grading latency; without it the mock
grader is near-instant, so there's almost nothing for the concurrency to
parallelize and the speedup number will be meaningless. This benchmark only
tells you something real with a live API key.
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

from core.batch_engine import BatchEngine  # noqa: E402
from core.cache import InMemoryCache  # noqa: E402
from core.grader import GradingEngine  # noqa: E402
from core.models import BatchSubmitRequest, CompositionItem  # noqa: E402
from db.database import init_db  # noqa: E402

init_db()

GOLDENS = json.loads((ROOT / "tests" / "evals" / "goldens" / "essays.json").read_text(encoding="utf-8"))

N_ITEMS = 12  # cycle through the 6 goldens twice; unique markers keep the cache from short-circuiting


def _build_compositions(job_tag: str) -> list[CompositionItem]:
    items = []
    for i in range(N_ITEMS):
        essay = GOLDENS[i % len(GOLDENS)]
        items.append(CompositionItem(
            composition_id=f"{job_tag}-c{i}",
            document_id=f"{job_tag}-doc{i}",
            text=f"{essay['text']} (submission marker #{i}, job {job_tag})",
        ))
    return items


async def _run_job(concurrency: int) -> float:
    grader = GradingEngine()
    # similarity_threshold > 1.0 is unreachable, so cache.get() can only ever
    # return an exact hit -- and every item's fingerprint is unique (distinct
    # marker text). This isolates grading concurrency from cache behavior;
    # without it, reusing the 6 goldens twice makes the second occurrence of
    # each English essay a *fuzzy* cache hit against the first (the marker
    # text is too small relative to the essay to drop Jaccard below 0.95),
    # which would silently skip grading for those items and understate what
    # concurrency actually buys you.
    cache = InMemoryCache(similarity_threshold=1.01)
    engine = BatchEngine(grader, cache)

    job_tag = f"conc{concurrency}"
    request = BatchSubmitRequest(
        job_id=f"bench-{job_tag}-{int(time.time())}",
        compositions=_build_compositions(job_tag),
        concurrency=concurrency,
    )

    t0 = time.perf_counter()
    job = engine.submit(request)
    while job.status != "completed":
        await asyncio.sleep(0.05)
    elapsed = time.perf_counter() - t0

    statuses = [r.status for r in job.results]
    print(f"concurrency={concurrency:>2d}  total={job.total}  "
          f"success={statuses.count('success')}  failed={statuses.count('failed')}  "
          f"elapsed={elapsed:.2f}s")
    return elapsed


async def main() -> None:
    serial_time = await _run_job(concurrency=1)
    concurrent_time = await _run_job(concurrency=10)

    print()
    print(f"serial   (concurrency=1):  {serial_time:.2f}s")
    print(f"parallel (concurrency=10): {concurrent_time:.2f}s")
    if concurrent_time > 0:
        print(f"speedup: {serial_time / concurrent_time:.2f}x")


if __name__ == "__main__":
    asyncio.run(main())
