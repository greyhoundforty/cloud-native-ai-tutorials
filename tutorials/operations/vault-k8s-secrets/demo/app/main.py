"""
Secrets-aware FastAPI app for the Vault + K8s tutorial.

The app reads its Anthropic API key from a file mounted by the Vault Agent
Injector sidecar — NOT from an environment variable or K8s Secret.
The file path defaults to /vault/secrets/anthropic-key but can be
overridden with VAULT_SECRET_PATH for local development.
"""

import os
from pathlib import Path

import anthropic
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="vault-demo", version="1.0.0")

# Vault Agent Injector writes secrets to /vault/secrets/<filename>
VAULT_SECRET_PATH = os.getenv("VAULT_SECRET_PATH", "/vault/secrets/anthropic-key")


def _read_api_key() -> str:
    """Read the Anthropic API key from the Vault-injected secret file."""
    secret_file = Path(VAULT_SECRET_PATH)
    if not secret_file.exists():
        raise RuntimeError(
            f"Secret file not found at {VAULT_SECRET_PATH}. "
            "Is the Vault Agent Injector sidecar running?"
        )
    return secret_file.read_text().strip()


class SummarizeRequest(BaseModel):
    text: str


class SummarizeResponse(BaseModel):
    summary: str
    model: str
    secret_source: str


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/summarize", response_model=SummarizeResponse)
async def summarize(req: SummarizeRequest):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty")

    try:
        api_key = _read_api_key()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))

    client = anthropic.Anthropic(api_key=api_key)

    message = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=256,
        messages=[
            {
                "role": "user",
                "content": f"Summarize the following text in 2-3 sentences:\n\n{req.text}",
            }
        ],
    )

    return SummarizeResponse(
        summary=message.content[0].text,
        model=message.model,
        secret_source=VAULT_SECRET_PATH,
    )
