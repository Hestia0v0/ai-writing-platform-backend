"""
DeepEval judge-model wrapper around Claude (Anthropic).

Reuses the same ANTHROPIC_API_KEY the agents service already loads as its
cross-provider fallback (see infrastructure/.env.example). The judge is
deliberately a different provider than DeepSeek, which powers both grading
tiers under test (ai_inference/grader.py and agents' EVAL_MODEL), so the
judge is never scoring outputs produced by its own model family.
"""
import os

from deepeval.models import DeepEvalBaseLLM
from langchain_anthropic import ChatAnthropic

_DEFAULT_JUDGE_MODEL = os.getenv("DEEPEVAL_JUDGE_MODEL", "claude-haiku-4-5")


class ClaudeJudge(DeepEvalBaseLLM):
    def __init__(self, model_name: str = _DEFAULT_JUDGE_MODEL) -> None:
        self.model_name = model_name
        self._client = ChatAnthropic(model=model_name, temperature=0)

    def load_model(self):
        return self._client

    def generate(self, prompt: str) -> str:
        return self.load_model().invoke(prompt).content

    async def a_generate(self, prompt: str) -> str:
        response = await self.load_model().ainvoke(prompt)
        return response.content

    def get_model_name(self) -> str:
        return self.model_name


def get_judge() -> ClaudeJudge:
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set — required for the deepeval judge model. "
            "See infrastructure/.env.example."
        )
    return ClaudeJudge()
