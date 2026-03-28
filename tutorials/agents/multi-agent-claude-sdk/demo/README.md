# Demo

Multi-agent research pipeline with Claude SDK.

## Prerequisites
- Python >= 3.10, Anthropic API key

## Quick Start
```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=<your-key>
python pipeline.py --topic "Kubernetes networking"
```

## Kubernetes Batch Job
```bash
kubectl create secret generic anthropic-key --from-literal=ANTHROPIC_API_KEY=<your-key>
kubectl apply -f k8s/job.yaml
kubectl logs job/multi-agent-pipeline -f
```
