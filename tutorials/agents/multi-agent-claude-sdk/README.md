# Multi-Agent Workflows with the Claude Agent SDK

Building a research pipeline that plans, searches, and writes — end to end

---

A single Claude call can summarise a document or answer a question, but many real workflows demand more: one model instance browses the web while another evaluates its findings, a planner decides what work to do before a specialist does it, and a separate editor polishes the output before it ships. That is the multi-agent pattern, and Anthropic's Agent SDK makes it straightforward to compose.

This tutorial walks you through a working three-stage research pipeline:

1. **Planner** — takes a topic and writes a structured research plan to disk
2. **Researcher** — reads the plan, searches the web, and compiles findings
3. **Writer** — synthesises the research into a polished markdown article

By the end you will have runnable Python code, an understanding of the tracing and debugging story, and a Kubernetes Job manifest that deploys the pipeline with a shared volume for inter-agent state.

---

## When multi-agent beats a single prompt

Before reaching for agents, ask whether the task genuinely requires it. A large system prompt handles many cases that seem like they need agents. The four signals that push toward a multi-agent design are:

| Signal | Example |
|--------|---------|
| **Context growth** | An agent that browses 20 URLs will overflow a single context window |
| **Specialisation** | A planner and a researcher reason differently; forcing one model to be both degrades both |
| **Parallelism** | Research sub-topics can be explored simultaneously |
| **Error isolation** | If the research stage fails you can re-run it without re-running the planner |

The orchestrator/worker pattern maps directly to these concerns. An **orchestrator** decides what needs doing and in what order. **Workers** (sub-agents) perform bounded, well-defined tasks and hand their output back. The orchestrator never needs to know the implementation details of each worker; workers never need to know the bigger plan.

```
User prompt
    │
    ▼
┌──────────────┐
│  Planner     │  decides: what subtopics to research
│  (Agent)     │  writes:  plan.json to /workspace
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  Researcher  │  reads:  plan.json
│  (Agent)     │  uses:   WebSearch, WebFetch
│              │  writes: research.md to /workspace
└──────┬───────┘
       │
       ▼
┌──────────────┐
│  Writer      │  reads:  research.md
│  (Agent)     │  writes: report.md to /workspace
└──────────────┘
```

Each stage is a Claude agent with its own tool allowlist. State flows through the filesystem — a shared directory that all stages mount.

---

## Agent SDK setup

Install the SDK and its async runtime:

```bash
pip install claude-agent-sdk anyio
```

The SDK requires `claude` (the Claude Code CLI) to be available in `PATH`. Install it globally:

```bash
npm install -g @anthropic/claude-code
```

Export your API key:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

The core import surface is small:

```python
from claude_agent_sdk import (
    query,               # run an agent, returns an async iterator
    ClaudeAgentOptions,  # configuration: tools, cwd, permission mode, etc.
    ResultMessage,       # the agent's final answer
    AssistantMessage,    # individual turns (useful for streaming/tracing)
    SystemMessage,       # lifecycle events (session init, etc.)
    TextBlock,           # a text content block inside an AssistantMessage
    AgentDefinition,     # used when defining named sub-agents
)
```

`query()` is the primary entry point. It takes a `prompt` and an `options` object, and returns an async iterator of message events:

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

---

## Building the research pipeline

### Shared workspace

Every stage reads and writes to a single directory. Locally this is `./workspace`; in Kubernetes it becomes a PersistentVolumeClaim mounted at `/workspace`.

```python
import os
from pathlib import Path

WORKSPACE = Path(os.environ.get("WORKSPACE", "./workspace"))
WORKSPACE.mkdir(parents=True, exist_ok=True)
```

### Stage 1 — Planner agent

The planner knows nothing about web search. Its only tool is `Write` (to produce `plan.json`) and `Read` (in case it wants to verify what it wrote). Keeping the tool allowlist minimal reduces the risk of the agent going off-script.

