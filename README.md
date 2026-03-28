# greyhoundforty-tutorials

Practical, working tutorials for cloud native developers and AI tinkerers — covering Kubernetes, infrastructure-as-code, LLM deployment, agents, and production operations.

Every tutorial ships with a **working demo** you can run today.

---

## Who this is for

- Kubernetes operators adding AI workloads to their clusters
- Platform engineers building internal ML infrastructure
- Developers integrating LLM APIs into production services
- AI hobbyists running local models on home lab hardware

---

## Tutorials

### Kubernetes

| Tutorial | Description |
|----------|-------------|
| [Getting Started with Claude API on Kubernetes](tutorials/kubernetes/getting-started-claude-api-k8s/) | Deploy a FastAPI app backed by Claude on a local kind cluster with proper secrets management |
| [Deploying an LLM-Backed App with Helm](tutorials/kubernetes/deploying-llm-app-helm/) | Package a Claude-powered service as a Helm chart, configure secrets, and manage upgrades |
| [Ollama on Kubernetes with GPU Scheduling](tutorials/kubernetes/ollama-k8s-gpu-scheduling/) | Self-host LLMs via Ollama with GPU node labeling, tolerations, and persistent storage |
| [Agentic RAG with Claude API and pgvector](tutorials/kubernetes/agentic-rag-pgvector-k8s/) | Build a retrieval-augmented generation service with pgvector running on Kubernetes |

### Infrastructure

| Tutorial | Description |
|----------|-------------|
| [Ansible + Proxmox for AI Lab Infrastructure](tutorials/infrastructure/ansible-proxmox-ai-lab/) | Automate GPU VM provisioning, GPU pass-through, and Ollama deployment on Proxmox VE |
| [Terraform for GPU Cloud Workloads](tutorials/infrastructure/terraform-gpu-cloud/) | Provision GPU instances on AWS, GCP, or Azure with automated CUDA and runtime bootstrap |

### Models

| Tutorial | Description |
|----------|-------------|
| [Fine-tuning Llama 3.2 with LoRA on a Home Lab GPU](tutorials/models/fine-tuning-lora-unsloth/) | Fine-tune a 3B model on custom data using LoRA + Unsloth on a single consumer GPU |
| [vLLM vs Ollama: Choosing Your Inference Backend](tutorials/models/vllm-vs-ollama-inference/) | Benchmark throughput and latency to pick the right inference runtime for production |
| [Prompt Caching and Cost Optimization with Claude](tutorials/models/claude-prompt-caching/) | Reduce token costs and latency using Claude's prompt caching with real benchmarks |
| [Model Routing and Fallback with LiteLLM](tutorials/models/litellm-model-routing/) | Route across providers, implement fallback chains, and enforce per-model rate limits |

### Agents

| Tutorial | Description |
|----------|-------------|
| [Building MCP Servers with the Model Context Protocol](tutorials/agents/building-mcp-servers/) | Write a Python MCP server, expose tools to Claude, and deploy on Kubernetes |
| [Multi-Agent Workflows with Claude SDK](tutorials/agents/multi-agent-claude-sdk/) | Orchestrate specialist sub-agents using the Claude Agent SDK and run as K8s batch jobs |
| [AI-Powered CLI Tools with Claude and Typer](tutorials/agents/ai-cli-typer/) | Build a codebase-aware CLI tool with streaming output, tool use, and conversation memory |

### Operations

| Tutorial | Description |
|----------|-------------|
| [LLM Observability with OpenTelemetry and Grafana](tutorials/operations/llm-observability-otel/) | Instrument LLM apps with OTEL, visualize token usage and latency in Grafana dashboards |
| [Secrets Management with HashiCorp Vault and Kubernetes](tutorials/operations/vault-k8s-secrets/) | Store and inject API keys using Vault K8s auth, External Secrets Operator, and Helm |
| [LLM Evaluation Pipelines with promptfoo and CI](tutorials/operations/promptfoo-llm-evals/) | Build automated eval suites for Claude prompts and run them in GitHub Actions |

---

## Repository Structure

```
tutorials/
  <category>/
    <tutorial-slug>/
      README.md      ← the full tutorial
      demo/          ← working demo code with setup instructions
```

Categories: `kubernetes`, `infrastructure`, `models`, `agents`, `operations`

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for how to add a new tutorial.

---

## License

MIT
