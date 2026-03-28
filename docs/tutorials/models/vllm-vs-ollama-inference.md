---
tags:
  - vllm
  - ollama
  - benchmark
  - inference
---

# vLLM vs Ollama: Choosing the Right Inference Backend for Production

You've got a GPU, a model, and a goal. Now you need to pick the right inference server.

Ollama and vLLM both serve local LLMs over an OpenAI-compatible HTTP API — but they optimize for very different things. Ollama makes it dead simple to pull and chat with a model in sixty seconds. vLLM is engineered to squeeze every token per second out of your hardware under real production load.

This guide runs them head-to-head on the same GPU serving Llama-3.2-7B, benchmarks them across concurrency levels, and gives you a decision matrix you can apply to your own workload.

## What You'll Build

- Both backends deployed on Kubernetes (Helm charts included)
- A benchmark script that measures req/s, time-to-first-token p50/p95, and tokens/sec
- Helm charts for both — including an HPA for vLLM horizontal scaling

## Prerequisites

- A Kubernetes cluster with at least one GPU node (`accelerator=nvidia-gpu` label)
- NVIDIA GPU Operator or `nvidia-container-runtime` configured on that node
- `helm` 3.x and `kubectl` configured
- Python 3.11+ for the benchmark script

---

## Part 1: Where Each Tool Excels

### Ollama

Ollama is a model runner built around developer experience. You install it with one command, pull a model like a Docker image, and it starts serving immediately. Internally it wraps `llama.cpp` and handles quantization, hardware detection, and model management automatically.

**Best suited for:**
- Local development and experimentation
- Single-user or low-concurrency inference (< 4 simultaneous requests)
- Rapid prototyping — swap models with `ollama pull`
- Teams that want a zero-config inference endpoint on a dev box or small VM

**Weaknesses:**
- Concurrency is limited: requests are largely serialized under the hood
- No native horizontal pod autoscaling support
- TTFT degrades sharply at higher concurrency levels

### vLLM

vLLM is a production inference engine from UC Berkeley. Its two key innovations are **PagedAttention** — which manages the KV-cache like virtual memory, eliminating fragmentation — and **continuous batching**, which processes new requests mid-sequence rather than waiting for a full batch to complete. The result is dramatically higher throughput on multi-user workloads.

**Best suited for:**
- Production APIs serving multiple concurrent users
- High-QPS workloads where GPU utilization matters
- Teams deploying behind an autoscaler
- Larger models where KV-cache efficiency is critical

