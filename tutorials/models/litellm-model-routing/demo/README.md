# Demo

LiteLLM proxy with multi-provider routing.

## Prerequisites
- Docker (local) or Kubernetes + Helm, provider API keys

## Quick Start (Docker)
```bash
# Edit config/litellm_config.yaml with your API keys
docker run -p 4000:4000 -v $(pwd)/config:/config ghcr.io/berriai/litellm:latest --config /config/litellm_config.yaml
pip install openai && python demo/router_client.py
```

## Kubernetes
```bash
kubectl apply -f k8s/
kubectl port-forward svc/litellm 4000:4000
```
