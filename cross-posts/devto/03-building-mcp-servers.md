---
title: "Building MCP Servers with the Model Context Protocol"
published: false
description: "Go from zero to a running MCP server in Python, connect it to Claude Desktop and Claude Code, then package it in Docker and deploy it as a Kubernetes sidecar."
tags: ["ai", "python", "mcp", "claude"]
canonical_url: "https://greyhoundforty-tutorials.netlify.app/tutorials/agents/building-mcp-servers/"
---

Claude is only as useful as the context it has access to. The **Model Context Protocol (MCP)** is an open standard — think USB-C for AI context — that lets you plug any data source or tool into Claude through a single well-defined interface. Build one MCP server and any MCP-compatible client can use it.

This post walks from zero to a running Python MCP server, connected to Claude Desktop and Claude Code, then packaged in Docker and deployed as a Kubernetes sidecar.

## What you'll build

A file-system browser MCP server that exposes two tools:

- `list_directory(path)` — lists files and subdirectories under a sandboxed root
- `read_file(path)` — reads a file's content with path-traversal protection

## The three MCP primitives

| Concept | What it is | Example |
|---------|-----------|---------|
| **Tool** | A callable function Claude invokes | `read_file(path)` |
| **Resource** | URI-addressed read-only content | `file:///logs/app.log` |
| **Prompt** | A reusable prompt template | `summarize_file` |

Transport options: **stdio** (subprocess stdin/stdout, ideal for local desktop) and **Streamable HTTP** (for production/sidecar deployments).

## Scaffold the server

```bash
uv init file-browser-mcp
cd file-browser-mcp
uv add "mcp[cli]"
```

Create `src/file_browser_mcp/server.py`:

```python
import os
from pathlib import Path
from mcp.server.fastmcp import FastMCP

ROOT_DIR = Path(os.environ.get("ROOT_DIR", Path.home() / "mcp-sandbox")).resolve()
MAX_FILE_BYTES = int(os.environ.get("MAX_FILE_BYTES", 512 * 1024))


def _safe_path(relative: str) -> Path:
    resolved = (ROOT_DIR / relative).resolve()
    if not resolved.is_relative_to(ROOT_DIR):
        raise ValueError(f"Path '{relative}' escapes the sandbox root.")
    return resolved


mcp = FastMCP("file-browser")


@mcp.tool()
def list_directory(path: str = ".") -> list[dict]:
    """List contents of path relative to the sandbox root."""
    target = _safe_path(path)
    if not target.is_dir():
        raise NotADirectoryError(f"Not a directory: {path}")
    return [
        {"name": item.name, "type": "directory" if item.is_dir() else "file",
         "size_bytes": item.stat().st_size if item.is_file() else 0}
        for item in sorted(target.iterdir())
    ]


@mcp.tool()
def read_file(path: str) -> str:
    """Read text content of path relative to the sandbox root."""
    target = _safe_path(path)
    raw = target.read_bytes()
    if len(raw) > MAX_FILE_BYTES:
        return raw[:MAX_FILE_BYTES].decode("utf-8", errors="replace") + \
               f"\n\n[... truncated at {MAX_FILE_BYTES} bytes ...]"
    return raw.decode("utf-8", errors="replace")


if __name__ == "__main__":
    ROOT_DIR.mkdir(parents=True, exist_ok=True)
    mcp.run(transport="stdio")
```

Test with the interactive dev inspector:

```bash
ROOT_DIR=~/mcp-sandbox uv run mcp dev src/file_browser_mcp/server.py
# Opens browser inspector at http://localhost:5173
```

## Connect to Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "file-browser": {
      "command": "uv",
      "args": ["run", "--project", "/absolute/path/to/file-browser-mcp",
               "python", "src/file_browser_mcp/server.py"],
      "env": {"ROOT_DIR": "/Users/yourname/mcp-sandbox"}
    }
  }
}
```

Restart Claude Desktop. A hammer icon (🔨) appears in the chat input — click it to confirm the tools are registered. Then ask: *"What files are in my sandbox? Read hello.txt for me."*

## Connect to Claude Code

Option A — per-project `.claude/settings.json` (same JSON structure as above).

Option B — CLI:

```bash
claude mcp add file-browser \
  --command "uv" \
  --args "run,--project,/path/to/file-browser-mcp,python,src/file_browser_mcp/server.py" \
  --env "ROOT_DIR=/Users/yourname/mcp-sandbox"
```

Verify with `claude mcp list`, then run `/mcp` inside a session.

## Package for production (Docker + HTTP transport)

For remote deployments, switch to Streamable HTTP transport:

```python
if __name__ == "__main__":
    ROOT_DIR.mkdir(parents=True, exist_ok=True)
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "streamable-http":
        mcp.run(transport="streamable-http",
                host=os.environ.get("MCP_HOST", "0.0.0.0"),
                port=int(os.environ.get("MCP_PORT", "8000")),
                path="/mcp")
    else:
        mcp.run(transport="stdio")
```

Dockerfile:

```dockerfile
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
WORKDIR /app
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev
COPY src/ ./src/
RUN useradd -m appuser && chown -R appuser /app
USER appuser
ENV MCP_TRANSPORT=streamable-http MCP_HOST=0.0.0.0 MCP_PORT=8000 ROOT_DIR=/data
EXPOSE 8000
CMD ["uv", "run", "python", "src/file_browser_mcp/server.py"]
```

## Deploy as a Kubernetes sidecar

The sidecar pattern co-locates the MCP server in the same Pod as your app. Both communicate over `localhost` — no Service exposure needed.

```yaml
containers:
  - name: app
    image: your-app:latest
    env:
      - name: MCP_SERVER_URL
        value: "http://localhost:8000/mcp"

  - name: mcp-file-browser
    image: file-browser-mcp:latest
    env:
      - name: MCP_TRANSPORT
        value: streamable-http
      - name: ROOT_DIR
        value: /data
    volumeMounts:
      - name: shared-docs
        mountPath: /data
        readOnly: true
    resources:
      requests: {cpu: 50m, memory: 64Mi}
      limits:   {cpu: 200m, memory: 128Mi}
    readinessProbe:
      httpGet: {path: /mcp, port: 8000}
```

## What's next

- **Add authentication** — MCP supports OAuth 2.1, or add a simple API-key header check
- **Expose a database tool** — swap file tools for `execute_query` using asyncpg; the FastMCP pattern is identical
- **Multi-server setups** — Claude Desktop and Claude Code support multiple simultaneous MCP servers
- **Publish to [mcp.so](https://mcp.so)** — the community MCP registry

---

→ **Full tutorial + demo code:** [greyhoundforty-tutorials.netlify.app](https://greyhoundforty-tutorials.netlify.app/tutorials/agents/building-mcp-servers/)
