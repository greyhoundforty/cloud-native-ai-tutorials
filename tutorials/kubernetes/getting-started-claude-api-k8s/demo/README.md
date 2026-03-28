# Demo

FastAPI + Claude on Kubernetes.

## Prerequisites
- Docker, kubectl, kind, Anthropic API key

## Quick Start
```bash
docker build -t claude-k8s-demo:latest app/
kubectl create namespace claude-demo
kubectl create secret generic claude-api-key --from-literal=ANTHROPIC_API_KEY=<your-key> -n claude-demo
kubectl apply -f k8s/
kubectl port-forward svc/claude-demo 8080:80 -n claude-demo
curl http://localhost:8080/chat -d '{"message":"Hello"}' -H 'Content-Type: application/json'
```
