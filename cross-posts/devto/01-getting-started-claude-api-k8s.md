---
title: "Getting Started with the Claude API on Kubernetes"
published: false
description: "Deploy a FastAPI service that answers questions via Claude to a local kind cluster — Secrets, ConfigMaps, health probes, and port-forwarding included."
tags: ["kubernetes", "ai", "python", "claude"]
canonical_url: "https://greyhoundforty-tutorials.netlify.app/tutorials/kubernetes/getting-started-claude-api-k8s/"
---

If you've used the Claude API in a notebook but haven't shipped it to Kubernetes yet, this is your on-ramp. We'll build a minimal FastAPI service, containerise it, deploy it to a local [kind](https://kind.sigs.k8s.io/) cluster, and call it with `curl` — in about 15 minutes.

## What you'll build

A single-pod deployment that exposes two endpoints:

| Method | Path | Description |
|--------|------|-------------|
| GET | `/healthz` | Liveness/readiness check |
| POST | `/ask` | Send a question, get a Claude answer |

The architecture looks like this:

```
kind cluster
└── Namespace: claude-demo
    └── Deployment: claude-demo
        └── Pod → FastAPI (port 8000)
              ├── envFrom: ConfigMap  (model name, token limit)
              └── env:     Secret     (API key)
                Service: ClusterIP :80
                    ↕ kubectl port-forward
              localhost:8080
```

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) 24+
- [kind](https://kind.sigs.k8s.io/) 0.22+
- [kubectl](https://kubernetes.io/docs/tasks/tools/) 1.29+
- An Anthropic API key from [console.anthropic.com](https://console.anthropic.com)

## Step 1 — Create a kind cluster

```bash
kind create cluster --name claude-demo
kubectl cluster-info --context kind-claude-demo
```

## Step 2 — Build and load the image

```bash
cd app
docker build -t claude-k8s-demo:latest .
kind load docker-image claude-k8s-demo:latest --name claude-demo
```

kind can't pull from your local Docker daemon — the `load` step is required.

## Step 3 — Apply the namespace and ConfigMap

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml
```

The ConfigMap stores `CLAUDE_MODEL` and `MAX_TOKENS`. Swap models without rebuilding the image — just edit the ConfigMap and roll the deployment.

## Step 4 — Store your API key as a Secret

Never commit a real key. Use kubectl to write it directly:

```bash
kubectl create secret generic claude-demo-secret \
  --namespace claude-demo \
  --from-literal=ANTHROPIC_API_KEY=sk-ant-YOUR_KEY_HERE
```

## Step 5 — Deploy

```bash
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
kubectl rollout status deployment/claude-demo -n claude-demo
```

## Step 6 — Test it

```bash
kubectl port-forward svc/claude-demo 8080:80 -n claude-demo
```

In a second terminal:

```bash
curl -s -X POST http://localhost:8080/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What is Kubernetes in one sentence?"}' | jq .
```

```json
{
  "answer": "Kubernetes is an open-source container orchestration platform...",
  "model": "claude-haiku-4-5-20251001"
}
```

## Changing the model

Edit `k8s/configmap.yaml`, update `CLAUDE_MODEL`, then roll:

```bash
kubectl apply -f k8s/configmap.yaml
kubectl rollout restart deployment/claude-demo -n claude-demo
```

Available models:

| Model ID | Speed | Cost |
|----------|-------|------|
| `claude-haiku-4-5-20251001` | Fastest | Lowest |
| `claude-sonnet-4-6` | Balanced | Medium |
| `claude-opus-4-6` | Most capable | Highest |

## Scaling

```bash
kubectl scale deployment claude-demo --replicas=3 -n claude-demo
```

All replicas share the same ConfigMap and Secret — no extra config needed.

## Moving to a cloud cluster

The manifests work unchanged on GKE, EKS, and AKS. Two changes:

1. Push the image to a registry your cluster can reach and update `image:` in `deployment.yaml`.
2. Change the Service type to `LoadBalancer` for a public IP.

## Cleanup

```bash
kind delete cluster --name claude-demo
```

---

→ **Full tutorial + demo code:** [greyhoundforty-tutorials.netlify.app](https://greyhoundforty-tutorials.netlify.app/tutorials/kubernetes/getting-started-claude-api-k8s/)
