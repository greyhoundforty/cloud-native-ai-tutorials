---
tags:
  - home
---

# Cloud Native AI Tutorials

Practical, working tutorials for **cloud native developers and AI tinkerers** — covering Kubernetes, infrastructure-as-code, LLM deployment, agent frameworks, and production operations for AI services.

Every tutorial ships with a **working demo** you can run today.

---

## Featured Tutorials

<div class="grid cards" markdown>

-   :simple-kubernetes: **[Getting Started with Claude API on K8s](tutorials/kubernetes/getting-started-claude-api-k8s.md)**

    Deploy your first LLM-backed FastAPI service on a local Kubernetes cluster with proper secrets management.

-   :simple-ansible: **[Ansible + Proxmox for AI Lab Infrastructure](tutorials/infrastructure/ansible-proxmox-ai-lab.md)**

    Automate GPU VM provisioning and Ollama deployment on bare-metal Proxmox VE.

-   :material-robot: **[Multi-Agent Workflows with Claude SDK](tutorials/agents/multi-agent-claude-sdk.md)**

    Orchestrate specialist sub-agents using the Claude Agent SDK and deploy as Kubernetes batch jobs.

-   :material-chart-line: **[LLM Observability with OpenTelemetry](tutorials/operations/llm-observability-otel.md)**

    Instrument LLM apps with OTEL, visualize token usage and latency in Grafana.

</div>

---

## Browse by Category

### :simple-kubernetes: Kubernetes

Deploy and scale AI workloads on Kubernetes — from local kind clusters to production GPU nodes.

| Tutorial | What you'll build |
|----------|-------------------|
| [Getting Started with Claude API on K8s](tutorials/kubernetes/getting-started-claude-api-k8s.md) | FastAPI app proxying Claude, deployed with K8s manifests |
| [Deploying an LLM-Backed App with Helm](tutorials/kubernetes/deploying-llm-app-helm.md) | Helm chart for a Claude-powered service with secret management |
| [Ollama on Kubernetes with GPU Scheduling](tutorials/kubernetes/ollama-k8s-gpu-scheduling.md) | Self-hosted Ollama with GPU tolerations, PVC storage, and Ingress |
| [Agentic RAG with Claude API and pgvector](tutorials/kubernetes/agentic-rag-pgvector-k8s.md) | RAG service with pgvector on K8s and Claude for synthesis |

### :material-server: Infrastructure

Provision and configure AI infrastructure with Ansible and Terraform.

| Tutorial | What you'll build |
|----------|-------------------|
| [Ansible + Proxmox for AI Lab](tutorials/infrastructure/ansible-proxmox-ai-lab.md) | Repeatable GPU VM fleet with IOMMU pass-through and Ollama |
| [Terraform for GPU Cloud Workloads](tutorials/infrastructure/terraform-gpu-cloud.md) | GPU instance provisioned on AWS/GCP/Azure with CUDA bootstrapped |

### :material-cube: Models

Serve, fine-tune, benchmark, and optimize LLMs.

| Tutorial | What you'll build |
|----------|-------------------|
| [Fine-tuning Llama 3.2 with LoRA on a Home Lab GPU](tutorials/models/fine-tuning-lora-unsloth.md) | Fine-tuned 3B model with Unsloth/LoRA on a single consumer GPU |
| [vLLM vs Ollama: Choosing Your Inference Backend](tutorials/models/vllm-vs-ollama-inference.md) | Benchmark comparing throughput, latency, and memory for both runtimes |
| [Prompt Caching with Claude API](tutorials/models/claude-prompt-caching.md) | Benchmark showing token cost and latency savings from prompt caching |
| [Model Routing with LiteLLM](tutorials/models/litellm-model-routing.md) | Multi-provider proxy with fallback chains and per-model rate limits |

### :material-robot-outline: Agents

Build agent frameworks, MCP servers, and AI-powered developer tools.

| Tutorial | What you'll build |
|----------|-------------------|
| [Building MCP Servers](tutorials/agents/building-mcp-servers.md) | Python MCP server exposing file tools to Claude, deployed on K8s |
| [Multi-Agent Workflows with Claude SDK](tutorials/agents/multi-agent-claude-sdk.md) | Research-review pipeline with specialist sub-agents |
| [AI-Powered CLI Tools with Typer](tutorials/agents/ai-cli-typer.md) | codeqa: a CLI for code review, explanation, and codebase Q&A |

### :material-wrench: Operations

Observability, secrets management, and automated evaluation for production LLM services.

| Tutorial | What you'll build |
|----------|-------------------|
| [LLM Observability with OTel and Grafana](tutorials/operations/llm-observability-otel.md) | Full observability stack with token metrics, latency, and traces |
| [Secrets Management with Vault and K8s](tutorials/operations/vault-k8s-secrets.md) | API key injection via Vault K8s auth and External Secrets Operator |
| [LLM Eval Pipelines with promptfoo](tutorials/operations/promptfoo-llm-evals.md) | Automated eval suite for Claude prompts with CI integration |

---

## Quick Start

Pick a tutorial, follow the steps, run the demo:

```bash
git clone https://github.com/greyhoundforty/greyhoundforty-tutorials
cd greyhoundforty-tutorials/tutorials/<category>/<tutorial-slug>/demo
cat README.md   # setup + run instructions
```

---

## Contributing

See the [Contributing Guide](contributing.md) to add a new tutorial.
