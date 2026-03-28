---
tags:
  - kubernetes
  - rag
  - pgvector
  - claude-api
---

# Agentic RAG with Claude API and pgvector on Kubernetes

Build a production-grade question-answering service that lets Claude autonomously search your document library — deployed on Kubernetes with Helm.

---

## What You'll Build

A containerised FastAPI app that:

1. **Ingests PDFs** — chunks text, generates embeddings with `sentence-transformers`, stores vectors in `pgvector`.
2. **Answers questions** — retrieves the most relevant chunks and sends them to `claude-sonnet-4-6` for synthesis.
3. **Runs an agentic loop** — uses Claude's native tool-use to let the model decide _when_ and _how many times_ to search the knowledge base before answering.
4. **Runs on Kubernetes** — packaged as a Helm chart with a bundled pgvector-enabled PostgreSQL dependency.

### Architecture

```
┌─────────────┐     POST /chat      ┌──────────────────────────────────────┐
│   Client    │ ──────────────────► │           FastAPI (app.py)           │
└─────────────┘                     │                                      │
                                    │  ┌─────────────────────────────────┐ │
                                    │  │       Agentic Loop              │ │
                                    │  │                                 │ │
                                    │  │  claude-sonnet-4-6              │ │
                                    │  │        │ tool_use               │ │
                                    │  │        ▼                        │ │
                                    │  │  search_documents()             │ │
                                    │  │        │                        │ │
                                    │  │        ▼                        │ │
                                    │  │  pgvector (cosine search) ◄─────┼─┼── PDF ingest
                                    │  │        │                        │ │
                                    │  │        └── tool_result ──►      │ │
                                    │  │  (repeat until end_turn)        │ │
                                    │  └─────────────────────────────────┘ │
                                    └──────────────────────────────────────┘
```

---

## Prerequisites

| Tool | Version |
|------|---------|
| Docker | 24+ |
| kubectl | 1.28+ |
| Helm | 3.14+ |
| A Kubernetes cluster | any — kind/k3s/EKS/GKE all work |
| Anthropic API key | [console.anthropic.com](https://console.anthropic.com) |

---

## Part 1 — The Data Pipeline (`ingest.py`)

### Why chunking matters

Large language models have context windows, and vector similarity only works well on semantically coherent passages. The goal is to chunk each PDF page into ~512-character segments with a small overlap so no sentence is cut in half.

```python
def chunk_text(text: str, size: int = 512, overlap: int = 64) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        # Prefer sentence boundaries
        if end < len(text):
            boundary = text.rfind(". ", start, end)
            if boundary != -1 and boundary > start + overlap:
                end = boundary + 1
        chunks.append(text[start:end].strip())
        start = end - overlap
    return [c for c in chunks if c]
```

### The pgvector schema

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE documents (
    id          BIGSERIAL PRIMARY KEY,
    source      TEXT NOT NULL,
    page        INT  NOT NULL,
    chunk_index INT  NOT NULL,
    content     TEXT NOT NULL,
    embedding   VECTOR(384)   -- matches all-MiniLM-L6-v2 output
);

-- IVFFlat index: fast approximate nearest-neighbour search
CREATE INDEX documents_embedding_idx
    ON documents USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
```

The `VECTOR(384)` column stores the 384-dimensional embedding produced by `all-MiniLM-L6-v2`. The IVFFlat index clusters the space into 100 lists — a sensible default for up to ~1 million chunks.

### Batch embedding

Calling the embedding model once per chunk is slow. Batch-process all chunks from a document in a single call:

```python
texts = [r[3] for r in rows]                           # all chunk text
embeddings = model.encode(texts, batch_size=64, ...)    # one forward pass per 64
```

### Ingesting a document

```bash
# Ingest a single file
python ingest.py --file report.pdf

# Ingest a whole directory
python ingest.py --dir ./docs/
```

---

## Part 2 — The API Service (`app.py`)

### Single-turn RAG (`POST /query`)

The simplest path: embed the question, fetch the top-K chunks, build a context string, call Claude.

```python
@app.post("/query")
def single_query(req: QueryRequest):
    chunks = vector_search(req.question, req.top_k)
    context = "\n\n---\n\n".join(
        f"[{c['source']} p.{c['page']}]\n{c['content']}" for c in chunks
    )
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2048,
        system="Answer based strictly on the provided context. Cite source and page.",
        messages=[{"role": "user", "content": f"Context:\n{context}\n\nQuestion: {req.question}"}],
    )
    return {"answer": response.content[0].text, "sources": chunks}
