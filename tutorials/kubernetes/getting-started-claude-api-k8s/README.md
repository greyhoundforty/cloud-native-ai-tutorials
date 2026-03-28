# Getting Started with Claude API on Kubernetes

A minimal working demo: a FastAPI service that answers questions via the Claude API, deployed to a local [kind](https://kind.sigs.k8s.io/) cluster using plain `kubectl` manifests.

## What You'll Build

```
┌──────────────────────────────────────────┐
│  kind cluster (local)                    │
│                                          │
│  Namespace: claude-demo                  │
│  ┌────────────────────────────────────┐  │
│  │  Deployment: claude-demo           │  │
│  │    Pod → FastAPI (port 8000)       │  │
│  │      envFrom: ConfigMap (model)    │  │
│  │      env:     Secret (API key)     │  │
│  └────────────────────────────────────┘  │
│  Service: claude-demo (ClusterIP :80)    │
└──────────────────────────────────────────┘
         ↕ kubectl port-forward
    localhost:8080  (your terminal)
```

**Endpoints:**

| Method | Path      | Description                         |
|--------|-----------|-------------------------------------|
| GET    | `/healthz` | Liveness/readiness check            |
| POST   | `/ask`     | Send a question, get a Claude answer |

---

## Prerequisites

| Tool | Purpose | Min version |
|------|---------|-------------|
| [Docker](https://docs.docker.com/get-docker/) | Build the container image | 24+ |
| [kind](https://kind.sigs.k8s.io/docs/user/quick-start/#installation) | Local Kubernetes cluster | 0.22+ |
| [kubectl](https://kubernetes.io/docs/tasks/tools/) | Talk to the cluster | 1.29+ |

You'll also need an **Anthropic API key**. Get one at [console.anthropic.com](https://console.anthropic.com/).

---

## Step 1 — Create a kind Cluster

```bash
kind create cluster --name claude-demo
```

Verify it's up:

```bash
kubectl cluster-info --context kind-claude-demo
```

---

## Step 2 — Build the Docker Image

```bash
cd app
docker build -t claude-k8s-demo:latest .
```

Load the image into the kind cluster (kind can't pull from your local Docker daemon without this step):

```bash
kind load docker-image claude-k8s-demo:latest --name claude-demo
```

---

## Step 3 — Create the Namespace and ConfigMap

```bash
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml
```

The ConfigMap stores non-sensitive config (`CLAUDE_MODEL`, `MAX_TOKENS`). Edit `k8s/configmap.yaml` to switch models or tweak token limits without rebuilding the image.

---

## Step 4 — Store Your API Key as a Secret

**Option A — kubectl (recommended, nothing sensitive touches disk):**

```bash
kubectl create secret generic claude-demo-secret \
  --namespace claude-demo \
  --from-literal=ANTHROPIC_API_KEY=sk-ant-YOUR_KEY_HERE
```

**Option B — edit the manifest:**

1. Base64-encode your key:
   ```bash
   echo -n "sk-ant-YOUR_KEY_HERE" | base64
   ```
2. Replace `REPLACE_WITH_BASE64_ENCODED_KEY` in `k8s/secret.yaml` with the output.
3. Apply it:
   ```bash
   kubectl apply -f k8s/secret.yaml
   ```

> **Never commit a real API key to source control.** `k8s/secret.yaml` contains only a placeholder and is safe to commit as-is.

---

## Step 5 — Deploy the App

```bash
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
```

Watch the pod come up:

```bash
kubectl rollout status deployment/claude-demo -n claude-demo
```

You should see: `deployment "claude-demo" successfully rolled out`

---

## Step 6 — Test It

Forward the service to your local machine:

```bash
kubectl port-forward svc/claude-demo 8080:80 -n claude-demo
```

In a second terminal, send a question:

```bash
curl -s -X POST http://localhost:8080/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What is Kubernetes in one sentence?"}' | jq .
```

Expected response:

```json
{
  "answer": "Kubernetes is an open-source container orchestration platform...",
  "model": "claude-haiku-4-5-20251001"
}
```

Check the health endpoint:

```bash
curl http://localhost:8080/healthz
# {"status":"ok"}
```

---

## Changing the Model

Edit `k8s/configmap.yaml` and update `CLAUDE_MODEL`, then roll the deployment to pick up the change:

```bash
kubectl apply -f k8s/configmap.yaml
kubectl rollout restart deployment/claude-demo -n claude-demo
```

Available Claude models (as of 2026-03-28):

| Model ID | Speed | Cost |
|----------|-------|------|
| `claude-haiku-4-5-20251001` | Fastest | Lowest |
| `claude-sonnet-4-6` | Balanced | Medium |
| `claude-opus-4-6` | Most capable | Highest |

---

## Scaling

```bash
kubectl scale deployment claude-demo --replicas=3 -n claude-demo
```

All replicas share the same ConfigMap and Secret — no extra config needed.

---

## Deploying to a Cloud Cluster

The manifests work unchanged on GKE, EKS, and AKS. Two things to change:

1. **Image**: push to a registry your cluster can pull from (e.g. Docker Hub, ECR, GCR):
   ```bash
   docker tag claude-k8s-demo:latest your-registry/claude-k8s-demo:latest
   docker push your-registry/claude-k8s-demo:latest
   ```
   Update `image:` in `k8s/deployment.yaml` and set `imagePullPolicy: Always`.

2. **Service type**: change `ClusterIP` to `LoadBalancer` in `k8s/service.yaml` to get a public IP:
   ```yaml
   type: LoadBalancer
   ```

---

## Cleanup

```bash
kind delete cluster --name claude-demo
```

---

## Project Layout

```
claude-k8s-demo/
├── app/
│   ├── main.py           # FastAPI service
│   ├── requirements.txt  # Python deps (pinned)
│   └── Dockerfile
└── k8s/
    ├── namespace.yaml
    ├── configmap.yaml    # Model name, token limit
    ├── secret.yaml       # API key placeholder (do not commit real keys)
    ├── deployment.yaml   # Pod spec with probes and resource limits
    └── service.yaml      # ClusterIP (change to LoadBalancer for cloud)
```
