---
tags:
  - kubernetes
  - helm
  - claude-api
---

# Deploying an LLM-Backed App with Helm

> **Audience:** Intermediate Kubernetes users who have deployed apps before but haven't wired up an LLM API key or managed a Helm release from scratch.
> **Goal:** By the end you'll have a working Claude-powered summarizer running in a local [kind](https://kind.sigs.k8s.io/) cluster, managed entirely through Helm.

---

## Prerequisites

Make sure the following are installed and on your `$PATH`:

| Tool | Purpose | Min version |
|---|---|---|
| [Docker](https://docs.docker.com/get-docker/) | Build the container image | 24+ |
| [kind](https://kind.sigs.k8s.io/docs/user/quick-start/#installation) | Local Kubernetes cluster | 0.22+ |
| [kubectl](https://kubernetes.io/docs/tasks/tools/) | Talk to the cluster | 1.29+ |
| [Helm](https://helm.sh/docs/intro/install/) | Package manager | 3.14+ |

You'll also need an **Anthropic API key**. Get one at [console.anthropic.com](https://console.anthropic.com/).

---

## The Demo App

The app is a small FastAPI service with two endpoints:

- `GET /healthz` — liveness/readiness probe
- `POST /summarize` — accepts `{ "text": "..." }`, calls Claude, returns `{ "summary": "...", "model": "..." }`

```
helm-llm-tutorial/
├── app/
│   ├── main.py          # FastAPI app
│   ├── requirements.txt
│   └── Dockerfile
├── chart/
│   └── llm-app/         # Helm chart
│       ├── Chart.yaml
│       ├── values.yaml
│       └── templates/
│           ├── _helpers.tpl
│           ├── deployment.yaml
│           └── service.yaml
└── tutorial.md
```

### `app/main.py`

The app reads `ANTHROPIC_API_KEY` from the environment — never from a config file, never baked into the image.

```python
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
        messages=[{
            "role": "user",
            "content": f"Summarize in 2-3 sentences. Reply with the summary only.\n\n{req.text}"
        }],
    )
    return SummarizeResponse(summary=message.content[0].text, model=MODEL)
```

### `app/Dockerfile`

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## Step 1 — Create the kind Cluster

kind runs a full Kubernetes cluster inside Docker containers. Create one:

```bash
kind create cluster --name llm-demo
```

Verify it's up:

```bash
kubectl cluster-info --context kind-llm-demo
```

You should see the control-plane address printed. kind also updates your kubeconfig automatically.

---

## Step 2 — Build the Image

kind clusters use their own Docker daemon. You need to build the image **and load it into kind** — otherwise the cluster can't pull it.

```bash
# Build
docker build -t llm-summarizer:latest ./app

# Load into kind
kind load docker-image llm-summarizer:latest --name llm-demo
```

> **Why not push to a registry?** For local development, loading directly into kind is faster. In CI/CD you'd push to a registry (ECR, GHCR, etc.) and set `image.pullPolicy: Always`.

---

## Step 3 — Create the API Key Secret

**Never put your API key in `values.yaml`**. Helm templates are often committed to git. Instead, create a Kubernetes Secret directly with `kubectl`:

```bash
kubectl create secret generic llm-app-secret \
  --from-literal=ANTHROPIC_API_KEY=sk-ant-...
```

Verify it was created:

```bash
kubectl get secret llm-app-secret
```

Your key is now base64-encoded in etcd and will be injected into the pod as an environment variable — not visible in Helm's rendered templates.

> **For production:** Consider using [External Secrets Operator](https://external-secrets.io/) or [Vault Agent Injector](https://developer.hashicorp.com/vault/docs/platform/k8s/injector) to sync secrets from an external store rather than managing them with `kubectl create secret`.

---

## Step 4 — Inspect the Helm Chart

Before deploying, it helps to understand what Helm will render. Run a dry-run to see the generated manifests:

```bash
helm template my-llm ./chart/llm-app
```

You'll see a `Deployment` and a `Service`. The deployment pulls `ANTHROPIC_API_KEY` from the secret you just created via `secretKeyRef`.

Key section in `templates/deployment.yaml`:

```yaml
env:
  - name: ANTHROPIC_API_KEY
    valueFrom:
      secretKeyRef:
        name: {{ .Values.existingSecret.name }}   # llm-app-secret
        key: {{ .Values.existingSecret.key }}      # ANTHROPIC_API_KEY
  - name: CLAUDE_MODEL
    value: {{ .Values.claudeModel | quote }}
```

And in `values.yaml`:

```yaml
existingSecret:
  name: llm-app-secret
  key: ANTHROPIC_API_KEY

claudeModel: "claude-haiku-4-5-20251001"
maxTokens: "256"
```

This separation keeps config in `values.yaml` and secrets out of version control entirely.

---

## Step 5 — Install the Release

```bash
helm install my-llm ./chart/llm-app
```

Watch the pod come up:

```bash
kubectl get pods -w
```

Once `STATUS` is `Running` and `READY` shows `1/1`, the app is live. Check the release:

```bash
helm list
```

Output:

```
NAME    NAMESPACE  REVISION  STATUS    CHART        APP VERSION
my-llm  default    1         deployed  llm-app-0.1.0  1.0.0
```

---

## Step 6 — Test the App

The service is `ClusterIP`, so it's only reachable inside the cluster. Use `kubectl port-forward` to proxy it locally:

```bash
kubectl port-forward svc/my-llm-llm-app 8080:80
```

In a second terminal, send a request:

```bash
curl -s -X POST http://localhost:8080/summarize \
  -H "Content-Type: application/json" \
  -d '{
    "text": "Kubernetes is an open-source container orchestration system that automates the deployment, scaling, and management of containerized applications. It groups containers that make up an application into logical units for easy management and discovery."
  }' | jq .
```

Expected response:

```json
{
  "summary": "Kubernetes is an open-source system that automates the deployment, scaling, and management of containerized applications. It organizes containers into logical units to simplify management and discovery.",
  "model": "claude-haiku-4-5-20251001"
}
```

---

## Step 7 — Upgrade the Release

One of Helm's core strengths is managing upgrades. Let's change the model to Claude Sonnet and increase the token limit without touching any Kubernetes YAML directly.

Create a `values-prod.yaml` override file:

```yaml
# values-prod.yaml
claudeModel: "claude-sonnet-4-6"
maxTokens: "512"
replicaCount: 2
```

Apply the upgrade:

```bash
helm upgrade my-llm ./chart/llm-app -f values-prod.yaml
```

Helm performs a rolling update — old pods are replaced one at a time, keeping the service available throughout. Check the new revision:

```bash
helm list
# REVISION is now 2
```

Verify the rollout:

```bash
kubectl rollout status deployment/my-llm-llm-app
# Waiting for deployment "my-llm-llm-app" rollout to finish: ...
# deployment "my-llm-llm-app" successfully rolled out
```

---

## Step 8 — Roll Back

Made a bad upgrade? Helm keeps a history of every revision. Roll back to the previous one:

```bash
helm rollback my-llm 1
```

Or view the full history first:

```bash
helm history my-llm
```

```
REVISION  STATUS      CHART         APP VERSION  DESCRIPTION
1         superseded  llm-app-0.1.0  1.0.0        Install complete
2         superseded  llm-app-0.1.0  1.0.0        Upgrade complete
3         deployed    llm-app-0.1.0  1.0.0        Rollback to 1
```

---

## Step 9 — Cleanup

```bash
# Uninstall the Helm release
helm uninstall my-llm

# Delete the secret
kubectl delete secret llm-app-secret

# Tear down the kind cluster
kind delete cluster --name llm-demo
```

---

## Key Takeaways

1. **Secrets go in Kubernetes Secrets, not Helm values.** Use `secretKeyRef` to inject them at pod startup.

2. **`helm template` before you install.** Rendering templates locally catches typos and misconfigured references before they touch the cluster.

3. **Helm's `--set` and `-f` flags layer cleanly.** Use a base `values.yaml` for defaults and per-environment override files (`values-staging.yaml`, `values-prod.yaml`) — never edit the chart for environment differences.

4. **kind is great for local iteration.** `kind load docker-image` avoids a registry round-trip. When you move to CI, swap in `image.pullPolicy: Always` and push to a real registry.

5. **Rolling upgrades and rollbacks are built-in.** Helm's revision history means a bad config change is always one `helm rollback` away.

---

## What's Next

- Add an `Ingress` resource so the service is reachable without `port-forward`
- Use [External Secrets Operator](https://external-secrets.io/) to sync `ANTHROPIC_API_KEY` from AWS Secrets Manager or Vault
- Add a `HorizontalPodAutoscaler` to scale the deployment based on CPU or custom LLM latency metrics
- Package the chart as a versioned `.tgz` with `helm package` and host it in a Helm repository (OCI registry or GitHub Pages)