```

This works, but it retrieves chunks blindly. Claude has no way to refine the search if the first query doesn't capture the full picture.

### Agentic RAG (`POST /chat`) — the interesting part

The agentic endpoint hands Claude a tool and lets it decide how to use it.

#### Define the tool

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

#### The agentic loop

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
        # Claude decided it has enough context — extract the answer
        return extract_final_answer(messages)

    if response.stop_reason == "tool_use":
        # Execute each search the model requested
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

**What makes this "agentic":**

- Claude chooses _when_ to search (not every turn).
- Claude formulates the search queries itself — often issuing two or three different phrasings of a question to triangulate the answer.
- Claude decides _when it has enough_ and transitions to `end_turn`.
- A `max_tool_calls` guard prevents runaway loops while still giving the model enough turns to be useful.

#### Example interaction

```bash
curl -X POST http://localhost:8000/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "messages": [
      {"role": "user", "content": "What were the key findings about GPU memory efficiency in the 2024 benchmarks?"}
    ]
  }'
```

Behind the scenes, Claude might:

1. Call `search_documents("GPU memory efficiency 2024 benchmarks")`
2. Review results, notice a related term, call `search_documents("HBM3 bandwidth utilisation inference")`
3. Synthesise both result sets into a cited answer.

---

## Part 3 — Containerising the App

### Multi-stage Dockerfile

```dockerfile
FROM python:3.12-slim AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y gcc libpq-dev
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim
WORKDIR /app
RUN apt-get update && apt-get install -y libpq5
COPY --from=builder /install /usr/local
COPY app.py ingest.py ./
# Pre-bake the embedding model weights — avoids cold-start download
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
RUN useradd -r -u 1001 appuser && chown -R appuser /app
USER appuser
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
```

Key decisions:

- **Multi-stage** keeps the runtime image lean (no `gcc`, no build cache).
- **Model baked in** means the pod is immediately ready; no download on startup.
- **Non-root user** (`appuser`, uid 1001) satisfies most admission controllers.

### Build and push

```bash
export IMAGE=ghcr.io/yourorg/rag-service:1.0.0
docker build -t $IMAGE ./demo/
docker push $IMAGE
```

---

## Part 4 — Kubernetes Deployment with Helm

The Helm chart lives in `demo/helm/`. It bundles the RAG service and a `pgvector`-enabled PostgreSQL via the Bitnami sub-chart.

### Chart layout

```
helm/
├── Chart.yaml          # metadata + postgresql sub-chart dependency
├── values.yaml         # all tunables
└── templates/
    ├── _helpers.tpl    # name helpers
    ├── deployment.yaml
    ├── service.yaml
    ├── configmap.yaml  # non-secret env vars
    └── secret.yaml     # ANTHROPIC_API_KEY + DATABASE_URL
```

### Installing

```bash
# 1. Fetch sub-chart dependencies
helm dependency update ./demo/helm/

# 2. Install with your Anthropic key
helm install rag-service ./demo/helm/ \
  --namespace rag \
  --create-namespace \
  --set anthropic.apiKey="sk-ant-..." \
  --set image.repository="ghcr.io/yourorg/rag-service" \
  --set image.tag="1.0.0"
```

### Verifying the deployment

```bash
# Watch pods come up
kubectl get pods -n rag -w

# Check logs
kubectl logs -n rag -l app.kubernetes.io/name=rag-service -f

# Port-forward for local testing
kubectl port-forward -n rag svc/rag-service 8080:80
```

### Ingesting documents from inside the cluster

The recommended pattern is a Kubernetes Job:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: ingest-docs
  namespace: rag
spec:
  template:
    spec:
      restartPolicy: OnFailure
      containers:
        - name: ingest
          image: ghcr.io/yourorg/rag-service:1.0.0
          command: ["python", "ingest.py", "--dir", "/docs"]
          env:
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: rag-service-db
                  key: DATABASE_URL
          volumeMounts:
            - name: docs
              mountPath: /docs
      volumes:
        - name: docs
          configMap:
            name: my-pdf-docs     # or a PVC
```

