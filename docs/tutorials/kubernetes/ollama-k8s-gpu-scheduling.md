---
tags:
  - kubernetes
  - ollama
  - gpu
  - helm
---

# Deploying Ollama on Kubernetes with GPU Scheduling

Self-host a large language model on Kubernetes using [Ollama](https://ollama.ai) with proper GPU scheduling, persistent model storage, and a production-ready Ingress setup.

**What you'll build:** A Helm-managed Ollama deployment that pulls Llama 3 on startup, exposes the API behind NGINX Ingress with basic auth, and lets you run inference with a single `curl` command.

## Prerequisites

- Kubernetes 1.28+ cluster with at least one GPU node (NVIDIA recommended)
- `kubectl` configured with cluster-admin access
- Helm 3.12+
- NVIDIA GPU Operator **or** manually installed `nvidia-device-plugin` DaemonSet
- NGINX Ingress Controller deployed in the cluster
- `htpasswd` utility (from `apache2-utils` / `httpd-tools`)

## Architecture

```
Internet / LAN
      │
      ▼
NGINX Ingress (basic-auth)
      │
      ▼
ollama Service (ClusterIP :11434)
      │
      ▼
ollama Pod  ──── PersistentVolumeClaim (model weights ~5 GB per model)
  (GPU node)
```

---

## Step 1 — Label Your GPU Nodes

Kubernetes doesn't know which nodes carry GPUs unless you tell it. The NVIDIA device plugin exposes `nvidia.com/gpu` as a schedulable resource, but we also add a custom label so Ollama's `nodeSelector` is explicit and portable.

```bash
# List nodes and identify which ones have GPUs
kubectl get nodes -o wide

# Label each GPU node (repeat for every GPU worker)
kubectl label node <gpu-node-name> accelerator=nvidia-gpu

# Verify
kubectl get nodes -l accelerator=nvidia-gpu
```

If you're using the NVIDIA GPU Operator (recommended for production), it manages the device plugin, container runtime config, and node feature discovery automatically:

```bash
helm repo add nvidia https://helm.ngc.nvidia.com/nvidia
helm repo update

helm install gpu-operator nvidia/gpu-operator \
  --namespace gpu-operator \
  --create-namespace \
  --wait
```

After installation, nodes with GPUs will advertise `nvidia.com/gpu` capacity:

```bash
kubectl get nodes -o json \
  | jq '.items[] | {name: .metadata.name, gpu: .status.capacity["nvidia.com/gpu"]}'
```

---

## Step 2 — Deploy Ollama with Helm

The chart in `./charts/ollama` packages Ollama's Deployment, Service, PVC, and optional Ingress. Clone this repo (or copy the chart directory) and install it:

```bash
# From the ollama-k8s-gpu directory
helm install ollama ./charts/ollama \
  --namespace ollama \
  --create-namespace \
  --wait \
  --timeout 10m
```

The `--wait --timeout 10m` flags are important: the init container pulls your chosen model (~4-5 GB for Llama 3 8B) before the main container starts. On a cold pull this can take several minutes depending on your node's download speed.

### Key default values

| Value | Default | Purpose |
|---|---|---|
| `image.tag` | `0.6` | Ollama container image tag |
| `model` | `llama3` | Model to pull on startup |
| `gpu.enabled` | `true` | Request `nvidia.com/gpu: 1` |
| `gpu.count` | `1` | Number of GPUs to request |
| `storage.size` | `20Gi` | PVC size for model weights |
| `ingress.enabled` | `true` | Create Ingress resource |
| `ingress.host` | `ollama.example.com` | Ingress hostname |
| `auth.enabled` | `true` | Enable basic auth on Ingress |

Override any value inline or via a `values-override.yaml` file:

```bash
helm install ollama ./charts/ollama \
  --namespace ollama \
  --create-namespace \
  --set model=llama3:8b \
  --set storage.size=30Gi \
  --set ingress.host=ollama.mydomain.com \
  --wait --timeout 15m
```

---

## Step 3 — Configure Basic Auth on the Ingress

The chart creates an NGINX Ingress with basic authentication. You need to provide the credentials as a Kubernetes Secret **before** (or immediately after) installing the chart.

```bash
# Generate the htpasswd hash
# Replace 'ollamauser' and 'supersecretpassword' with your own values
htpasswd -nb ollamauser supersecretpassword > /tmp/auth

# Create the secret in the ollama namespace
kubectl create secret generic ollama-basic-auth \
  --from-file=auth=/tmp/auth \
  --namespace ollama

# Verify
kubectl get secret ollama-basic-auth -n ollama
```

The Ingress resource references this secret via annotations:

```yaml
nginx.ingress.kubernetes.io/auth-type: basic
nginx.ingress.kubernetes.io/auth-secret: ollama-basic-auth
nginx.ingress.kubernetes.io/auth-realm: "Ollama API"
```

> **Security note:** Basic auth over plain HTTP is not secure. Always terminate TLS at the Ingress. Use [cert-manager](https://cert-manager.io) with a Let's Encrypt ClusterIssuer for automatic certificate management.

---

## Step 4 — Verify the Deployment

Check that the pod is running on a GPU node and the model finished pulling:

```bash
# Watch pod startup (init container pulls the model, then main container starts)
kubectl get pods -n ollama -w

# Confirm the pod landed on a GPU node
kubectl get pod -n ollama -l app.kubernetes.io/name=ollama \
  -o jsonpath='{.items[0].spec.nodeName}'

# Check GPU allocation
kubectl describe pod -n ollama -l app.kubernetes.io/name=ollama \
  | grep -A5 "Limits:"

# Tail the logs to confirm model load
kubectl logs -n ollama -l app.kubernetes.io/name=ollama -f
```

Expected log output once the model is loaded:

```
time=2024-01-15T10:23:45Z level=INFO source=routes.go msg="Listening on [::]:11434"
time=2024-01-15T10:23:46Z level=INFO source=gpu.go msg="inference compute" id=GPU-... library=cuda
```

---

## Step 5 — Query Ollama via Ingress

With basic auth and the Ingress hostname pointing to your cluster's load balancer IP:

```bash
# Set your credentials and endpoint
OLLAMA_HOST="https://ollama.example.com"
OLLAMA_USER="ollamauser"
OLLAMA_PASS="supersecretpassword"

# Check available models
curl -s -u "${OLLAMA_USER}:${OLLAMA_PASS}" \
  "${OLLAMA_HOST}/api/tags" | jq .

# Run a non-streaming inference request
curl -s -u "${OLLAMA_USER}:${OLLAMA_PASS}" \
  "${OLLAMA_HOST}/api/generate" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3",
    "prompt": "Explain Kubernetes GPU scheduling in one sentence.",
    "stream": false
  }' | jq .response
```

Expected output (varies by model):

```
"Kubernetes GPU scheduling assigns GPU resources to pods using the nvidia.com/gpu resource request, paired with node selectors or tolerations to ensure pods land on nodes with the required GPU hardware."
```

### Port-forward for local testing (no Ingress required)

```bash
kubectl port-forward svc/ollama -n ollama 11434:11434 &

curl -s http://localhost:11434/api/generate \
  -H "Content-Type: application/json" \
  -d '{"model": "llama3", "prompt": "Hello!", "stream": false}' \
  | jq .response
```

---

## Step 6 — Pull Additional Models

Ollama supports hundreds of models from the [model library](https://ollama.ai/library). To pull additional models without redeploying:

```bash
# Exec into the running pod
kubectl exec -it -n ollama \
  $(kubectl get pod -n ollama -l app.kubernetes.io/name=ollama -o name) \
  -- ollama pull mistral

# Or via the API
curl -s -u "${OLLAMA_USER}:${OLLAMA_PASS}" \
  "${OLLAMA_HOST}/api/pull" \
  -H "Content-Type: application/json" \
  -d '{"name": "mistral"}' | jq .
```

Models are stored on the PVC at `/root/.ollama/models`, so they persist across pod restarts.

---

## Customizing the Helm Chart

### Running without a GPU (CPU-only)

For development or testing on clusters without GPUs:

```bash
helm install ollama ./charts/ollama \
  --namespace ollama \
  --create-namespace \
  --set gpu.enabled=false \
  --set model=llama3:8b \
  --wait --timeout 20m
```

CPU inference is significantly slower but functional for development purposes.

### Resource requests and limits

Tune resources in `values.yaml` or via `--set`:

```bash
helm upgrade ollama ./charts/ollama \
  --namespace ollama \
  --set resources.requests.memory=8Gi \
  --set resources.limits.memory=16Gi \
  --set gpu.count=2
```

### Multiple replicas and model sharding

Ollama does not natively support multi-GPU model parallelism across pods. For larger models (70B+), use a single pod with multiple GPUs on one node by increasing `gpu.count`. For horizontal scaling of inference throughput, deploy multiple single-GPU replicas behind the Service (each pod maintains its own copy of the model weights on a separate PVC).

---

## Troubleshooting

### Pod stuck in `Pending`

```bash
kubectl describe pod -n ollama -l app.kubernetes.io/name=ollama
```

Common causes:
- `0/N nodes are available: N Insufficient nvidia.com/gpu` — no GPU nodes available or device plugin not running
- PVC not bound — check `kubectl get pvc -n ollama` and your StorageClass

### Init container `model-pull` failing

```bash
kubectl logs -n ollama \
  $(kubectl get pod -n ollama -l app.kubernetes.io/name=ollama -o name) \
  -c model-pull
```

- Network policy blocking egress to `registry.ollama.ai`
- Insufficient PVC space (increase `storage.size`)

### GPU not detected inside the container

```bash
kubectl exec -it -n ollama \
  $(kubectl get pod -n ollama -l app.kubernetes.io/name=ollama -o name) \
  -- nvidia-smi
```

If `nvidia-smi` fails, the NVIDIA container runtime is not configured on the node. Verify the GPU Operator is running and the node runtime class is set to `nvidia`.

### 401 Unauthorized from Ingress

```bash
kubectl get secret ollama-basic-auth -n ollama -o jsonpath='{.data.auth}' | base64 -d
```

Ensure the secret exists in the `ollama` namespace and the `auth` key contains a valid `htpasswd` hash.

---

## Clean Up

```bash
helm uninstall ollama --namespace ollama
kubectl delete namespace ollama
```

> The PVC is **not** deleted by `helm uninstall` by default. Delete it explicitly if you want to reclaim storage: `kubectl delete pvc -n ollama --all`

---

## Next Steps

- Add [cert-manager](https://cert-manager.io) for automatic TLS certificates
- Set up [Prometheus + Grafana](https://github.com/prometheus-community/helm-charts) to monitor GPU utilization via DCGM Exporter
- Integrate Ollama with [Open WebUI](https://github.com/open-webui/open-webui) for a ChatGPT-style browser interface
- Explore the [Ollama Python SDK](https://github.com/ollama/ollama-python) or [LangChain Ollama integration](https://python.langchain.com/docs/integrations/llms/ollama) for application development
