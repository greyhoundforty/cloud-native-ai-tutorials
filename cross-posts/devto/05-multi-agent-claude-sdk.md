---
title: "Multi-Agent Workflows with the Claude Agent SDK"
published: false
description: "Build a three-stage research pipeline where a Planner, Researcher, and Writer agent each do one job well — with filesystem state sharing, session tracing, and a Kubernetes Job manifest."
tags: ["ai", "python", "agents", "claude"]
canonical_url: "https://greyhoundforty-tutorials.netlify.app/tutorials/agents/multi-agent-claude-sdk/"
---

A single Claude call handles summarisation and Q&A well. But some workflows genuinely demand more: a planner deciding what to research before the researcher starts, a writer polishing output that came from a web-search agent, each stage isolated so a failure doesn't throw away work that succeeded.

That's the multi-agent pattern. Anthropic's Agent SDK makes it straightforward to compose.

## When multi-agent beats a single prompt

| Signal | Example |
|--------|---------|
| **Context growth** | Browsing 20 URLs overflows a single context window |
| **Specialisation** | A planner and researcher reason differently; one model doing both degrades both |
| **Parallelism** | Research sub-topics can be explored simultaneously |
| **Error isolation** | Re-run the research stage without re-running the planner |

## What you'll build

A three-stage research pipeline:

```
User prompt
    ↓
Planner   → writes plan.json to /workspace
    ↓
Researcher → reads plan.json, searches the web, writes research.md
    ↓
Writer     → reads research.md, writes report.md
```

Each stage is a Claude agent with a minimal tool allowlist. State flows through the filesystem.

## SDK setup

```bash
pip install claude-agent-sdk anyio
npm install -g @anthropic/claude-code  # SDK requires the Claude Code CLI in PATH
export ANTHROPIC_API_KEY="sk-ant-..."
```

The core entry point is `query()`:

```python
import anyio
from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage

async def main():
    async for msg in query(
        prompt="Summarise the key benefits of eBPF in one paragraph",
        options=ClaudeAgentOptions(allowed_tools=[]),
    ):
        if isinstance(msg, ResultMessage):
            print(msg.result)

anyio.run(main)
```

## Stage 1 — Planner

The planner's only tools are `Write` and `Read`. No web access — it cannot hallucinate search results instead of writing a plan:

```python
async def run_planner(topic: str) -> dict:
    plan_path = WORKSPACE / "plan.json"
    prompt = f"""You are a research planner.
Topic: {topic!r}
Break this topic into 3–5 sub-topics. For each, write one research question
and propose 2 specific search queries.
Write the plan as JSON to {plan_path}, then confirm with a one-line summary.
"""
    async for msg in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            cwd=str(WORKSPACE),
            allowed_tools=["Write", "Read"],
            permission_mode="bypassPermissions",
            allow_dangerously_skip_permissions=True,
        ),
    ):
        if isinstance(msg, ResultMessage):
            print(f"[planner] {msg.result[:100]}")

    with open(plan_path) as f:
        return json.load(f)
```

## Stage 2 — Researcher

The researcher gets the full tool allowlist it needs: `Read`, `Write`, `WebSearch`, `WebFetch`. The `max_turns=40` cap gives it room to run multiple search/fetch cycles while staying budget-bounded:

```python
async def run_researcher(plan: dict) -> None:
    prompt = f"""You are a research specialist.
Read the plan from {WORKSPACE / 'plan.json'}.
For each sub-topic, run both suggested queries with WebSearch,
fetch the 1–2 most relevant results, and extract key facts and source URLs.
Write all findings to {WORKSPACE / 'research.md'} as structured markdown.
"""
    async for msg in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            cwd=str(WORKSPACE),
            allowed_tools=["Read", "Write", "WebSearch", "WebFetch"],
            permission_mode="bypassPermissions",
            allow_dangerously_skip_permissions=True,
            max_turns=40,
        ),
    ):
        if isinstance(msg, ResultMessage):
            print(f"[researcher] {msg.result[:100]}")
```

