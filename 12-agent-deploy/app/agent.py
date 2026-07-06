"""Model selection and the review runner.

The model comes from the environment, in priority order:

1. OPENAI_BASE_URL set: use the OpenAI protocol against that endpoint.
   Point it at your in-cluster vLLM (Phase 04/06) and no hosted key is
   needed. This is the same base_url bridge you built in Phase 07 lab-04.
2. ANTHROPIC_API_KEY set: use the Anthropic API directly.

A missing model configuration raises at startup, on purpose: a Pod that
cannot work should crash early and loudly, where the kubelet can see it.
"""

import os

from strands import Agent

from prompts import REVIEW_PROMPT, SYSTEM_PROMPT
from tools import file_feedback, list_repo, read_repo_file, recent_commits

TOOLS = [list_repo, read_repo_file, recent_commits, file_feedback]


def build_model():
    base_url = os.getenv("OPENAI_BASE_URL")
    if base_url:
        from strands.models.openai import OpenAIModel

        return OpenAIModel(
            client_args={
                "api_key": os.getenv("OPENAI_API_KEY", "EMPTY"),  # vLLM ignores it; the client requires it
                "base_url": base_url,
            },
            model_id=os.getenv("MODEL_ID", "Qwen/Qwen2.5-0.5B-Instruct"),
            params={"max_tokens": 1024, "temperature": 0.7},
        )

    if os.getenv("ANTHROPIC_API_KEY"):
        from strands.models.anthropic import AnthropicModel

        return AnthropicModel(
            client_args={"api_key": os.getenv("ANTHROPIC_API_KEY")},
            model_id=os.getenv("MODEL_ID", "claude-haiku-4-5-20251001"),
            max_tokens=4000,
        )

    raise RuntimeError("set OPENAI_BASE_URL (vLLM) or ANTHROPIC_API_KEY")


MODEL = build_model()


def run_review() -> str:
    """One stateless review pass. A fresh agent per call reads the repo as it is now."""
    agent = Agent(model=MODEL, system_prompt=SYSTEM_PROMPT, tools=TOOLS, name="review-agent")
    return str(agent(REVIEW_PROMPT)).strip()


def chat(text: str) -> str:
    """One conversational turn about the repository, used by the Discord listener."""
    agent = Agent(model=MODEL, system_prompt=SYSTEM_PROMPT, tools=TOOLS, name="review-agent")
    return str(agent(text)).strip()
