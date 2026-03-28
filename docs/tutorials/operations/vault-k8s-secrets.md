---
tags:
  - vault
  - kubernetes
  - secrets
  - security
---

# Secrets Management for AI Applications with HashiCorp Vault and Kubernetes

> **Audience:** Developers running LLM-backed services on Kubernetes who want to stop putting API keys in `.env` files, ConfigMaps, or unencrypted K8s Secrets.
> **Goal:** By the end you'll have a FastAPI app deployed to a local [kind](https://kind.sigs.k8s.io/) cluster that reads its Anthropic API key from HashiCorp Vault at runtime — the key never appears in your image, your manifests, or your git history.

---

## Why This Matters: The API Key Sprawl Problem

When you start building AI features, it's tempting to just export `ANTHROPIC_API_KEY` and move on. That works on your laptop. On Kubernetes, the same instinct produces something like this:

```yaml
# Don't do this
env:
  - name: ANTHROPIC_API_KEY
    value: "sk-ant-api03-..."
```

Or, if you've read the docs, you upgrade to a K8s Secret:

```bash
kubectl create secret generic llm-creds --from-literal=ANTHROPIC_API_KEY=sk-ant-...
```

Better — but K8s Secrets are just base64-encoded, not encrypted. Anyone with `kubectl get secret` access can decode them. They live in etcd. They show up in `helm get values`. They get copy-pasted into CI pipelines. Before long your API key is in four places and you don't know which one is current.

HashiCorp Vault solves this with a single source of truth: a secrets store with fine-grained access control, audit logging, and lease-based rotation. Pods authenticate to Vault using their ServiceAccount token — nothing secrets-shaped needs to exist in your cluster at all.

### The Two Patterns We'll Cover

| Pattern | How it works | Best for |
|---|---|---|
| **Vault Agent Injector** | Sidecar container mounts secrets as files into your pod | Existing apps, no code changes |
| **External Secrets Operator** | Syncs Vault secrets into native K8s Secrets | GitOps workflows, ArgoCD, Flux |

We'll build the injector pattern end-to-end, then show the ESO alternative.

---

## Project Layout

```
vault-k8s-secrets/
├── app/
│   ├── main.py              # FastAPI app — reads key from file, not env
│   ├── requirements.txt
│   └── Dockerfile
├── k8s/
│   ├── namespace.yaml       # Namespace with injector webhook label
│   ├── service-account.yaml # SA that Vault uses for authentication
│   ├── deployment.yaml      # Deployment with injector annotations
│   ├── service.yaml
│   └── external-secrets-example.yaml  # Alternative ESO pattern
├── vault/
│   ├── vault-values.yaml    # Vault Helm chart values (dev mode)
│   └── setup.sh             # Configure Vault after install
└── tutorial.md              # This file
```

---

## Prerequisites

