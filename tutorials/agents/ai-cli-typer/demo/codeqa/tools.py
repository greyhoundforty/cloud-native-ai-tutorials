"""Tool-use allow-list for shell commands Claude may request."""

from __future__ import annotations

import json
import shlex
import subprocess
from typing import Any

# Only read-only, informational commands are allowed.
ALLOWED_PREFIXES = (
    "git log",
    "git diff",
    "git show",
    "git status",
    "git blame",
    "ls ",
    "ls\n",
    "find ",
    "grep ",
    "cat ",
    "wc ",
    "head ",
    "tail ",
    "file ",
    "stat ",
    "tree ",
)

SHELL_TOOL: dict[str, Any] = {
    "name": "run_shell",
    "description": (
        "Run a read-only shell command and return its stdout and stderr. "
        "Only informational commands are permitted (ls, find, grep, git log/diff/show, cat, wc, head, tail, file, stat, tree). "
        "No write operations, no network calls."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The exact shell command to run.",
            }
        },
        "required": ["command"],
    },
}


def run_shell(command: str) -> dict[str, Any]:
    """Validate against the allow-list, then execute the command."""
    stripped = command.strip()

    allowed = any(stripped.startswith(p) for p in ALLOWED_PREFIXES) or stripped in (
        "ls",
        "git status",
        "git log",
        "git diff",
    )

    if not allowed:
        return {
            "error": (
                f"Command '{stripped}' is not in the allow-list. "
                f"Permitted prefixes: {', '.join(sorted(set(p.strip() for p in ALLOWED_PREFIXES)))}"
            )
        }

    try:
        result = subprocess.run(
            shlex.split(stripped),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return {
            "stdout": result.stdout[:8000],  # cap output to avoid token explosion
            "stderr": result.stderr[:2000],
            "returncode": result.returncode,
        }
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out after 10 seconds."}
    except FileNotFoundError as exc:
        return {"error": f"Command not found: {exc}"}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def dispatch_tool(name: str, inputs: dict[str, Any]) -> str:
    """Route a tool call from Claude to the correct handler and return JSON."""
    if name == "run_shell":
        result = run_shell(inputs["command"])
    else:
        result = {"error": f"Unknown tool: {name}"}
    return json.dumps(result)
