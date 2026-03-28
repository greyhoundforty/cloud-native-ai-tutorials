# Demo

FastAPI RAG app with pgvector on Kubernetes.

## Prerequisites
- Kubernetes cluster, Helm >= 3.12, Anthropic API key

## Quick Start
```bash
helm repo add bitnami https://charts.bitnami.com/bitnami
helm install postgres bitnami/postgresql --set image.tag=16-pgvector --set auth.postgresPassword=secret
helm install rag-app helm/ --set anthropicApiKey=<your-key> --set postgres.password=secret
kubectl port-forward svc/rag-app 8080:8080
python ingest.py --url http://localhost:8080
curl http://localhost:8080/query -d '{"question":"Your question"}' -H 'Content-Type: application/json'
```