| Tool | Purpose | Min version |
|---|---|---|
| [Docker](https://docs.docker.com/get-docker/) | Build container image | 24+ |
| [kind](https://kind.sigs.k8s.io/docs/user/quick-start/) | Local Kubernetes cluster | 0.22+ |
| [kubectl](https://kubernetes.io/docs/tasks/tools/) | Talk to the cluster | 1.29+ |
| [Helm](https://helm.sh/docs/intro/install/) | Install Vault | 3.14+ |
| [vault CLI](https://developer.hashicorp.com/vault/install) | Configure Vault | 1.17+ |

You'll also need an **Anthropic API key** from [console.anthropic.com](https://console.anthropic.com/).

---

## Part 1: Stand Up the Cluster

```bash
kind create cluster --name vault-demo
kubectl config use-context kind-vault-demo
```

Verify:

```bash
kubectl get nodes
# NAME                       STATUS   ROLES           AGE   VERSION
# vault-demo-control-plane   Ready    control-plane   30s   v1.31.x
```

---

## Part 2: Install Vault with Helm

Add the HashiCorp Helm repo and install Vault into its own namespace:

```bash
helm repo add hashicorp https://helm.releases.hashicorp.com
helm repo update

helm install vault hashicorp/vault \
  --namespace vault \
  --create-namespace \
  --values vault/vault-values.yaml
```

Wait for the pod to be ready:

```bash
kubectl rollout status deployment/vault-agent-injector -n vault
kubectl rollout status statefulset/vault -n vault
```

The Helm chart deploys two things:
- **vault** — the Vault server (dev mode: in-memory, auto-unsealed, root token `root`)
- **vault-agent-injector** — a MutatingWebhookConfiguration that intercepts pod creation and injects sidecars when it sees the right annotations

### Verify the injector webhook is registered

```bash
kubectl get mutatingwebhookconfiguration vault-agent-injector-cfg
```

If this resource exists, the injector is wired in.

---

## Part 3: Configure Vault

Port-forward the Vault API so we can talk to it from your laptop:

```bash
kubectl port-forward svc/vault -n vault 8200:8200 &
export VAULT_ADDR=http://127.0.0.1:8200
export VAULT_TOKEN=root
```

Now run the setup script. It will:
1. Enable the KV v2 secrets engine
2. Store your Anthropic API key at `secret/llm/anthropic`
3. Write a policy granting read access to that path
4. Enable and configure Kubernetes auth
5. Create a role binding your app's ServiceAccount to the policy

```bash
export ANTHROPIC_API_KEY=sk-ant-your-key-here

# The script runs inside the vault pod to configure k8s auth
kubectl exec -n vault vault-0 -- /bin/sh -c "
  export VAULT_TOKEN=root
  export ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY
  vault secrets enable -path=secret kv-v2 2>/dev/null || true

  vault kv put secret/llm/anthropic api_key=$ANTHROPIC_API_KEY

  vault policy write llm-app-policy - <<'EOF'
path \"secret/data/llm/anthropic\" {
  capabilities = [\"read\"]
}
path \"secret/metadata/llm/*\" {
  capabilities = [\"list\"]
}
EOF

  vault auth enable kubernetes 2>/dev/null || true

  vault write auth/kubernetes/config \
    kubernetes_host=https://\${KUBERNETES_SERVICE_HOST}:\${KUBERNETES_SERVICE_PORT} \
    kubernetes_ca_cert=@/var/run/secrets/kubernetes.io/serviceaccount/ca.crt \
    token_reviewer_jwt=@/var/run/secrets/kubernetes.io/serviceaccount/token

  vault write auth/kubernetes/role/llm-app \
    bound_service_account_names=vault-demo \
    bound_service_account_namespaces=llm-app \
    policies=llm-app-policy \
    ttl=1h
"
```

Verify the key was stored:

```bash
vault kv get secret/llm/anthropic
# ====== Secret Path ======
# secret/data/llm/anthropic
#
# ======= Data =======
# Key        Value
# ---        -----
# api_key    sk-ant-...
```

The key exists in Vault. It is **not** in any K8s resource yet.

---

## Part 4: How Vault Auth Works (the SPIFFE-like pattern)

Before deploying the app, it's worth understanding the auth flow:

```
Pod starts
  │
  ├── Vault Agent sidecar (injected) reads its own K8s ServiceAccount token
  │   (projected by K8s at /var/run/secrets/kubernetes.io/serviceaccount/token)
  │
  ├── Sidecar sends that token to Vault: POST /v1/auth/kubernetes/login
  │   { role: "llm-app", jwt: "<sa-token>" }
  │
  ├── Vault asks the K8s API: "is this token valid? what SA is it for?"
  │   K8s: "yes, it's vault-demo in llm-app"
  │
  ├── Vault checks: does role llm-app allow sa=vault-demo in ns=llm-app?
  │   Yes → issues a short-lived Vault token (TTL: 1h)
  │
  └── Sidecar uses that Vault token to read secret/data/llm/anthropic
      and writes the api_key value to /vault/secrets/anthropic-key
```

The app container never handles Vault tokens. It just reads a file.

---

## Part 5: Build and Load the App Image

```bash
docker build -t vault-demo:latest ./app

# Load into kind — no registry needed
kind load docker-image vault-demo:latest --name vault-demo
```

---

## Part 6: Deploy the App

### 6.1 Create the namespace (with the injector webhook label)

```bash
kubectl apply -f k8s/namespace.yaml
```

The label `vault.hashicorp.com/inject: "true"` on the namespace tells the webhook to watch pods here. Without it, the injector annotations are silently ignored.

### 6.2 Create the ServiceAccount

```bash
kubectl apply -f k8s/service-account.yaml
```

This is the SA we bound to the `llm-app` Vault role in Part 3.

### 6.3 Deploy

```bash
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
```

Watch the pod come up:

```bash
kubectl get pods -n llm-app -w
```

You should see **two containers** in the pod — `vault-agent` (the injector sidecar) and `app` (your FastAPI service):

```
NAME                          READY   STATUS    RESTARTS   AGE
vault-demo-6d8b9c7f4-xk2p9   2/2     Running   0          30s
```

`2/2` means both containers are running. If you see `0/2` or `Init:` states, the agent is still authenticating.

### 6.4 Verify the secret was injected

```bash
kubectl exec -n llm-app deploy/vault-demo -c app -- cat /vault/secrets/anthropic-key
# sk-ant-...
```

The API key is there — injected by the sidecar, never present in any manifest.

### 6.5 Test the app

```bash
kubectl port-forward svc/vault-demo -n llm-app 8080:80 &

curl -s -X POST http://localhost:8080/summarize \
  -H "Content-Type: application/json" \
  -d '{"text": "HashiCorp Vault is a secrets management tool that provides a unified interface to any secret, while providing tight access control and recording a detailed audit log."}' \
  | jq .
```

Expected response:

```json
{
  "summary": "HashiCorp Vault is a centralized secrets management solution offering controlled access and comprehensive audit logging for sensitive data.",
  "model": "claude-haiku-4-5-20251001",
  "secret_source": "/vault/secrets/anthropic-key"
}
```

The `secret_source` field confirms the app read the key from the Vault-injected file, not from an environment variable.

---

## Part 7: Audit Logging — Who Read What and When

One of Vault's killer features for AI workloads is audit logging. In dev mode it's disabled by default. Enable it:

```bash
kubectl exec -n vault vault-0 -- vault audit enable file file_path=/vault/logs/audit.log
```

Now tail the log while you make a request:

```bash
# In one terminal
kubectl exec -n vault vault-0 -- tail -f /vault/logs/audit.log | jq .

# In another
curl -s -X POST http://localhost:8080/summarize \
  -H "Content-Type: application/json" \
  -d '{"text": "Testing audit logs"}'
```

You'll see a structured JSON entry for every Vault operation — including the `secret/data/llm/anthropic` read, which entity performed it, from which IP, and with which token. For LLM applications this is invaluable: if an API key is leaked or abused, you can trace exactly which pod, at what time, accessed it.

Sample audit entry (abbreviated):

```json
{
  "type": "response",
  "auth": {
    "client_token": "hmac-sha256:...",
    "accessor": "hmac-sha256:...",
    "entity_id": "",
    "metadata": {
      "role": "llm-app",
      "service_account_name": "vault-demo",
      "service_account_namespace": "llm-app"
    }
  },
  "request": {
    "operation": "read",
    "path": "secret/data/llm/anthropic",
    "remote_address": "10.244.0.12"
  },
  "response": {
    "data": {
      "api_key": "hmac-sha256:..."
    }
  }
}
```

The actual value is HMAC-hashed in the log — Vault never writes plaintext secrets to the audit trail.

---

## Part 8: Secret Rotation Without Pod Restarts

The Vault Agent sidecar has a lease renewal loop. When you rotate the key in Vault, the sidecar picks up the new value and rewrites `/vault/secrets/anthropic-key` — your app reads the new key on its next request without a restart.

### Simulate a key rotation

```bash
# Write a new value to Vault (simulating a rotation)
kubectl exec -n vault vault-0 -- vault kv put secret/llm/anthropic \
  api_key="sk-ant-new-rotated-key"

# The sidecar's default re-read interval is the token TTL (1h in our config)
# Force an immediate re-read by restarting only the sidecar — not the app
kubectl exec -n llm-app deploy/vault-demo -c vault-agent -- kill -HUP 1

# Check the file was updated
kubectl exec -n llm-app deploy/vault-demo -c app -- cat /vault/secrets/anthropic-key
# sk-ant-new-rotated-key
```

For production, set a shorter TTL on the Vault role (e.g., `ttl=5m`) so rotation propagates quickly, and configure `vault.hashicorp.com/agent-inject-template-anthropic-key` with `{{ with secret ... }}` so the sidecar re-renders the file on each lease renewal.

---

## Part 9: Alternative Pattern — External Secrets Operator

The injector approach requires pod annotation changes, which can be awkward in GitOps workflows where you don't control the Deployment template. The **External Secrets Operator** (ESO) takes a different approach: it syncs Vault secrets into native K8s Secrets, which any workload can consume normally.

```
Vault KV secret  ──[ESO controller]──►  K8s Secret  ──[envFrom]──►  Pod env var
```

Install ESO:

```bash
helm repo add external-secrets https://charts.external-secrets.io
helm install external-secrets external-secrets/external-secrets \
  -n external-secrets-system --create-namespace
```

Apply the example config in `k8s/external-secrets-example.yaml`:

```bash
kubectl apply -f k8s/external-secrets-example.yaml
```

This creates a `SecretStore` (how to connect to Vault) and an `ExternalSecret` (what to sync). ESO reconciles on the `refreshInterval` and keeps the K8s Secret in sync. When you rotate in Vault, ESO updates the K8s Secret within the refresh window, and any pod that mounts it as an env var gets the new value on its next restart.

### Injector vs ESO — when to use which

| Concern | Injector | ESO |
|---|---|---|
| No app code changes | Yes (file-based) | Yes (env var-based) |
| GitOps-friendly | Less — annotations on pods | Yes — separate `ExternalSecret` object |
| Works with any workload | Yes | Yes |
| Secrets visible in `kubectl get secret` | No | Yes (synced K8s Secret exists) |
| Fine-grained per-pod access | Yes (per-SA role) | Per-namespace (SecretStore) |
| Rotation without restart | Yes (file rewrite) | No (restart needed for env vars) |

For most cloud-native AI workloads, ESO + a short `refreshInterval` with rolling restarts is the simpler operational path. Use the injector when you need per-pod isolation or zero-restart rotation.

---

## Part 10: What NOT to Do

A few patterns that are common in tutorials but dangerous in practice:

**Don't print the key in logs:**
```python
# Wrong
print(f"Using key: {api_key}")

# Right — log that a key was loaded, never its value
logger.info("API key loaded from %s", VAULT_SECRET_PATH)
```

**Don't pass the key as a build arg:**
```dockerfile
# Wrong — appears in image layers and `docker history`
ARG ANTHROPIC_API_KEY
ENV ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY
```

**Don't store the key in a ConfigMap:**
```yaml
# Wrong — ConfigMaps are not secret
data:
  ANTHROPIC_API_KEY: "sk-ant-..."
```

**Don't hardcode the Vault token:**
```bash
# Wrong — the injector sidecar handles auth; you never need a Vault token in app code
export VAULT_TOKEN=root
```

---

## Cleanup

```bash
# Remove the app
kubectl delete -f k8s/

# Remove Vault
helm uninstall vault -n vault

# Tear down the cluster
kind delete cluster --name vault-demo
```

---

## Production Checklist

Before taking this to production, address these gaps that dev mode papers over:

- [ ] **Vault HA with TLS** — use `server.ha.enabled: true` and a real TLS cert (cert-manager works well)
- [ ] **Persistent storage** — mount a `StorageClass` for Vault's data; dev mode uses in-memory storage
- [ ] **Auto-unseal** — configure KMS auto-unseal (AWS KMS, GCP CKMS, Azure Key Vault) so Vault survives pod restarts
- [ ] **Short TTLs** — set role TTL to match your rotation SLA (5m–15m is common for API keys)
- [ ] **Audit log shipping** — forward `/vault/logs/audit.log` to your SIEM (Splunk, Datadog, etc.)
- [ ] **Namespace isolation** — one Vault role per namespace; don't bind `bound_service_account_names=*`
- [ ] **Sentinel policies** — use Vault Sentinel (Enterprise) or OPA admission control to block direct `kubectl get secret`
- [ ] **Key version pinning** — use `kv get -version=N` in the injector template if you need rollback control

---

## Summary

You've seen:

1. **Why K8s Secrets aren't enough** — base64 ≠ encryption; they proliferate and lack audit trails
2. **Vault on K8s** — Helm install with dev mode, KV v2 engine, Kubernetes auth
3. **Vault Agent Injector** — pod annotations that mount secrets as files, no code changes required
4. **Auth flow** — how pods authenticate using their ServiceAccount token, no credentials baked in
5. **Audit logging** — per-request structured logs with HMAC-hashed values
6. **Rotation without restarts** — the injector sidecar rewrites the secret file on lease renewal
7. **External Secrets Operator** — the GitOps-friendly alternative that syncs to native K8s Secrets

The working demo ships zero secrets in the container image, zero secrets in K8s manifests, and zero hardcoded credentials in application code. That's the baseline for any AI service handling third-party API keys in production.
