"""
LLM-backed summarizer app — demo for Helm tutorial.

POST /summarize   { "text": "..." }  → { "summary": "..." }
GET  /healthz                        → { "status": "ok" }
"""

import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import anthropic

app = FastAPI(title="llm-summarizer", version="1.0.0")
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "256"))


class SummarizeRequest(BaseModel):
    text: str


class SummarizeResponse(BaseModel):
    summary: str
    model: str


@app.get("/healthz")
def health():
    return {"status": "ok"}


@app.post("/summarize", response_model=SummarizeResponse)
def summarize(req: SummarizeRequest):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text must not be empty")

    message = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[
            {
                "role": "user",
                "content": (
                    "Summarize the following text in 2-3 sentences. "
                    "Reply with the summary only.\n\n"
                    f"{req.text}"
                ),
            }
        ],
    )

    summary = message.content[0].text
    return SummarizeResponse(summary=summary, model=MODEL)