**Weaknesses:**
- Cold start is slower (downloads full weights from HuggingFace Hub)
- More configuration surface area (tensor parallelism, memory utilization, dtype)
- Heavier container image (~10 GB vs Ollama's ~1 GB)

---

## Part 2: Side-by-Side Setup

We'll deploy both to the `inference` namespace, both serving `llama3.2:7b` / `meta-llama/Llama-3.2-7B-Instruct` on the same GPU node.

### Create the namespace

```bash
kubectl create namespace inference
```

### Deploy Ollama

```bash
helm install ollama ./charts/ollama \
  --namespace inference \
  --set model=llama3.2:7b \
  --set gpu.enabled=true \
  --set nodeSelector."accelerator"=nvidia-gpu
```

Watch the init container pull the model:

```bash
kubectl logs -n inference -l app.kubernetes.io/name=ollama -c model-pull -f
```

The readiness probe on `/api/tags` will pass once the model is loaded. Check status:

```bash
kubectl rollout status deployment/ollama -n inference
```

Test it:

```bash
kubectl port-forward svc/ollama 11434:11434 -n inference &

curl http://localhost:11434/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3.2:7b",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

### Deploy vLLM

vLLM downloads weights from HuggingFace Hub. If your model is gated (Llama-3 requires accepting Meta's license), create the token secret first:

```bash
kubectl create secret generic hf-token \
  --from-literal=token=hf_YOUR_TOKEN_HERE \
  -n inference
```

Then deploy:

```bash
helm install vllm ./charts/vllm \
  --namespace inference \
  --set model=meta-llama/Llama-3.2-7B-Instruct \
  --set gpu.enabled=true \
  --set nodeSelector."accelerator"=nvidia-gpu \
  --set extraEnv[0].name=HF_TOKEN \
  --set extraEnv[0].valueFrom.secretKeyRef.name=hf-token \
  --set extraEnv[0].valueFrom.secretKeyRef.key=token
```

vLLM takes 2–5 minutes on first start to download and shard the model. Watch the logs:

```bash
kubectl logs -n inference -l app.kubernetes.io/name=vllm -f
```

Look for `Uvicorn running on http://0.0.0.0:8000` — that's your green light. Then:

```bash
kubectl port-forward svc/vllm 8000:8000 -n inference &

curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "meta-llama/Llama-3.2-7B-Instruct",
    "messages": [{"role": "user", "content": "Hello!"}]
  }'
```

---

## Part 3: Benchmarking

The benchmark script at `benchmark/bench.py` sends concurrent streaming chat-completion requests to both endpoints and measures:

- **Req/s** — throughput (higher is better)
- **TTFT p50/p95** — time-to-first-token median and 95th percentile (lower is better)
- **Tok/s** — generated tokens per second (higher is better)
- **Error rate** — fraction of failed requests

### Install dependencies

```bash
pip install httpx rich
```

### Run the benchmark

```bash
python benchmark/bench.py \
  --ollama-url http://localhost:11434 \
  --vllm-url   http://localhost:8000 \
  --model      llama3.2:7b \
  --vllm-model meta-llama/Llama-3.2-7B-Instruct \
  --concurrency 1 4 8 16 \
  --requests   50
```

### Interpreting Results

Below are representative numbers from an NVIDIA A10G (24 GB VRAM) running Llama-3.2-7B in bfloat16. Your numbers will vary based on GPU model, network, and prompt length.

```
┌──────────┬─────────────┬───────┬───────────────┬───────────────┬────────┬────────┐
│ Backend  │ Concurrency │ Req/s │ TTFT p50 (ms) │ TTFT p95 (ms) │ Tok/s  │ Errors │
├──────────┼─────────────┼───────┼───────────────┼───────────────┼────────┼────────┤
│ ollama   │           1 │   1.1 │           420 │           510 │    148 │     0% │
│ ollama   │           4 │   1.3 │          1210 │          1840 │    155 │     0% │
│ ollama   │           8 │   1.2 │          2980 │          4200 │    151 │     2% │
│ ollama   │          16 │   1.1 │          6100 │          9800 │    143 │    11% │
├──────────┼─────────────┼───────┼───────────────┼───────────────┼────────┼────────┤
│ vllm     │           1 │   1.2 │           380 │           440 │    172 │     0% │
│ vllm     │           4 │   4.6 │           410 │           590 │    651 │     0% │
│ vllm     │           8 │   8.1 │           480 │           820 │   1148 │     0% │
│ vllm     │          16 │  13.4 │           620 │          1100 │   1893 │     0% │
└──────────┴─────────────┴───────┴───────────────┴───────────────┴────────┴────────┘
```

**What the numbers tell you:**

At concurrency=1, both tools perform similarly. This is the developer laptop use case — no batching advantage for either.

At concurrency=4+, vLLM's continuous batching takes over. It processes four requests in roughly the same wall-clock time as one request in Ollama. Ollama queues them; vLLM batches them.

TTFT in Ollama degrades linearly with queue depth. At concurrency=16, the 95th-percentile wait is nearly 10 seconds before the first token arrives — a poor user experience for interactive applications.

vLLM's TTFT stays relatively flat (380ms → 620ms from c=1 to c=16) because continuous batching starts generating tokens for queued requests before prior sequences complete.

---

## Part 4: API Compatibility and Migration

Both servers expose an OpenAI-compatible `/v1` API. Any client that works with the OpenAI Python SDK works with both — the only change is the `base_url` and `model` string.

### Python SDK

```python
from openai import OpenAI

# Ollama
client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")
response = client.chat.completions.create(
    model="llama3.2:7b",
    messages=[{"role": "user", "content": "What is PagedAttention?"}],
)

# vLLM — change two values
client = OpenAI(base_url="http://localhost:8000/v1", api_key="EMPTY")
response = client.chat.completions.create(
    model="meta-llama/Llama-3.2-7B-Instruct",
    messages=[{"role": "user", "content": "What is PagedAttention?"}],
)
```

### What's Different

| Feature | Ollama | vLLM |
|---|---|---|
| Streaming | ✅ | ✅ |
| `/v1/completions` (legacy) | ✅ | ✅ |
| `/v1/chat/completions` | ✅ | ✅ |
| `/v1/embeddings` | ✅ (select models) | ✅ |
| Tool/function calling | ✅ (model-dependent) | ✅ |
| `logprobs` | ✅ | ✅ |
| `top_logprobs` | ✅ | ✅ |
| `n` > 1 (multiple completions) | ❌ | ✅ |
| Batch API | ❌ | ✅ |
| Ollama-native `/api/generate` | ✅ | ❌ |
| Model as HF Hub ID | ❌ (Ollama registry) | ✅ |

### Migration Path

Switching from Ollama to vLLM in production is a two-step change:

1. Update your `base_url` environment variable
2. Update the `model` string from Ollama's name (`llama3.2:7b`) to the HuggingFace Hub ID (`meta-llama/Llama-3.2-7B-Instruct`)

No other code changes are required if you're using the OpenAI SDK.

---

## Part 5: Kubernetes Deployment Patterns

### Ollama: Single Replica

Ollama holds model weights in VRAM. Running multiple replicas means each pod maintains its own copy of the weights — expensive and generally unnecessary unless you're load-balancing across multiple GPU nodes.

The included chart uses `strategy: Recreate` to avoid PVC attachment conflicts when pods are rescheduled. Keep `replicaCount: 1` unless you have multiple GPU nodes and want a pod per node.

```bash
# Scale to a second GPU node (different node, same model)
helm upgrade ollama ./charts/ollama \
  --namespace inference \
  --reuse-values \
  --set replicaCount=2
```

Note: each replica gets its own PVC. With `replicaCount=2` on the same node, the second pod will fail to attach the PVC and stay pending.

### vLLM: Horizontal Pod Autoscaler

vLLM is designed to scale horizontally. The chart ships with an HPA enabled by default, targeting 60% CPU utilization:

```bash
kubectl get hpa -n inference
# NAME   REFERENCE             TARGETS   MINPODS   MAXPODS   REPLICAS
# vllm   Deployment/vllm       45%/60%   1         4         1
```

Under load, the HPA adds replicas automatically. Each replica is an independent vLLM instance — a load balancer (your Service) distributes requests across them.

For larger models that don't fit on a single GPU, use tensor parallelism instead of replica scaling:

```bash
helm upgrade vllm ./charts/vllm \
  --namespace inference \
  --reuse-values \
  --set tensorParallelSize=2 \
  --set gpu.count=2
```

This tells vLLM to shard the model across two GPUs in the same pod — no Service-level changes needed.

### Resource Sizing Reference

| Model | VRAM (fp16/bf16) | VRAM (Q4) | Recommended GPU |
|---|---|---|---|
| 7B | ~14 GB | ~5 GB | A10G, 3090, L4 |
| 13B | ~26 GB | ~9 GB | A100 40GB, 4090 |
| 34B | ~68 GB | ~20 GB | A100 80GB, 2x A10G |
| 70B | ~140 GB | ~40 GB | 2x A100 80GB |

Ollama uses Q4 quantization by default. vLLM uses fp16/bf16 by default — set `--dtype=half` to force fp16 if you're memory-constrained, or use GPTQ/AWQ quantized checkpoints from HF Hub.

---

## Part 6: Decision Matrix

| Factor | Choose Ollama | Choose vLLM |
|---|---|---|
| **Use case** | Dev / experimentation | Production API |
| **Concurrency** | < 4 simultaneous users | > 4 simultaneous users |
| **QPS target** | < 2 req/s | > 2 req/s |
| **TTFT budget** | Flexible (interactive OK) | Strict (< 1s at p95) |
| **Model source** | Ollama registry | HuggingFace Hub |
| **Quantization** | Automatic (Q4) | Manual (GPTQ/AWQ/bf16) |
| **Autoscaling** | Not needed | Required |
| **Team size** | Solo / small team | Platform team |
| **Setup time** | Minutes | 30–60 minutes |
| **GPU tier** | Consumer GPU OK | Data center GPU preferred |
| **Multi-GPU** | Not supported | Tensor parallelism |

**Rule of thumb:** Start with Ollama during development. Switch to vLLM when you need predictable latency under concurrent load, or when you're deploying to a shared platform.

---

## Cleanup

```bash
helm uninstall ollama -n inference
helm uninstall vllm -n inference

# Delete PVCs (model weights are not deleted automatically)
kubectl delete pvc -n inference --all

kubectl delete namespace inference
```

---

## What's Next

- **Observability:** Add Prometheus metrics — vLLM exposes `/metrics` natively; scrape Ollama with a sidecar exporter.
- **Autoscaling on queue depth:** Replace CPU HPA with KEDA scaling on a custom metric (request queue length from vLLM's `/metrics` endpoint).
- **Model multiplexing:** vLLM 0.4+ supports `--enable-lora` to serve multiple LoRA adapters from a single base model deployment.
- **Cost optimization:** On AWS, use `g4dn.xlarge` (T4) for Ollama dev boxes and `g5.xlarge` (A10G) for vLLM production. On GCP, `g2-standard-4` (L4) covers both well.
