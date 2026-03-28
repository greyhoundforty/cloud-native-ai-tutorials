"""
file-browser-mcp — an MCP server that lets Claude list and read files
from a configurable root directory.

Usage (stdio transport, for Claude Desktop / Claude Code):
    ROOT_DIR=/path/to/docs uv run python src/file_browser_mcp/server.py

Usage (HTTP transport, for Docker / K8s):
    MCP_TRANSPORT=streamable-http ROOT_DIR=/data uv run python src/file_browser_mcp/server.py
"""

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# ── Configuration ───────────────────────────────────────────────────────────
ROOT_DIR = Path(os.environ.get("ROOT_DIR", Path.home() / "mcp-sandbox")).resolve()
MAX_FILE_BYTES = int(os.environ.get("MAX_FILE_BYTES", 512 * 1024))  # 512 KB default


def _safe_path(relative: str) -> Path:
    """Resolve *relative* against ROOT_DIR and reject path-traversal attempts."""
    resolved = (ROOT_DIR / relative).resolve()
    if not resolved.is_relative_to(ROOT_DIR):
        raise ValueError(f"Path '{relative}' escapes the sandbox root.")
    return resolved


# ── Server ──────────────────────────────────────────────────────────────────
mcp = FastMCP(
    "file-browser",
    instructions=(
        f"Browse and read files under {ROOT_DIR}. "
        "Use list_directory to explore folders and read_file to view file contents."
    ),
)


@mcp.tool()
def list_directory(path: str = ".") -> list[dict]:
    """List the contents of *path* (relative to the sandbox root).

    Args:
        path: Directory path relative to the sandbox root. Defaults to the root itself.

    Returns:
        A list of entries, each with:
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

    Args:
        path: File path relative to the sandbox root.

    Returns:
        The file's text content. Files exceeding MAX_FILE_BYTES are truncated.
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


# ── Resource: expose files as browsable MCP resources ──────────────────────
@mcp.resource("file:///{path}")
def file_resource(path: str) -> str:
    """Expose individual files as MCP resources at file:///<path>."""
    return read_file(path)


# ── Entry point ─────────────────────────────────────────────────────────────
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
