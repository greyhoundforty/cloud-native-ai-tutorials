# Demo

Full LLM observability stack: FastAPI + OTEL + Prometheus + Grafana + Tempo.

## Prerequisites
- Docker, Docker Compose, Anthropic API key

## Quick Start
```bash
export ANTHROPIC_API_KEY=<your-key>
docker compose up -d
# Grafana:    http://localhost:3000 (admin/admin)
# App:        http://localhost:8080
# Prometheus: http://localhost:9090
```