For large document sets, mount a PersistentVolumeClaim and copy PDFs in with `kubectl cp`.

### Production tweaks

**Horizontal Pod Autoscaler**

The API service is stateless — scale it freely:

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: rag-service
  namespace: rag
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: rag-service
  minReplicas: 2
  maxReplicas: 10
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 60
```

**External secrets**

Never store `ANTHROPIC_API_KEY` in a plain Kubernetes Secret in production. Use [External Secrets Operator](https://external-secrets.io/) with AWS Secrets Manager, Vault, or GCP Secret Manager:

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: anthropic-key
  namespace: rag
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: aws-secretsmanager
    kind: ClusterSecretStore
  target:
    name: rag-service-anthropic
  data:
    - secretKey: ANTHROPIC_API_KEY
      remoteRef:
        key: prod/rag-service/anthropic
        property: api_key
```

**pgvector on a managed database**

For production, replace the bundled PostgreSQL with a managed instance (RDS, CloudSQL, etc.) that has the `pgvector` extension available. Set `postgresql.enabled=false` and provide `externalDatabase.*` values:

```bash
helm upgrade rag-service ./demo/helm/ \
  --set postgresql.enabled=false \
  --set externalDatabase.host=mydb.us-east-1.rds.amazonaws.com \
  --set externalDatabase.password=... \
  --set anthropic.apiKey=...
```

---

## Part 5 — Multi-turn Demo

With the service running, here's what a multi-turn conversation looks like:

```bash
BASE=http://localhost:8080

# Turn 1 — broad question
curl -s -X POST $BASE/chat \
  -H 'Content-Type: application/json' \
  -d '{"messages": [{"role": "user", "content": "Summarise the methodology section."}]}' \
  | jq .answer

# Turn 2 — follow-up
curl -s -X POST $BASE/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "messages": [
      {"role": "user", "content": "Summarise the methodology section."},
      {"role": "assistant", "content": "The methodology used a three-stage pipeline..."},
      {"role": "user", "content": "Were there any limitations mentioned for stage 2?"}
    ]
  }' | jq .answer
```

On the second turn, Claude will issue a targeted search for "stage 2 limitations" rather than re-fetching a general methodology overview — that's the agentic behaviour in practice.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| Pod stuck in `Init:0/1` | PostgreSQL not ready | `kubectl describe pod` — check init container logs |
| `503 Service Unavailable` on `/ready` | DB connection refused | Check `DATABASE_URL` secret, verify pgvector pod is running |
| Empty search results | No documents ingested | Run the ingest Job or `POST /upload` with a PDF |
| `tool_calls` hits max | Document set too fragmented | Increase `max_tool_calls` or improve chunking strategy |
| Slow cold start | Embedding model downloading | Pre-bake model in Docker image (already done in the provided Dockerfile) |

---

## What's Next

- **Streaming responses** — swap `client.messages.create` for `client.messages.stream` and use FastAPI's `StreamingResponse`.
- **Namespace isolation** — run one instance per customer using Kubernetes namespaces + pgvector schemas.
- **Re-ranking** — add a cross-encoder re-ranker between vector search and Claude to improve retrieval precision.
- **Observability** — instrument with OpenTelemetry; trace each tool call as a span to understand retrieval latency.
- **Hybrid search** — combine pgvector cosine similarity with PostgreSQL full-text search using `tsvector` for keyword fallback.

---

## Repository Layout

```
agentic-rag-pgvector-k8s/
├── README.md              ← this file
└── demo/
    ├── ingest.py          ← PDF → chunks → pgvector
    ├── app.py             ← FastAPI + agentic Claude loop
    ├── requirements.txt
    ├── Dockerfile
    └── helm/
        ├── Chart.yaml
        ├── values.yaml
        └── templates/
            ├── _helpers.tpl
            ├── deployment.yaml
            ├── service.yaml
            ├── configmap.yaml
            └── secret.yaml
```
