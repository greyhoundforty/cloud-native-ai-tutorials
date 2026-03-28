# Demo

Helm chart for Ollama on a GPU Kubernetes node.

## Prerequisites
- K8s cluster with GPU node, Helm >= 3.12, NVIDIA device plugin

## Quick Start
```bash
kubectl label node <gpu-node> accelerator=nvidia
helm install ollama charts/ollama/ --set gpu.enabled=true,gpu.count=1
kubectl exec -it deploy/ollama -- ollama pull llama3.2
kubectl port-forward svc/ollama 11434:11434
curl http://localhost:11434/api/generate -d '{"model":"llama3.2","prompt":"Hello"}'
```
