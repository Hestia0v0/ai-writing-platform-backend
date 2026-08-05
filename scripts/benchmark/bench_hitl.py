"""
Measures how much of the golden essay set the grading engine auto-flags for
human review vs. auto-approves. Uses tests/evals/goldens/essays.json.

Run (from ai-writing-platform-backend/):
    pip install -r backend/ai_inference/requirements.txt
    python scripts/resume_bench/bench_hitl.py

Set DEEPSEEK_API_KEY for real grading; without it, the deterministic mock
grader is used (still exercises the same confidence/edge-score flagging
logic in grader.py, just with synthetic scores derived from text hashes).

Caveat: the golden set only has 6 essays. That's enough to sanity-check the
flagging logic and print a directional number, but treat any percentage
from this run as illustrative, not a statistically solid resume claim --
widen tests/evals/goldens/essays.json first if you want a defensible N.
"""
import asyncio
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite:///./_bench_scratch.db")

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "ai_inference"))

from core.grader import GradingEngine  # noqa: E402
from db.database import init_db  # noqa: E402

init_db()

GOLDENS = json.loads((ROOT / "tests" / "evals" / "goldens" / "essays.json").read_text(encoding="utf-8"))


async def main() -> None:
    grader = GradingEngine()

    flagged = 0
    by_tier = defaultdict(lambda: {"total": 0, "flagged": 0})

    print(f"{'id':16s} {'tier':8s} {'score':>6s} {'conf':>6s} {'flagged':>8s}  reason")
    for essay in GOLDENS:
        result = await grader.grade(document_id=essay["id"], text=essay["text"])
        by_tier[essay["tier"]]["total"] += 1
        if result.flagged_for_review:
            flagged += 1
            by_tier[essay["tier"]]["flagged"] += 1
        reason = result.flag_reason.value if result.flag_reason else "-"
        print(f"{essay['id']:16s} {essay['tier']:8s} {result.score:6.1f} "
              f"{result.confidence:6.3f} {str(result.flagged_for_review):>8s}  {reason}")

    total = len(GOLDENS)
    print()
    print(f"auto-flagged for human review: {flagged}/{total} = {flagged / total:.1%}")
    print(f"auto-approved (no review needed): {total - flagged}/{total} = {(total - flagged) / total:.1%}")
    print()
    print("by tier:")
    for tier, stats in by_tier.items():
        print(f"  {tier:8s} flagged {stats['flagged']}/{stats['total']}")


if __name__ == "__main__":
    asyncio.run(main())
