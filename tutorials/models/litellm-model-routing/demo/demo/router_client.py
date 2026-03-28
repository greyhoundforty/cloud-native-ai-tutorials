"""
LiteLLM proxy routing demo.

Routes requests to the cheapest capable model and falls back
automatically on 429 / 5xx errors.

Usage:
    export LITELLM_PROXY_URL=http://localhost:4000
    export LITELLM_API_KEY=sk-my-master-key
    python router_client.py
"""

import os
import sys
import time
from openai import OpenAI

PROXY_URL = os.environ.get("LITELLM_PROXY_URL", "http://localhost:4000")
API_KEY = os.environ.get("LITELLM_API_KEY", "sk-test")

client = OpenAI(base_url=PROXY_URL, api_key=API_KEY)


# ---------------------------------------------------------------------------
# Task definitions
# ---------------------------------------------------------------------------

CLASSIFICATION_PROMPT = """\
Classify the following customer message into exactly one category.
Reply with ONLY the category name, nothing else.

Categories: billing, technical_support, feature_request, other

Message: "I was charged twice for my subscription this month."
"""

GENERATION_PROMPT = """\
Write a two-paragraph technical blog introduction about why developers
should use a unified LLM proxy instead of calling provider APIs directly.
Be specific about the operational benefits.
"""


# ---------------------------------------------------------------------------
# Routing helpers
# ---------------------------------------------------------------------------

def classify(text: str) -> str:
    """Short classification task — routed to Haiku (cheap, fast)."""
    resp = client.chat.completions.create(
        model="claude-haiku",
        messages=[{"role": "user", "content": text}],
        temperature=0,
    )
    return resp.choices[0].message.content.strip()


def generate(prompt: str) -> str:
    """Long-form generation — routed to Sonnet (more capable)."""
    resp = client.chat.completions.create(
        model="claude-sonnet",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )
    return resp.choices[0].message.content.strip()


def smart_route(prompt: str, task_type: str = "generation") -> dict:
    """
    Use the smart-router alias, which lets LiteLLM decide.
    Returns the response plus which backend model actually handled it.
    """
    resp = client.chat.completions.create(
        model="smart-router",
        messages=[{"role": "user", "content": prompt}],
        extra_headers={"x-task-type": task_type},
    )
    return {
        "content": resp.choices[0].message.content.strip(),
        "model_used": resp.model,
        "usage": {
            "prompt_tokens": resp.usage.prompt_tokens,
            "completion_tokens": resp.usage.completion_tokens,
        },
    }


# ---------------------------------------------------------------------------
# Fallback demo
# ---------------------------------------------------------------------------

def demo_fallback() -> None:
    """
    Simulate a fallback by requesting a deliberately bad model name,
    showing the proxy's error handling. Then send the same request
    through the smart-router alias that has fallbacks configured.
    """
    print("\n[fallback demo] Sending request to a model that will fail...")
    try:
        client.chat.completions.create(
            model="nonexistent-model",
            messages=[{"role": "user", "content": "Hello"}],
        )
    except Exception as exc:
        print(f"  Expected error: {exc}\n")

    print("[fallback demo] Same request via smart-router (fallback chain active)...")
    result = smart_route("Say hello in one sentence.", task_type="classification")
    print(f"  Handled by: {result['model_used']}")
    print(f"  Response: {result['content']}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("=" * 60)
    print("LiteLLM Proxy Routing Demo")
    print(f"Proxy: {PROXY_URL}")
    print("=" * 60)

    # 1. Classification → Haiku
    print("\n[1] Classification task → claude-haiku")
    t0 = time.perf_counter()
    category = classify(CLASSIFICATION_PROMPT)
    elapsed = time.perf_counter() - t0
    print(f"  Category : {category}")
    print(f"  Latency  : {elapsed:.2f}s")

    # 2. Generation → Sonnet
    print("\n[2] Generation task → claude-sonnet")
    t0 = time.perf_counter()
    blog_intro = generate(GENERATION_PROMPT)
    elapsed = time.perf_counter() - t0
    print(f"  Latency  : {elapsed:.2f}s")
    print(f"  Output   :\n{blog_intro[:300]}...")

    # 3. Smart router
    print("\n[3] Smart router (LiteLLM picks the model)")
    result = smart_route("Summarise the benefits of model routing in 3 bullet points.")
    print(f"  Model used      : {result['model_used']}")
    print(f"  Prompt tokens   : {result['usage']['prompt_tokens']}")
    print(f"  Completion tokens: {result['usage']['completion_tokens']}")
    print(f"  Output:\n{result['content']}")

    # 4. Fallback demo
    demo_fallback()

    print("\nDone.")


if __name__ == "__main__":
    main()
