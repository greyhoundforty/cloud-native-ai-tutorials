# Demo

Helm chart + FastAPI for Claude on Kubernetes.

## Prerequisites
- Docker, Helm >= 3.12, kind, Anthropic API key

## Quick Start
```bash
docker build -t llm-app:latest app/
helm install llm-app chart/llm-app/ --set anthropicApiKey=<your-key> --set image.repository=llm-app,image.tag=latest
kubectl port-forward svc/llm-app 8080:80
curl http://localhost:8080/chat -d '{"message":"Hello"}' -H 'Content-Type: application/json'
```