```python
async def run_planner(topic: str) -> dict:
    plan_path = WORKSPACE / "plan.json"

    prompt = f"""You are a research planner.

Topic: {topic!r}

Break this topic into 3–5 focused sub-topics. For each sub-topic:
- Write one precise research question
- Propose 2 specific search queries

Write the plan as JSON to {plan_path}:
{{
  "topic": "<string>",
  "subtopics": [
    {{
      "id": "1",
      "title": "<short title>",
      "question": "<one-sentence research question>",
      "search_queries": ["<query 1>", "<query 2>"]
    }}
  ]
}}

After writing the file, confirm with a one-line summary.
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

Key points:

- `cwd=str(WORKSPACE)` anchors all relative file paths to the shared workspace.
- `permission_mode="bypassPermissions"` suppresses interactive prompts, which is required for unattended pipeline execution. Always pair it with `allow_dangerously_skip_permissions=True`.
- The planner has no `WebSearch` — it cannot go off and browse when it should be planning.

### Stage 2 — Researcher agent

The researcher is the most tool-rich agent. It needs `Read` (to load the plan), `Write` (to save findings), `WebSearch`, and `WebFetch`.

```python
async def run_researcher(plan: dict) -> None:
    plan_path = WORKSPACE / "plan.json"
    research_path = WORKSPACE / "research.md"

    prompt = f"""You are a research specialist with web search access.

Read the research plan from {plan_path}.

For each sub-topic:
1. Run both suggested search queries with WebSearch
2. Fetch the 1–2 most relevant results with WebFetch
3. Extract key facts, statistics, quotes, and source URLs

Write all findings to {research_path} as structured markdown:

# Research: <topic>
## <Sub-topic title>
<3–4 paragraphs of findings>
### Sources
- [Title](URL)
"""
    async for msg in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            cwd=str(WORKSPACE),
            allowed_tools=["Read", "Write", "WebSearch", "WebFetch"],
            permission_mode="bypassPermissions",
            allow_dangerously_skip_permissions=True,
            max_turns=40,   # researcher may need many search/fetch iterations
        ),
    ):
        if isinstance(msg, ResultMessage):
            print(f"[researcher] {msg.result[:100]}")