## Stage 3 — Writer

The writer reads one file and writes another. No web access, no shell access:

```python
async def run_writer(topic: str) -> None:
    prompt = f"""You are a technical writer for cloud-native developers.
Read the research from {WORKSPACE / 'research.md'}.
Write a polished 1800–2500 word article about {topic!r} targeting senior engineers.
Include an intro, H2 sections per theme, code snippets, and a Key Takeaways section.
Write to {WORKSPACE / 'report.md'}.
"""
    async for msg in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            cwd=str(WORKSPACE),
            allowed_tools=["Read", "Write"],
            permission_mode="bypassPermissions",
            allow_dangerously_skip_permissions=True,
        ),
    ):
        if isinstance(msg, ResultMessage):
            print(f"[writer] {msg.result[:100]}")
```

## Wire it together

```python
async def main() -> None:
    topic = " ".join(sys.argv[1:]) or "eBPF for Kubernetes observability"
    WORKSPACE.mkdir(parents=True, exist_ok=True)

    plan = await run_planner(topic)
    await run_researcher(plan)
    await run_writer(topic)

    report = WORKSPACE / "report.md"
    print(f"\nDone. Report at {report} ({report.stat().st_size:,} bytes)")

anyio.run(main)
```

```bash
python pipeline.py "eBPF for Kubernetes observability"
```

## Tracing with the event stream

`query()` emits typed events — log them for a full trace without extra instrumentation:

```python
from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, TextBlock

async for msg in query(prompt=prompt, options=options):
    if isinstance(msg, SystemMessage) and msg.subtype == "init":
        print(f"session: {msg.data.get('session_id')}")
    elif isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, TextBlock):
                print(f"turn: {block.text[:200]}")
        if msg.usage:
            print(f"tokens: in={msg.usage['input_tokens']} out={msg.usage['output_tokens']}")
    elif isinstance(msg, ResultMessage):
        print(f"result: {msg.result}")
```

If a stage fails, capture the `session_id` and resume instead of restarting — the SDK persists session history locally, so you don't re-run work that already succeeded.

## Deploy as a Kubernetes Job

The sequential pipeline maps cleanly to init containers. Each stage must complete successfully before the next starts. All stages share a PVC mounted at `/workspace`:

```yaml
initContainers:
  - name: planner
    image: ghcr.io/your-org/research-pipeline:latest
    command: ["python", "pipeline.py", "--stage", "planner", "$(RESEARCH_TOPIC)"]
    env:
      - name: ANTHROPIC_API_KEY
        valueFrom:
          secretKeyRef: {name: anthropic-creds, key: api-key}
      - name: WORKSPACE
        value: /workspace
    volumeMounts:
      - {name: workspace, mountPath: /workspace}

  - name: researcher
    # ...same pattern, --stage researcher

  - name: writer
    # ...same pattern, --stage writer

containers:
  - name: report-publisher
    image: busybox
    command: ["sh", "-c", "cat /workspace/report.md"]
    volumeMounts:
      - {name: workspace, mountPath: /workspace}

volumes:
  - name: workspace
    persistentVolumeClaim:
      claimName: research-workspace
```

Observe each stage independently:

```bash
kubectl logs -f job/research-pipeline -c researcher
kubectl logs    job/research-pipeline -c report-publisher
```

## Key takeaways

- **Narrow `allowed_tools` per stage** — the single most impactful reliability control. A writer that can't call `WebSearch` won't hallucinate search results.
- **Filesystem as message bus** — simple, inspectable, survives container restarts. No message queue needed for sequential pipelines.
- **Init containers + PVC** — idiomatic K8s for sequential stateful batch jobs; each stage is independently observable.
- **Session resumption saves money** — if stage 2 fails after 30 search calls, resume it instead of restarting.

---

→ **Full tutorial + demo code:** [greyhoundforty-tutorials.netlify.app](https://greyhoundforty-tutorials.netlify.app/tutorials/agents/multi-agent-claude-sdk/)
