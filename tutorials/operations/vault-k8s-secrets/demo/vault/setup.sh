#!/usr/bin/env bash
# vault/setup.sh — configure Vault after it starts in dev mode
#
# Run this once after `helm install vault` and the vault pod is Ready.
# It configures:
#   1. KV v2 secrets engine at secret/
#   2. A policy granting read access to the LLM API key path
#   3. Kubernetes auth method so pods can authenticate with their ServiceAccount
#   4. A role binding the llm-app ServiceAccount to the policy
#
# Usage:
#   VAULT_TOKEN=root VAULT_ADDR=http://127.0.0.1:8200 ./vault/setup.sh

set -euo pipefail

VAULT_ADDR="${VAULT_ADDR:-http://127.0.0.1:8200}"
VAULT_TOKEN="${VAULT_TOKEN:-root}"
NAMESPACE="${NAMESPACE:-llm-app}"
SERVICE_ACCOUNT="${SERVICE_ACCOUNT:-vault-demo}"

export VAULT_ADDR VAULT_TOKEN

echo "==> Using Vault at $VAULT_ADDR"

# 1. Enable KV v2 secrets engine (already enabled in dev mode at secret/)
echo "==> Ensuring KV v2 engine is enabled at secret/"
vault secrets enable -path=secret kv-v2 2>/dev/null || echo "   (already enabled)"

# 2. Store the LLM API key
echo "==> Writing LLM API key to secret/llm/anthropic"
if [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "ERROR: ANTHROPIC_API_KEY environment variable is not set."
  exit 1
fi
vault kv put secret/llm/anthropic api_key="$ANTHROPIC_API_KEY"

# 3. Create a policy for the app
echo "==> Writing policy llm-app-policy"
vault policy write llm-app-policy - <<'EOF'
# Allow reading the Anthropic API key
path "secret/data/llm/anthropic" {
  capabilities = ["read"]
}

# Allow listing (optional, useful for debugging)
path "secret/metadata/llm/*" {
  capabilities = ["list"]
}
EOF

# 4. Enable Kubernetes auth
echo "==> Enabling Kubernetes auth method"
vault auth enable kubernetes 2>/dev/null || echo "   (already enabled)"

# Configure it using the in-cluster service account token
vault write auth/kubernetes/config \
  kubernetes_host="https://${KUBERNETES_SERVICE_HOST}:${KUBERNETES_SERVICE_PORT}" \
  kubernetes_ca_cert=@/var/run/secrets/kubernetes.io/serviceaccount/ca.crt \
  token_reviewer_jwt=@/var/run/secrets/kubernetes.io/serviceaccount/token

# 5. Bind ServiceAccount → policy
echo "==> Creating Kubernetes auth role llm-app"
vault write auth/kubernetes/role/llm-app \
  bound_service_account_names="$SERVICE_ACCOUNT" \
  bound_service_account_namespaces="$NAMESPACE" \
  policies=llm-app-policy \
  ttl=1h

echo ""
echo "==> Done. Vault is configured:"
echo "    Secret path : secret/llm/anthropic"
echo "    Policy      : llm-app-policy"
echo "    Auth role   : llm-app (ns=$NAMESPACE, sa=$SERVICE_ACCOUNT)"
