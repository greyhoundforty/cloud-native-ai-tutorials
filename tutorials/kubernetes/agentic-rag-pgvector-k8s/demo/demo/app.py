"""
app.py — FastAPI service for agentic RAG using Claude API + pgvector.

Endpoints:
  POST /upload         Upload a PDF for ingestion
  POST /query          Single-turn RAG query
  POST /chat           Multi-turn agentic conversation with tool use
  GET  /health         Liveness probe
  GET  /ready          Readiness probe (checks DB connection)
"""

import json
import os
import tempfile
from pathlib import Path

import anthropic
import psycopg2
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sentence_transformers import SentenceTransformer

# ── Config ─────────────────────────────────────────────────────────────────────
DB_URL = os.getenv("DATABASE_URL", "postgresql://rag:rag@localhost:5432/rag")
EMBED_MODEL = os.getenv("EMBED_MODEL", "all-MiniLM-L6-v2")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
TOP_K = int(os.getenv("TOP_K", "5"))
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "2048"))

# ── App bootstrap ──────────────────────────────────────────────────────────────
app = FastAPI(title="Agentic RAG — Claude + pgvector", version="1.0.0")

_embedder: SentenceTransformer | None = None
_client: anthropic.Anthropic | None = None


def get_embedder() -> SentenceTransformer:
    global _embedder
    if _embedder is None:
        _embedder = SentenceTransformer(EMBED_MODEL)
    return _embedder


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def get_db():
    return psycopg2.connect(DB_URL)


# ── Vector search ──────────────────────────────────────────────────────────────

def vector_search(query: str, top_k: int = TOP_K) -> list[dict]:
    """Embed query and retrieve nearest chunks from pgvector."""
    embedder = get_embedder()
    q_vec = embedder.encode(query).tolist()

    conn = get_db()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT source, page, content,
                       1 - (embedding <=> %s::vector) AS score
                FROM   documents
                ORDER  BY embedding <=> %s::vector
                LIMIT  %s
                """,
                (q_vec, q_vec, top_k),
            )
            rows = cur.fetchall()
    finally:
        conn.close()

    return [
        {"source": r[0], "page": r[1], "content": r[2], "score": float(r[3])}
        for r in rows
    ]


# ── Claude tool definitions ────────────────────────────────────────────────────

SEARCH_TOOL = {
    "name": "search_documents",
    "description": (
        "Search the document knowledge base for chunks relevant to a query. "
        "Returns the top matching passages with source and page references. "
        "Call this multiple times with different queries to gather comprehensive context."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Semantic search query to find relevant document passages",
            },
            "top_k": {
                "type": "integer",
                "description": "Number of results to return (1–10, default 5)",
                "default": TOP_K,
            },
        },
        "required": ["query"],
    },
}


# ── Request/response models ────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    question: str
    top_k: int = TOP_K


class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    max_tool_calls: int = 6  # guard against runaway loops


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    try:
        conn = get_db()
        conn.close()
        return {"status": "ready"}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


@app.post("/upload")
async def upload_pdf(file: UploadFile = File(...)):
    """Accept a PDF upload and trigger ingestion into pgvector."""
    if not file.filename.endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    # Import here to keep startup fast when ingest deps are present
    from ingest import ensure_schema, ingest_pdf

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)

    try:
        conn = get_db()
        ensure_schema(conn)
        n = ingest_pdf(tmp_path, get_embedder(), conn)
        conn.close()
    finally:
        tmp_path.unlink(missing_ok=True)

    return {"filename": file.filename, "chunks_stored": n}


@app.post("/query")
def single_query(req: QueryRequest):
    """Single-turn RAG: retrieve context then ask Claude for an answer."""
    chunks = vector_search(req.question, req.top_k)
    if not chunks:
        raise HTTPException(status_code=404, detail="No documents ingested yet")

    context = "\n\n---\n\n".join(
        f"[{c['source']} p.{c['page']}]\n{c['content']}" for c in chunks
    )
    system = (
        "You are a helpful assistant that answers questions based strictly on the "
        "provided document context. Cite source and page when referencing a passage. "
        "If the context does not contain enough information, say so clearly."
    )
    user_msg = f"Context:\n{context}\n\nQuestion: {req.question}"

    client = get_client()
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )
    answer = response.content[0].text
    return {
        "question": req.question,
        "answer": answer,
        "sources": [{"source": c["source"], "page": c["page"], "score": c["score"]} for c in chunks],
    }


@app.post("/chat")
def agentic_chat(req: ChatRequest):
    """
    Multi-turn agentic RAG using Claude's tool_use feature.

    Claude autonomously decides when and how many times to call search_documents,
    enabling it to refine queries and gather comprehensive context before answering.
    """
    client = get_client()
    system = (
        "You are a research assistant with access to a document knowledge base. "
        "Use the search_documents tool to find relevant passages before answering. "
        "You may search multiple times with different queries to build a complete picture. "
        "Always cite the source document and page number when referencing a passage. "
        "When you have gathered sufficient context, synthesise a clear, well-structured answer."
    )

    # Convert incoming messages to Anthropic format
    messages = [{"role": m.role, "content": m.content} for m in req.messages]

    tool_calls_made = 0

    # ── Agentic loop ──────────────────────────────────────────────────────────
    while tool_calls_made <= req.max_tool_calls:
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=MAX_TOKENS,
            system=system,
            tools=[SEARCH_TOOL],
            messages=messages,
        )

        # Append Claude's response to conversation
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            # Claude is done — extract final text
            final_text = next(
                (block.text for block in response.content if hasattr(block, "text")),
                "",
            )
            return {
                "answer": final_text,
                "tool_calls": tool_calls_made,
                "messages": [
                    {"role": m["role"], "content": _content_to_str(m["content"])}
                    for m in messages
                ],
            }

        if response.stop_reason == "tool_use":
            # Execute each tool call and feed results back
            tool_results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                tool_calls_made += 1
                tool_input = block.input
                query = tool_input.get("query", "")
                top_k = min(int(tool_input.get("top_k", TOP_K)), 10)

                results = vector_search(query, top_k)
                result_text = json.dumps(results, indent=2) if results else "No results found."
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                })

            messages.append({"role": "user", "content": tool_results})
        else:
            # Unexpected stop reason
            break

    # Max tool calls reached — return what we have
    final_text = next(
        (
            block.text
            for m in reversed(messages)
            if m["role"] == "assistant"
            for block in (m["content"] if isinstance(m["content"], list) else [])
            if hasattr(block, "text")
        ),
        "Reached maximum tool call limit without a final answer.",
    )
    return {"answer": final_text, "tool_calls": tool_calls_made}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _content_to_str(content) -> str:
    """Flatten Anthropic content blocks to a string for serialisation."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if hasattr(block, "text"):
                parts.append(block.text)
            elif isinstance(block, dict) and block.get("type") == "tool_result":
                parts.append(f"[tool_result: {block.get('content', '')}]")
        return " ".join(parts)
    return str(content)
