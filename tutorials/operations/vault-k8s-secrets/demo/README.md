# Demo

FastAPI reading secrets from Vault via K8s auth.

## Prerequisites
- Kubernetes, Helm >= 3.12, vault CLI

## Quick Start
```bash
helm repo add hashicorp https://helm.releases.hashicorp.com
helm install vault hashicorp/vault -f vault/vault-values.yaml
bash vault/setup.sh
kubectl apply -f k8s/
kubectl port-forward svc/secret-demo 8080:8080
curl http://localhost:8080/secret
```
