---
tags:
  - litellm
  - routing
  - fallback
  - multi-provider
---

# Model Routing and Fallback with LiteLLM Proxy

> **Audience:** Developers who call LLM APIs directly today and want to add cost control, reliability, and multi-provider flexibility without rewriting their clients.
> **Goal:** By the end you'll have a LiteLLM proxy running locally that routes classification tasks to Claude Haiku, generation tasks to Claude Sonnet, and falls back to a local Ollama instance when the Anthropic API is unavailable.

---

## The Multi-Provider Problem

If your app calls an LLM API today, you've already felt this:

- Anthropic, OpenAI, Bedrock, and Ollama each have different SDKs, auth schemes, and error formats.
- You pay Sonnet/Opus prices for tasks that Haiku could handle at 10× lower cost.
- A single `429` or provider outage takes down your feature entirely.
- Tracking which model used how many tokens requires custom logging in every service.

The usual fixes — try/except retry blocks, conditional model selection logic, per-provider SDK wrappers — scatter routing decisions across your codebase and are painful to change.

**LiteLLM proxy** solves this with a single OpenAI-compatible endpoint in front of all your providers. Your app code never changes; the proxy handles routing, retries, and fallback.

---

## Prerequisites

| Tool | Purpose | Min version |
|---|---|---|
| [Docker](https://docs.docker.com/get-docker/) | Run the proxy container | 24+ |
| [Python](https://python.org) | Run the demo client | 3.11+ |
| [kubectl](https://kubernetes.io/docs/tasks/tools/) | Apply K8s manifests (optional) | 1.29+ |
| [Ollama](https://ollama.com) | Local fallback model | 0.3+ |

You'll also need an **Anthropic API key** from [console.anthropic.com](https://console.anthropic.com/).

Pull the Ollama model you'll use as a fallback:

```bash
ollama pull llama3.2
```

---

## Project Layout

```
litellm-proxy-routing/
├── config/
│   └── litellm_config.yaml   # proxy model list and router settings
├── demo/
│   ├── router_client.py      # routing demo (classification, generation, fallback)
│   └── requirements.txt
└── k8s/
    └── deployment.yaml       # Deployment + Service + ConfigMap + HPA
```

---

## Part 1 — LiteLLM Proxy Setup

LiteLLM proxy is a single Docker image that speaks OpenAI's API on the outside and any provider's API on the inside. Start it with a config file and a master key.

### Run the proxy locally

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export LITELLM_MASTER_KEY=sk-my-dev-key   # arbitrary string for local use

docker run -d \
  --name litellm \
  -p 4000:4000 \
  -v "$(pwd)/config:/app/config" \
  -e ANTHROPIC_API_KEY \
  -e LITELLM_MASTER_KEY \
  ghcr.io/berriai/litellm:main-latest \
  --config /app/config/litellm_config.yaml --port 4000
```

Check the proxy is healthy:

```bash
curl http://localhost:4000/health/liveliness
# {"status":"healthy"}

curl http://localhost:4000/v1/models \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" | jq '.data[].id'
# "claude-haiku"
# "claude-sonnet"
# "ollama-llama3"
# "smart-router"
```

The proxy now exposes an OpenAI-compatible endpoint. Any client that works with `openai.OpenAI(base_url=...)` works unchanged.

---

## Part 2 — Router Configuration

Open `config/litellm_config.yaml`. The key sections are:

### Model list

```yaml
model_list:
  - model_name: claude-haiku          # alias your app uses
    litellm_params:
      model: anthropic/claude-haiku-4-5-20251001   # real provider model
      api_key: os.environ/ANTHROPIC_API_KEY
      max_tokens: 1024

  - model_name: claude-sonnet
    litellm_params:
      model: anthropic/claude-sonnet-4-6
      api_key: os.environ/ANTHROPIC_API_KEY
      max_tokens: 4096

  - model_name: ollama-llama3
    litellm_params:
      model: ollama/llama3.2
      api_base: http://ollama:11434   # adjust for your Ollama host
      max_tokens: 2048
```

`model_name` is what your client sends. `litellm_params.model` is what the proxy sends upstream. This decoupling lets you swap the underlying model without touching client code.

### Fallback chain

```yaml
  - model_name: smart-router
    litellm_params:
      model: claude-haiku              # primary
      fallbacks:
        - claude-sonnet                # 429 or 5xx on haiku → try sonnet
        - ollama-llama3                # 429 or 5xx on sonnet → try ollama
```

When you call `model="smart-router"`, LiteLLM tries each model in the fallback chain until one succeeds or all fail.

### Router settings

```yaml
router_settings:
  routing_strategy: simple-shuffle   # distribute load when multiple replicas share a model alias
  num_retries: 2
  retry_after: 5                     # seconds to wait before retrying a failed model
```

`simple-shuffle` round-robins across replicas of the same model alias. Switch to `least-busy` to direct traffic to the replica with the lowest in-flight request count.

---

## Part 3 — Cost-Aware Routing in Practice

The key insight: **you don't need AI to do AI routing**. A handful of explicit routing rules covers 80% of real workloads.

### Route by task type

```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:4000", api_key="sk-my-dev-key")

def classify(text: str) -> str:
    """Short, structured output — use Haiku."""
    resp = client.chat.completions.create(
        model="claude-haiku",
        messages=[{"role": "user", "content": text}],
        temperature=0,
    )
    return resp.choices[0].message.content.strip()

def generate(prompt: str) -> str:
    """Long-form, nuanced output — use Sonnet."""
    resp = client.chat.completions.create(
        model="claude-sonnet",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )
    return resp.choices[0].message.content.strip()
```

The proxy receives standard OpenAI calls. Your routing logic is a single `model=` argument — no provider SDKs, no per-provider error handling.

### Cost delta

| Task | Direct Sonnet | Via Haiku | Savings |
|---|---|---|---|
| Classify message (50 tokens) | $0.0015 | $0.00008 | ~95% |
| 1000 classifications/day | $1.50/day | $0.08/day | $526/year |

For workloads mixing classification and generation, routing classification to Haiku typically cuts the Anthropic bill by 40–60%.

---

## Part 4 — Reliability: Automatic Retry and Fallback

LiteLLM proxy handles these error scenarios automatically:

| Error | Behavior |
|---|---|
| `429 Too Many Requests` | Retry up to `num_retries` times, then try next model in fallback chain |
| `500 / 503` provider error | Immediate fallback to next model |
| Request timeout | Configurable per-model; falls back on expiry |
| All models exhausted | Returns the last error to the client |

You can verify fallback behavior with the demo:

```bash
cd demo
pip install -r requirements.txt
export LITELLM_PROXY_URL=http://localhost:4000
export LITELLM_API_KEY=sk-my-dev-key
python router_client.py
```

The `demo_fallback()` function in `router_client.py` intentionally calls a non-existent model to show the error, then re-sends via `smart-router` which recovers through the fallback chain.

Expected output:

```
[fallback demo] Sending request to a model that will fail...
  Expected error: Error code: 400 - ...

[fallback demo] Same request via smart-router (fallback chain active)...
  Handled by: claude-haiku-4-5-20251001
  Response: Hello! How can I help you today?
```

The `model_used` field in the response tells you which backend actually handled the request — useful for debugging and dashboards.

---

## Part 5 — Usage Tracking

LiteLLM exposes a `/spend` endpoint and a built-in dashboard at `http://localhost:4000/ui`.

### Per-model token usage (API)

```bash
curl http://localhost:4000/spend/logs \
  -H "Authorization: Bearer $LITELLM_MASTER_KEY" | jq '.[0:3]'
```

```json
[
  {
    "model": "claude-haiku-4-5-20251001",
    "total_tokens": 312,
    "spend": 0.0000249,
    "request_id": "chatcmpl-abc123"
  },
  ...
]
```

### Enable Langfuse for full observability

Add to `litellm_config.yaml` under `litellm_settings`:

```yaml
litellm_settings:
  success_callback: ["langfuse"]
  failure_callback: ["langfuse"]
```

Set the Langfuse env vars when starting the proxy:

```bash
docker run ... \
  -e LANGFUSE_PUBLIC_KEY=pk-lf-... \
  -e LANGFUSE_SECRET_KEY=sk-lf-... \
  ghcr.io/berriai/litellm:main-latest ...
```

Langfuse records the full request/response, model selected, latency, cost, and whether a fallback fired — without any changes to your client code.

### Budget guardrails

```yaml
litellm_settings:
  max_budget: 0.10          # USD
  budget_duration: "1min"   # rolling window
```

When the budget is exceeded, LiteLLM returns a `429` with a budget error. Useful for development environments where you want to avoid surprise bills.

---

## Part 6 — Deploy to Kubernetes

The `k8s/deployment.yaml` manifest includes a `Deployment`, `Service`, `ConfigMap`, and `HorizontalPodAutoscaler`.

### Create the secrets

```bash
kubectl create secret generic llm-secrets \
  --from-literal=anthropic-api-key="$ANTHROPIC_API_KEY" \
  --from-literal=litellm-master-key="sk-prod-key"
```

If you have a Postgres database for usage persistence, add:

```bash
kubectl create secret generic llm-secrets \
  --from-literal=anthropic-api-key="$ANTHROPIC_API_KEY" \
  --from-literal=litellm-master-key="sk-prod-key" \
  --from-literal=database-url="postgresql://user:pass@postgres:5432/litellm"
```

### Apply the manifests

```bash
kubectl apply -f k8s/deployment.yaml
kubectl rollout status deployment/litellm-proxy
```

### Verify

```bash
kubectl port-forward svc/litellm-proxy 4000:4000 &
curl http://localhost:4000/health/liveliness
```

### HPA behavior

The `HorizontalPodAutoscaler` scales the proxy between 2 and 10 replicas based on CPU (60%) and memory (75%) utilization. For throughput-constrained workloads, the proxy is CPU-bound on JSON serialization and TLS; 2–4 replicas is enough for most teams under 100 rps. Add an external metrics adapter (KEDA + Prometheus) to scale on request queue depth if you need finer-grained control.

```bash
kubectl get hpa litellm-proxy
# NAME             REFERENCE                     TARGETS          MINPODS   MAXPODS   REPLICAS
# litellm-proxy    Deployment/litellm-proxy      35%/60%, 40%/75%   2         10        2
```

---

## Putting It All Together

Here's what the full routing path looks like at runtime:

```
Your app (OpenAI SDK)
        │
        │  POST /v1/chat/completions
        │  {"model": "smart-router", "messages": [...]}
        ▼
  LiteLLM Proxy (port 4000)
        │
        ├─► claude-haiku (try first)
        │         │
        │         ├─ 200 OK → return to client
        │         └─ 429/5xx → fallback
        │
        ├─► claude-sonnet (try second)
        │         │
        │         ├─ 200 OK → return to client
        │         └─ 429/5xx → fallback
        │
        └─► ollama-llama3 (local, always available)
```

The client always sees an OpenAI-compatible response. The fallback chain, retries, and cost logging are invisible to calling code.

---

## Next Steps

- **Virtual keys**: Issue per-service API keys with individual rate limits via `POST /key/generate`. Revoke them without touching your master key.
- **Load balancing across regions**: Add multiple replicas of the same model pointing at different regions or accounts to spread rate limits.
- **Fine-tuned fallback**: Set `context_window_fallbacks` to automatically retry on a shorter-context model when you exceed a model's token limit.
- **Prometheus metrics**: LiteLLM exports `/metrics` in Prometheus format; scrape it with your existing stack for latency histograms and error rates per model.
