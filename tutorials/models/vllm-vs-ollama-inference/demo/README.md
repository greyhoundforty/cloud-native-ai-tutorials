# Demo

Benchmark comparing vLLM vs Ollama on Kubernetes.

## Prerequisites
- Kubernetes with GPU, Helm >= 3.12, Python >= 3.10

## Quick Start
```bash
helm install ollama charts/ollama/ --set gpu.enabled=true
helm install vllm charts/vllm/ --set model=meta-llama/Llama-3.2-3B-Instruct --set gpu.count=1
pip install httpx rich
python benchmark/bench.py --ollama-url http://localhost:11434 --vllm-url http://localhost:8000 --concurrency 4 --requests 50
```
