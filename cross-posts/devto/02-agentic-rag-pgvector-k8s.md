---
title: "Agentic RAG with Claude API and pgvector on Kubernetes"
published: false
description: "Build a production-grade Q&A service where Claude autonomously decides when and how to search your document library — deployed with Helm and a bundled pgvector PostgreSQL."
tags: ["kubernetes", "ai", "python", "rag"]
canonical_url: "https://greyhoundforty-tutorials.netlify.app/tutorials/kubernetes/agentic-rag-pgvector-k8s/"
---

Standard RAG retrieves chunks mechanically — embed the query, fetch top-K, stuff into the prompt. It works, but the model can't refine the search if the first query misses. **Agentic RAG** hands Claude a search tool and lets it decide when to call it, how many times, and with what query — until it has enough context to answer confidently.

This post walks through the key parts of a production-grade implementation: a FastAPI service with a pgvector-backed knowledge base, deployed on Kubernetes via Helm.

## Architecture

```
Client → POST /chat → FastAPI
                         └── Agentic Loop
                               claude-sonnet-4-6
                                    ↓ tool_use
                               search_documents()
                                    ↓
                               pgvector (cosine search) ← PDF ingest
                                    ↓ tool_result
                               (repeat until end_turn)
```

## The data pipeline

Each PDF is chunked into ~512-character segments with overlap, embedded with `all-MiniLM-L6-v2`, and stored in pgvector:

```python
def chunk_text(text: str, size: int = 512, overlap: int = 64) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = text.rfind(". ", start, end)
            if boundary != -1 and boundary > start + overlap:
                end = boundary + 1
        chunks.append(text[start:end].strip())
        start = end - overlap
    return [c for c in chunks if c]
```

The pgvector schema uses an IVFFlat index for fast approximate nearest-neighbour search:

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE documents (
    id          BIGSERIAL PRIMARY KEY,
    source      TEXT NOT NULL,
    page        INT  NOT NULL,
    chunk_index INT  NOT NULL,
    content     TEXT NOT NULL,
    embedding   VECTOR(384)
);

CREATE INDEX documents_embedding_idx
    ON documents USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
```

## The agentic loop

Define the search tool:

```python
SEARCH_TOOL = {
    "name": "search_documents",
    "description": (
        "Search the document knowledge base for chunks relevant to a query. "
        "Call multiple times with different queries to build comprehensive context."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer", "default": 5},
        },
        "required": ["query"],
    },
}
```

Then let Claude drive:

```python
while tool_calls_made <= req.max_tool_calls:
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        tools=[SEARCH_TOOL],
        messages=messages,
    )
    messages.append({"role": "assistant", "content": response.content})

    if response.stop_reason == "end_turn":
        return extract_final_answer(messages)

    if response.stop_reason == "tool_use":
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                results = vector_search(block.input["query"], block.input.get("top_k", 5))
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(results),
                })
        messages.append({"role": "user", "content": tool_results})
        tool_calls_made += 1
```

What makes this "agentic": Claude chooses **when** to search, formulates the search queries itself (often multiple phrasings to triangulate), and decides **when it has enough** and moves to `end_turn`. The `max_tool_calls` guard prevents runaway loops.

## Deploying with Helm

The Helm chart bundles the RAG service and a pgvector-enabled PostgreSQL (via the Bitnami sub-chart):

```bash
helm dependency update ./demo/helm/

helm install rag-service ./demo/helm/ \
  --namespace rag \
  --create-namespace \
  --set anthropic.apiKey="sk-ant-..." \
  --set image.repository="ghcr.io/yourorg/rag-service" \
  --set image.tag="1.0.0"
```

Verify:

```bash
kubectl get pods -n rag -w
kubectl port-forward -n rag svc/rag-service 8080:80
```

Then ingest documents via a Kubernetes Job (see the full tutorial for the manifest) and query:

```bash
curl -X POST http://localhost:8080/chat \
  -H 'Content-Type: application/json' \
  -d '{"messages": [{"role": "user", "content": "What were the key findings on GPU memory efficiency?"}]}'
```

Behind the scenes Claude might issue two or three searches with different phrasings before synthesising a cited answer.

## Production considerations

- **HPA**: the API service is stateless — scale it freely with a HorizontalPodAutoscaler
- **External secrets**: don't store `ANTHROPIC_API_KEY` in a plain K8s Secret; use External Secrets Operator with AWS Secrets Manager or Vault
- **Managed pgvector**: swap the bundled PostgreSQL for RDS or CloudSQL with the pgvector extension for production workloads

## What's next

- **Streaming**: swap `messages.create` for `messages.stream` and use FastAPI's `StreamingResponse`
- **Re-ranking**: add a cross-encoder between vector search and Claude to improve retrieval precision
- **Hybrid search**: combine pgvector cosine similarity with PostgreSQL full-text search for keyword fallback

---

→ **Full tutorial + demo code:** [greyhoundforty-tutorials.netlify.app](https://greyhoundforty-tutorials.netlify.app/tutorials/kubernetes/agentic-rag-pgvector-k8s/)