```

`max_turns=40` gives the researcher room to run multiple search/fetch cycles. Without a cap the agent runs until it is done; with a cap you get a budget guardrail.

### Stage 3 — Writer agent

The writer is the simplest agent: read one file, write another. No web access needed.

```python
async def run_writer(topic: str) -> None:
    research_path = WORKSPACE / "research.md"
    report_path = WORKSPACE / "report.md"

    prompt = f"""You are a technical writer for cloud-native developers.

Read the research from {research_path}.

Write a polished article about {topic!r}:
- Compelling introduction explaining why this matters
- H2 sections for each major theme
- Code snippets or CLI examples where relevant
- Comparison table if evaluating multiple approaches
- "Key Takeaways" as the final section (3–5 bullets)
- Target: 1 800–2 500 words
- Audience: senior engineers who are pragmatic and time-constrained

Write the article to {report_path} in markdown.
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

### Wiring the pipeline

```python
import anyio
import sys

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

Run it:

```bash
python pipeline.py "eBPF for Kubernetes observability"
```

---

## Tool use across agents

Each stage's `allowed_tools` list tells the SDK exactly which built-in tools that agent may call. The full list of built-in tools is:

| Tool | What it does |
|------|-------------|
| `Read` | Read a file |
| `Write` | Create or overwrite a file |
| `Edit` | Make precise edits to an existing file |
| `Bash` | Run shell commands |
| `Glob` | Find files by pattern |
| `Grep` | Search file contents |
| `WebSearch` | Search the web |
| `WebFetch` | Fetch and parse a URL |
| `AskUserQuestion` | Prompt the user interactively |
| `Agent` | Spawn a named sub-agent |

Limiting the tool allowlist per stage is the single most impactful safety and reliability control available. A writer that cannot call `Bash` cannot accidentally delete files; a planner that cannot call `WebSearch` cannot hallucinate search results instead of writing a plan.

### Custom tools via MCP

For domain-specific tools — querying an internal API, running a linter, posting to Slack — wrap an MCP (Model Context Protocol) server:

```python
from claude_agent_sdk import query, ClaudeAgentOptions

async for msg in query(
    prompt="Check the CI status for PR #42 and summarise any failures",
    options=ClaudeAgentOptions(
        mcp_servers={
            "github": {
                "command": "npx",
                "args": ["-y", "@modelcontextprotocol/server-github"],
                "env": {"GITHUB_TOKEN": os.environ["GITHUB_TOKEN"]},
            }
        }
    ),
):
    ...
```

The agent can then call any tool exposed by the `github` MCP server in addition to built-in tools.

### Named sub-agents

When you want an orchestrator to delegate tasks at runtime — rather than a fixed sequential pipeline — define named sub-agents and give the orchestrator the `Agent` tool:

```python
from claude_agent_sdk import AgentDefinition

async for msg in query(
    prompt=f"Research '{topic}' and write a report. Use the researcher to gather facts and the writer to polish them.",
    options=ClaudeAgentOptions(
        allowed_tools=["Read", "Write", "Agent"],
        agents={
            "researcher": AgentDefinition(
                description="Searches the web and compiles research findings.",
                prompt="You are a research specialist. Use WebSearch and WebFetch to find information, then write your findings to research.md.",
                tools=["Read", "Write", "WebSearch", "WebFetch"],
            ),
            "writer": AgentDefinition(
                description="Synthesises research into a polished article.",
                prompt="You are a technical writer. Read research.md and produce a polished report.md.",
                tools=["Read", "Write"],
            ),
        },
    ),
):
    if isinstance(msg, ResultMessage):
        print(msg.result)
```

With this approach the orchestrator decides when to call each sub-agent and can handle errors or re-delegations dynamically. The sequential pipeline is better when the order is fixed and you want explicit control at the Python level; the orchestrator pattern is better when the agent itself needs to reason about sequencing.

---

## Tracing and debugging

### Inspecting message events

`query()` emits a stream of typed events. Log them to understand what the agent is doing:

```python
from claude_agent_sdk import (
    AssistantMessage, ResultMessage, SystemMessage, TextBlock,
)

async for msg in query(prompt=prompt, options=options):
    if isinstance(msg, SystemMessage) and msg.subtype == "init":
        # Emitted once at the start; contains the session ID
        session_id = msg.data.get("session_id")
        print(f"session: {session_id}")

    elif isinstance(msg, AssistantMessage):
        # Every turn the model takes — tools calls, intermediate reasoning, text
        for block in msg.content:
            if isinstance(block, TextBlock):
                print(f"turn: {block.text[:200]}")
        if msg.usage:
            print(f"tokens: in={msg.usage['input_tokens']} out={msg.usage['output_tokens']}")

    elif isinstance(msg, ResultMessage):
        # Final answer
        print(f"result: {msg.result}")
        print(f"stop_reason: {msg.stop_reason}")
```

`AssistantMessage.content` is a list of blocks. In addition to `TextBlock` you will see `ToolUseBlock` (when the agent invokes a tool) and the corresponding tool result in the next turn. Logging these gives you a full trace of every action the agent took.

### Session history

Every `query()` run produces a session that is persisted locally. Replay it after the fact:

```python
from claude_agent_sdk import list_sessions, get_session_messages

sessions = list_sessions()
latest = sessions[0]
print(f"Session {latest.session_id} in {latest.cwd}")

for msg in get_session_messages(latest.session_id):
    print(msg)
```

This is useful for post-mortem debugging: if the pipeline fails at stage 2 you can inspect exactly which web searches were run, what was fetched, and where the agent went wrong.

### Resuming a session

If a pipeline stage fails partway through, resume instead of restarting:

```python
session_id = None

async for msg in query(prompt=researcher_prompt, options=options):
    if isinstance(msg, SystemMessage) and msg.subtype == "init":
        session_id = msg.data.get("session_id")
    ...

# Later, after fixing the issue that caused the failure:
async for msg in query(
    prompt="Continue from where you left off. Write the remaining sub-topics to research.md.",
    options=ClaudeAgentOptions(resume=session_id),
):
    ...
```

---

## Deploy as a Kubernetes Job

The sequential pipeline maps cleanly to a Kubernetes Job with **init containers**. Each stage runs as a separate init container that must complete successfully before the next one starts. All stages share a PersistentVolumeClaim mounted at `/workspace`.

### Container image

```dockerfile
FROM python:3.12-slim

# Install Node.js (required for Claude Code CLI)
RUN apt-get update && apt-get install -y nodejs npm && rm -rf /var/lib/apt/lists/*
RUN npm install -g @anthropic/claude-code

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY pipeline.py .
```

Build and push:

```bash
docker build -t ghcr.io/your-org/research-pipeline:latest .
docker push ghcr.io/your-org/research-pipeline:latest
```

### Kubernetes manifests

Create the API key secret:

```bash
kubectl create secret generic anthropic-creds \
  --from-literal=api-key="$ANTHROPIC_API_KEY"
```

Apply the job:

```bash
kubectl apply -f k8s/job.yaml
```

The Job manifest (`k8s/job.yaml` in this repo) defines:

```yaml
initContainers:
  - name: planner
    image: ghcr.io/your-org/research-pipeline:latest
    command: ["python", "pipeline.py", "--stage", "planner", "$(RESEARCH_TOPIC)"]
    env:
      - name: ANTHROPIC_API_KEY
        valueFrom:
          secretKeyRef:
            name: anthropic-creds
            key: api-key
      - name: WORKSPACE
        value: /workspace
    volumeMounts:
      - name: workspace
        mountPath: /workspace

  - name: researcher
    # ... same pattern, --stage researcher

  - name: writer
    # ... same pattern, --stage writer

containers:
  - name: report-publisher
    image: busybox
    command: ["sh", "-c", "cat /workspace/report.md"]
    volumeMounts:
      - name: workspace
        mountPath: /workspace

volumes:
  - name: workspace
    persistentVolumeClaim:
      claimName: research-workspace
```

The main container (`report-publisher`) simply cats the finished report — making it visible in `kubectl logs`. In a real pipeline you would replace this with a step that uploads the report to object storage, posts it to Notion, or triggers a review workflow.

### Observing the job

```bash
# Watch init containers progress
kubectl get job research-pipeline -w

# Stream logs from the researcher stage
kubectl logs -f job/research-pipeline -c researcher

# Get the final report
kubectl logs job/research-pipeline -c report-publisher
```

If a stage fails, Kubernetes will not start the next init container. Inspect the failed container:

```bash
kubectl logs job/research-pipeline -c researcher --previous
```

### Handling retries and idempotency

The Job spec includes `backoffLimit: 1`, meaning it retries once on failure. For idempotency:

- The planner overwrites `plan.json` on every run — safe.
- The researcher overwrites `research.md` — safe.
- The writer overwrites `report.md` — safe.

If you want to preserve intermediate outputs across retries, write to a timestamped filename and symlink the latest:

```python
from datetime import datetime
ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
research_path = WORKSPACE / f"research_{ts}.md"
latest_link = WORKSPACE / "research.md"
# ... write to research_path, then:
latest_link.unlink(missing_ok=True)
latest_link.symlink_to(research_path)
```

---

## Key Takeaways

- **Specialise each agent** — narrow `allowed_tools` lists make agents more predictable and easier to debug. A writer that cannot search the web will not hallucinate search results.
- **Use the filesystem as a message bus** — inter-agent state via shared files is simple, inspectable, and survives container restarts. No message queue required for sequential pipelines.
- **Log every `AssistantMessage`** — the event stream from `query()` is your observability layer. Token counts, tool calls, and intermediate text are all there without any additional instrumentation.
- **Init containers + PVC** is the idiomatic Kubernetes pattern for sequential, stateful batch jobs. Each stage is independently observable via `kubectl logs -c <stage-name>`.
- **Session resumption saves money** — if stage 2 fails after 30 search calls, resume the session instead of restarting from scratch. The SDK persists session history locally.

---

## What's next

- Add a **critic agent** that reviews `report.md` and flags weak sections before the job completes.
- Swap the filesystem bus for a **Redis stream** when you need multiple researchers working in parallel.
- Add **cost guardrails** with `max_budget_usd` in `ClaudeAgentOptions` to cap spend per stage.
- Explore **MCP servers** for domain-specific tools: GitHub for code context, Postgres for data, Slack for notifications.
