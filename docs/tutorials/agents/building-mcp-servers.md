---
tags:
  - mcp
  - claude-api
  - python
  - kubernetes
---

# Building MCP Servers with the Model Context Protocol

Claude is only as useful as the context it has access to. The **Model Context Protocol (MCP)** is an open standard that lets you plug any data source or tool into Claude—and any other MCP-compatible client—through a single, well-defined interface. Instead of writing bespoke prompt-stuffing glue for every integration, you build one MCP server and any client that speaks the protocol can use it.

This tutorial walks you from zero to a running MCP server that exposes a file-system browser tool, connects it to Claude Desktop and Claude Code, and finishes by packaging the server in Docker and deploying it as a Kubernetes sidecar.

**What you'll build:** A Python MCP server that lets Claude list and read files from an approved directory—end-to-end, with a live demo in Claude Desktop.

---

## Table of Contents

1. [What is MCP and why does it matter?](#1-what-is-mcp-and-why-does-it-matter)
2. [Prerequisites](#2-prerequisites)
3. [Scaffold the minimal MCP server](#3-scaffold-the-minimal-mcp-server)
4. [Expose the file-system browser tool](#4-expose-the-file-system-browser-tool)
5. [Connect to Claude Desktop for live testing](#5-connect-to-claude-desktop-for-live-testing)
6. [Connect to Claude Code (CLI)](#6-connect-to-claude-code-cli)
7. [Package as a Docker container](#7-package-as-a-docker-container)
8. [Deploy to Kubernetes as a sidecar](#8-deploy-to-kubernetes-as-a-sidecar)
9. [Next steps](#9-next-steps)

---

## 1. What is MCP and why does it matter?

MCP is an **open protocol**—think USB-C but for AI context. It defines how clients (Claude Desktop, Claude Code, any SDK-based app) discover and call **tools**, **resources**, and **prompts** served by arbitrary backends.

```
┌──────────────────────────────────────────────────────┐
│                    MCP Client                        │
│  (Claude Desktop / Claude Code / your app)           │
└────────────────────┬─────────────────────────────────┘
                     │  MCP protocol (JSON-RPC 2.0)
          ┌──────────┴──────────┐
          │     MCP Server      │  ← you build this
          │  tools / resources  │
          └──────────┬──────────┘
                     │
          ┌──────────┴──────────┐
          │  Your data / APIs   │
          │  (files, DBs, SaaS) │
          └─────────────────────┘
```

Three concepts every MCP server can expose:

| Concept | What it is | Example |
|---------|-----------|---------|
| **Tool** | A callable function Claude invokes | `read_file(path)` |
| **Resource** | URI-addressed read-only content | `file:///logs/app.log` |
| **Prompt** | A reusable prompt template | `summarize_file` |

The server communicates over one of several **transports**:

- **stdio** — subprocess stdin/stdout; perfect for local desktop clients
- **Streamable HTTP** — HTTP with optional SSE streaming; ideal for production/sidecar deployments

---

## 2. Prerequisites

| Requirement | Version |
|------------|---------|
| Python | 3.11+ |
| [uv](https://docs.astral.sh/uv/) | latest |
| Claude Desktop | latest (for desktop testing) |
| Docker | 24+ (for containerisation) |
| kubectl + a K8s cluster | any 1.28+ cluster |

Install `uv` if you don't have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

## 3. Scaffold the minimal MCP server

```bash
# Create project
uv init file-browser-mcp
cd file-browser-mcp

# Add the official MCP SDK
uv add "mcp[cli]"
```

Your directory should look like:

```
file-browser-mcp/
├── pyproject.toml
└── src/
    └── file_browser_mcp/
        └── __init__.py
```

Create the entry point `src/file_browser_mcp/server.py`:

```python
"""Minimal MCP server skeleton — we'll fill in tools next."""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("file-browser", instructions="Browse and read files from an approved root directory.")

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

Test that it at least starts:

```bash
uv run python src/file_browser_mcp/server.py
# No output is correct — it's waiting for JSON-RPC messages on stdin.
# Press Ctrl-C to exit.
```

You can also use the MCP dev inspector (ships with the SDK) for interactive testing:

```bash
uv run mcp dev src/file_browser_mcp/server.py
```

This opens a browser-based inspector at `http://localhost:5173` where you can call tools manually.

---

## 4. Expose the file-system browser tool

Now we add two tools — `list_directory` and `read_file` — guarded by a configurable root path so Claude can only touch files you've explicitly approved.

Replace `src/file_browser_mcp/server.py` with the full implementation:

```python
"""
file-browser-mcp — an MCP server that lets Claude list and read files
from a configurable root directory.

Usage:
    ROOT_DIR=/path/to/docs uv run python src/file_browser_mcp/server.py
"""

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# ── Configuration ──────────────────────────────────────────────────────────
ROOT_DIR = Path(os.environ.get("ROOT_DIR", Path.home() / "mcp-sandbox")).resolve()
MAX_FILE_BYTES = int(os.environ.get("MAX_FILE_BYTES", 512 * 1024))  # 512 KB default


def _safe_path(relative: str) -> Path:
    """Resolve *relative* against ROOT_DIR and reject path-traversal attempts."""
    resolved = (ROOT_DIR / relative).resolve()
    if not resolved.is_relative_to(ROOT_DIR):
        raise ValueError(f"Path '{relative}' escapes the sandbox root.")
    return resolved


# ── Server ─────────────────────────────────────────────────────────────────
mcp = FastMCP(
    "file-browser",
    instructions=(
        f"Browse and read files under {ROOT_DIR}. "
        "Use list_directory to explore, read_file to view content."
    ),
)


@mcp.tool()
def list_directory(path: str = ".") -> list[dict]:
    """List the contents of *path* (relative to the sandbox root).

    Returns a list of entries, each with:
    - name: file or directory name
    - type: "file" or "directory"
    - size_bytes: size for files (0 for directories)
    """
    target = _safe_path(path)
    if not target.exists():
        raise FileNotFoundError(f"Path not found: {path}")
    if not target.is_dir():
        raise NotADirectoryError(f"Not a directory: {path}")

    entries = []
    for item in sorted(target.iterdir()):
        entries.append({
            "name": item.name,
            "type": "directory" if item.is_dir() else "file",
            "size_bytes": item.stat().st_size if item.is_file() else 0,
        })
    return entries


@mcp.tool()
def read_file(path: str) -> str:
    """Read and return the text content of *path* (relative to the sandbox root).

    Files larger than MAX_FILE_BYTES are truncated with a notice.
    """
    target = _safe_path(path)
    if not target.exists():
        raise FileNotFoundError(f"File not found: {path}")
    if not target.is_file():
        raise IsADirectoryError(f"Not a file: {path}")

    raw = target.read_bytes()
    if len(raw) > MAX_FILE_BYTES:
        truncated = raw[:MAX_FILE_BYTES].decode("utf-8", errors="replace")
        return truncated + f"\n\n[... truncated at {MAX_FILE_BYTES} bytes ...]"

    return raw.decode("utf-8", errors="replace")


# ── Resource: expose the sandbox root as a browsable resource ──────────────
@mcp.resource("file:///{path}")
def file_resource(path: str) -> str:
    """Expose individual files as MCP resources at file:///<path>."""
    return read_file(path)


# ── Entry point ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ROOT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[file-browser-mcp] sandbox root: {ROOT_DIR}", flush=True)
    mcp.run(transport="stdio")
```

### Create a sandbox to test with

```bash
mkdir -p ~/mcp-sandbox
echo "Hello from MCP!" > ~/mcp-sandbox/hello.txt
echo '{"service":"api","level":"info","msg":"started"}' > ~/mcp-sandbox/app.log
mkdir ~/mcp-sandbox/docs
echo "# Project Docs\nThis is a sample doc." > ~/mcp-sandbox/docs/readme.md
```

### Verify with the dev inspector

```bash
ROOT_DIR=~/mcp-sandbox uv run mcp dev src/file_browser_mcp/server.py
```

In the inspector, call `list_directory` with `{"path": "."}` — you should see `hello.txt`, `app.log`, and `docs/`.

---

## 5. Connect to Claude Desktop for live testing

Claude Desktop uses **stdio transport**: it spawns your server as a subprocess and communicates over stdin/stdout.

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "file-browser": {
      "command": "uv",
      "args": [
        "run",
        "--project",
        "/absolute/path/to/file-browser-mcp",
        "python",
        "src/file_browser_mcp/server.py"
      ],
      "env": {
        "ROOT_DIR": "/Users/yourname/mcp-sandbox"
      }
    }
  }
}
```

> **Tip:** Replace `/absolute/path/to/file-browser-mcp` with the actual path. Claude Desktop needs absolute paths.

Restart Claude Desktop. You'll see a hammer icon (🔨) in the chat input — click it to confirm `list_directory` and `read_file` are listed.

Now ask Claude:

> "What files are in my sandbox? Read hello.txt for me."

Claude will invoke `list_directory` and `read_file` automatically.

---

## 6. Connect to Claude Code (CLI)

Claude Code supports MCP servers via its settings file or the CLI.

### Option A — per-project config (`.claude/settings.json`)

In your project root, create `.claude/settings.json`:

```json
{
  "mcpServers": {
    "file-browser": {
      "command": "uv",
      "args": [
        "run",
        "--project",
        "/absolute/path/to/file-browser-mcp",
        "python",
        "src/file_browser_mcp/server.py"
      ],
      "env": {
        "ROOT_DIR": "/Users/yourname/mcp-sandbox"
      }
    }
  }
}
```

### Option B — global config

```bash
claude mcp add file-browser \
  --command "uv" \
  --args "run,--project,/absolute/path/to/file-browser-mcp,python,src/file_browser_mcp/server.py" \
  --env "ROOT_DIR=/Users/yourname/mcp-sandbox"
```

Verify the server is registered:

```bash
claude mcp list
```

Then in a Claude Code session:

```
/mcp
```

You'll see `file-browser` listed. Ask Claude to read a file from your sandbox and watch it call the tool.

---

## 7. Package as a Docker container

For deployed scenarios (remote servers, shared team tools), stdio transport isn't practical. We switch to **streamable HTTP** and package the server in a container.

### Update the server for HTTP transport

Add an environment variable to let operators choose the transport:

```python
# At the bottom of server.py, replace the __main__ block:
if __name__ == "__main__":
    ROOT_DIR.mkdir(parents=True, exist_ok=True)
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "8000"))
    print(f"[file-browser-mcp] root={ROOT_DIR} transport={transport}", flush=True)
    if transport == "streamable-http":
        mcp.run(transport="streamable-http", host=host, port=port, path="/mcp")
    else:
        mcp.run(transport="stdio")
```

### `pyproject.toml` — add a script entry point

```toml
[project.scripts]
file-browser-mcp = "file_browser_mcp.server:mcp.run"
```

### `Dockerfile`

```dockerfile
FROM python:3.12-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Copy dependency files first (layer cache)
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev

# Copy source
COPY src/ ./src/

# Non-root user
RUN useradd -m appuser && chown -R appuser /app
USER appuser

ENV MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000 \
    ROOT_DIR=/data

EXPOSE 8000

CMD ["uv", "run", "python", "src/file_browser_mcp/server.py"]
```

### Build and run locally

```bash
docker build -t file-browser-mcp:latest .

# Mount a local directory as /data
docker run --rm -p 8000:8000 \
  -v ~/mcp-sandbox:/data:ro \
  file-browser-mcp:latest
```

Test the HTTP endpoint:

```bash
# The MCP inspector can connect to HTTP servers too:
npx @modelcontextprotocol/inspector http://localhost:8000/mcp
```

---

## 8. Deploy to Kubernetes as a sidecar

The sidecar pattern co-locates the MCP server in the same Pod as an application container. The app (or Claude Code running inside the Pod) reaches the MCP server over `localhost`—no service exposure needed.

### `k8s/deployment.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: app-with-mcp
  labels:
    app: app-with-mcp
spec:
  replicas: 1
  selector:
    matchLabels:
      app: app-with-mcp
  template:
    metadata:
      labels:
        app: app-with-mcp
    spec:
      volumes:
        # Shared volume both containers can read
        - name: shared-docs
          emptyDir: {}
        # Or mount a PVC with real data:
        # - name: shared-docs
        #   persistentVolumeClaim:
        #     claimName: docs-pvc

      initContainers:
        # Populate shared-docs with sample files
        - name: init-docs
          image: busybox:1.36
          command:
            - sh
            - -c
            - |
              echo "Hello from K8s!" > /data/hello.txt
              echo '{"status":"ok"}' > /data/status.json
          volumeMounts:
            - name: shared-docs
              mountPath: /data

      containers:
        # ── Main application container ────────────────────────────────────
        - name: app
          image: your-app:latest
          ports:
            - containerPort: 3000
          env:
            - name: MCP_SERVER_URL
              value: "http://localhost:8000/mcp"

        # ── MCP sidecar ───────────────────────────────────────────────────
        - name: mcp-file-browser
          image: file-browser-mcp:latest
          ports:
            - containerPort: 8000
              name: mcp-http
          env:
            - name: MCP_TRANSPORT
              value: streamable-http
            - name: MCP_PORT
              value: "8000"
            - name: ROOT_DIR
              value: /data
            - name: MAX_FILE_BYTES
              value: "1048576"  # 1 MB
          volumeMounts:
            - name: shared-docs
              mountPath: /data
              readOnly: true
          resources:
            requests:
              cpu: 50m
              memory: 64Mi
            limits:
              cpu: 200m
              memory: 128Mi
          readinessProbe:
            httpGet:
              path: /mcp
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 10
          livenessProbe:
            httpGet:
              path: /mcp
              port: 8000
            initialDelaySeconds: 15
            periodSeconds: 30
```

### Deploy

```bash
# Push the image to your registry first
docker tag file-browser-mcp:latest your-registry/file-browser-mcp:latest
docker push your-registry/file-browser-mcp:latest

# Update the image reference in the YAML, then:
kubectl apply -f k8s/deployment.yaml

# Watch the rollout
kubectl rollout status deployment/app-with-mcp

# Tail sidecar logs
kubectl logs -f deployment/app-with-mcp -c mcp-file-browser
```

### Connecting Claude Code to the in-cluster MCP server

If you're running Claude Code from inside the cluster (e.g., in a dev Pod), point it at the sidecar:

```json
{
  "mcpServers": {
    "file-browser": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

For external access, expose via a `Service` + port-forward for development:

```bash
kubectl port-forward deployment/app-with-mcp 8000:8000
```

Then use `http://localhost:8000/mcp` in your Claude Desktop or Claude Code config.

---

## 9. Next steps

You now have a production-ready MCP server pipeline from local dev to K8s. A few directions to explore from here:

- **Add authentication** — Use MCP's OAuth 2.1 support or a simple API-key header check for production deployments.
- **Expose a database tool** — Swap the file-system tools for `execute_query` using `asyncpg` or `sqlalchemy`; the FastMCP pattern is identical.
- **Add prompts** — Use `@mcp.prompt()` to encode reusable analysis workflows ("summarise the latest log file") that Claude can invoke by name.
- **Multi-server setups** — Claude Desktop and Claude Code support multiple simultaneous MCP servers. Compose a file-browser, a database browser, and a metrics fetcher into a single Claude session.
- **Publish to the MCP registry** — The community maintains an open registry of MCP servers at [mcp.so](https://mcp.so). Once your server is polished, submit it so others can use it too.

### Reference files in this tutorial

```
mcp-server-tutorial/
├── tutorial.md                   ← this file
└── demo-server/
    ├── pyproject.toml
    ├── Dockerfile
    ├── src/
    │   └── file_browser_mcp/
    │       ├── __init__.py
    │       └── server.py
    └── k8s/
        └── deployment.yaml
```
