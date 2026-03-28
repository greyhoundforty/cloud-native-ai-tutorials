"""Anthropic client setup with env-var and keyring fallback for the API key."""

import os

import anthropic

SERVICE_NAME = "codeqa"


def get_api_key() -> str:
    """Return the Anthropic API key, preferring the environment variable."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key

    try:
        import keyring  # type: ignore

        key = keyring.get_password(SERVICE_NAME, "api_key")
        if key:
            return key
    except Exception:
        pass

    raise RuntimeError(
        "No Anthropic API key found.\n"
        "  Option 1: export ANTHROPIC_API_KEY=sk-...\n"
        "  Option 2: run 'codeqa config set-key'"
    )


def make_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=get_api_key())
