# Contributing a Tutorial

Thanks for adding to this collection. Follow this guide to keep the quality bar consistent.

---

## Tutorial Structure

Every tutorial lives at:

```
tutorials/<category>/<tutorial-slug>/
  README.md      ← the full tutorial (Markdown)
  demo/
    README.md    ← setup and run instructions
    ...          ← all working demo code
```

### Categories

| Category | Use for |
|----------|---------|
| `kubernetes` | Deploying AI workloads on K8s (Helm, manifests, GPU scheduling) |
| `infrastructure` | IaC for AI labs (Terraform, Ansible, Proxmox) |
| `models` | Model serving, fine-tuning, benchmarking, and cost optimization |
| `agents` | Agent frameworks, MCP servers, multi-agent workflows |
| `operations` | Observability, secrets, evals, and production ops for LLM apps |

### Naming

- Slugs are lowercase, hyphen-separated: `deploying-llm-app-helm`
- Be specific — prefer `claude-prompt-caching` over `caching`

---

## Tutorial README (`README.md`)

The tutorial is the primary artifact. It should be self-contained and teach by doing.

**Required sections:**

1. **Title and one-line description** — what the reader will build
2. **Overview** — why this matters and what problem it solves (2–3 paragraphs)
3. **Prerequisites** — exact versions, tools, and credentials needed
4. **Step-by-step walkthrough** — numbered steps with code blocks for every command
5. **Key concepts** — explain the important decisions and trade-offs
6. **Next steps** — where to go from here (links to related tutorials)

**Style:**

- Target intermediate engineers (know Kubernetes basics, have used an LLM API)
- Show real commands, real config files, real error messages
- Explain *why*, not just *what*
- Keep code blocks copy-paste ready — no ellipses, no `...` placeholders
- Use fenced code blocks with language identifiers (` ```bash `, ` ```yaml `, etc.)

---

## Demo Code (`demo/`)

The demo must work. A reader who follows the `demo/README.md` should reach a running system.

**Requirements:**

- `demo/README.md` — lists prerequisites, step-by-step quickstart, and any teardown commands
- All code is complete and runnable (no TODOs, no placeholder values except clearly marked `<your-key>`)
- Secrets handled via environment variables or K8s secrets — never hardcoded
- Include a `.gitignore` if the demo generates local state (`.terraform/`, `__pycache__/`, etc.)
- If the demo targets Kubernetes, provide either Helm charts or plain manifests

**Languages and tools:**

- Python demos: use `requirements.txt` or `pyproject.toml`, target Python ≥ 3.10
- Go demos: use `go.mod`
- Terraform: pin provider versions in `required_providers`
- Helm: charts must pass `helm lint` with zero errors

---

## Review Checklist

Before opening a PR, verify:

- [ ] `tutorials/<category>/<slug>/README.md` exists and follows the required sections
- [ ] `tutorials/<category>/<slug>/demo/README.md` exists with setup + run instructions
- [ ] All demo code is present and complete
- [ ] Code blocks in the tutorial match the actual demo code
- [ ] `helm lint` passes if Helm charts are included
- [ ] No secrets or API keys are committed
- [ ] Root `README.md` tutorial index is updated with the new entry
- [ ] Slug and category follow the naming conventions above

---

## Adding to the Index

When your tutorial is ready, add a row to the appropriate category table in the root `README.md`:

```markdown
| [Your Tutorial Title](tutorials/<category>/<slug>/) | One-line description of what the reader will build |
```

---

## Questions

Open an issue or ask in the project's discussion thread.
