# Demo

File Browser MCP server for Claude.

## Prerequisites
- Python >= 3.11, Docker (optional)

## Quick Start (Local)
```bash
cd demo-server
pip install -e .
python -m file_browser_mcp
```

## Docker
```bash
docker build -t file-browser-mcp demo-server/
docker run -p 8000:8000 file-browser-mcp
```

## Kubernetes
```bash
kubectl apply -f demo-server/k8s/
```
