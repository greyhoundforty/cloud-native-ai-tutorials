"""
Claude API demo service.

GET  /healthz           → { "status": "ok" }
POST /ask               → { "question": "..." } → { "answer": "...", "model": "..." }
"""

import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import anthropic

app = FastAPI(title="claude-k8s-demo", version="1.0.0")
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "512"))


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    answer: str
    model: str


@app.get("/healthz")
def health():
    return {"status": "ok"}


@app.post("/ask", response_model=AskResponse)
def ask(req: AskRequest):
    if not req.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")

    message = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        messages=[{"role": "user", "content": req.question}],
    )

    return AskResponse(answer=message.content[0].text, model=MODEL)
