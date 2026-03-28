# Building AI-Powered CLI Tools with Claude API and Typer

Command-line tools and large language models are a natural pairing. CLIs compose. They pipe. They script. They run inside cron jobs, CI pipelines, and SSH sessions where there is no browser. Wiring Claude into a CLI lets you bring intelligence to every place a terminal can reach.

This tutorial walks you through building production-quality CLI tools in Python using [Typer](https://typer.tiangolo.com/) and the [Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python). By the end you will have a working `codeqa` tool that reads source files, asks Claude questions about them, streams answers live to the terminal, and supports multi-turn conversations across a session.

---

## Why CLI + LLM Is a Great Fit

### Composability

Unix philosophy lives in pipes. A CLI tool that reads from stdin and writes to stdout chains with `grep`, `jq`, `awk`, and every other tool on the system:

```bash
git diff HEAD~1 | codeqa ask "Summarize what changed and flag any security issues"
```

### Scripting

LLM calls become first-class citizens in shell scripts:

```bash
for f in src/**/*.py; do
  codeqa ask --file "$f" "Does this file have any obvious bugs?" >> review.txt
done
```

### Low-ceremony access

No browser. No sign-in flow. No GUI. Just the tool, a terminal, and an API key in the environment.

---

## Project Setup

```bash
mkdir codeqa && cd codeqa
python -m venv .venv && source .venv/bin/activate
pip install typer[all] anthropic keyring rich
```

`typer[all]` pulls in Rich for pretty output and shellingham for shell completion. `keyring` gives you OS-level secret storage for the API key.

Create the package skeleton:

```
codeqa/
  __init__.py
  cli.py          # Typer app + commands
  client.py       # Anthropic client setup
  conversation.py # Multi-turn session state
  tools.py        # Tool-use allow-list
pyproject.toml
```

---

## Typer Basics: Commands, Options, and Rich Output

Typer turns type-annotated Python functions into CLI commands. A minimal app looks like this:

```python
# codeqa/cli.py
import typer
from rich.console import Console

app = typer.Typer(help="Ask Claude questions about your code.")
console = Console()

@app.command()
def ask(
    question: str = typer.Argument(..., help="Question to ask Claude"),
    file: typer.FileText = typer.Option(None, "--file", "-f", help="Source file to include as context"),
    model: str = typer.Option("claude-sonnet-4-6", "--model", "-m", envvar="CODEQA_MODEL"),
):
    """Ask a one-shot question, optionally with a file as context."""
    ...

if __name__ == "__main__":
    app()
```

Key patterns:

- `typer.Argument` is positional. `typer.Option` is a flag (`--file`).
- `envvar=` lets users set defaults via environment variables (`CODEQA_MODEL`).
- `typer.FileText` opens the file automatically and hands you a file-like object.
- `rich.console.Console` gives you colored output, markdown rendering, and spinners for free.

### Rich Output

Print a styled header and wrap the Claude response in a panel:

```python
from rich.panel import Panel
from rich.markdown import Markdown

console.rule("[bold blue]codeqa[/bold blue]")
console.print(Panel(Markdown(response_text), title="Answer", border_style="blue"))
```

---

## Streaming Responses in the Terminal

Streaming lets users see Claude's answer as it is generated rather than waiting for the whole response. This is critical for UX — a 10-second blank terminal feels broken; a 10-second stream of tokens feels alive.

The Anthropic SDK exposes `stream()` as a context manager:

```python
# codeqa/client.py
import os
import anthropic
import keyring

SERVICE_NAME = "codeqa"


def get_api_key() -> str:
    # 1. Prefer explicit env var
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key
    # 2. Fall back to OS keyring
    key = keyring.get_password(SERVICE_NAME, "api_key")
    if key:
        return key
    raise RuntimeError(
        "No API key found. Set ANTHROPIC_API_KEY or run: codeqa config set-key"
    )


def make_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=get_api_key())
```

```python
# In cli.py — streaming a one-shot answer
from rich.live import Live
from rich.text import Text
from codeqa.client import make_client

def stream_answer(messages: list[dict], model: str) -> str:
    client = make_client()
    full_text = ""
    display = Text()

    with Live(display, refresh_per_second=15, console=console) as live:
        with client.messages.stream(
            model=model,
            max_tokens=4096,
            messages=messages,
        ) as stream:
            for delta in stream.text_stream:
                full_text += delta
                display.append(delta)
                live.update(display)

    return full_text
```

`rich.live.Live` repaints a region of the terminal on each refresh cycle. Each token from `stream.text_stream` gets appended to both the accumulator string and the `Text` object. The result is a smooth, live-updating output with no screen flicker.

---

## Tool Use in CLIs: Let Claude Call Shell Commands Safely

Claude's tool-use feature lets you hand Claude a set of functions it can call. In a CLI context this means Claude can run shell commands — but you must constrain what it can run.

The pattern: define an allow-list of safe command prefixes, validate every call against it before executing.

```python
# codeqa/tools.py
import subprocess
import shlex
from typing import Any

ALLOWED_PREFIXES = (
    "git ",
    "ls ",
    "find ",
    "grep ",
    "cat ",
    "wc ",
    "head ",
    "tail ",
    "file ",
    "stat ",
)

SHELL_TOOL = {
    "name": "run_shell",
    "description": (
        "Run a read-only shell command and return its stdout. "
        "Only informational commands are permitted — no writes, no network calls."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to run.",
            }
        },
        "required": ["command"],
    },
}


def run_shell(command: str) -> dict[str, Any]:
    """Validate and execute a shell command from Claude."""
    stripped = command.strip()
    if not any(stripped.startswith(p) for p in ALLOWED_PREFIXES):
        return {
            "error": f"Command not allowed. Permitted prefixes: {', '.join(ALLOWED_PREFIXES)}"
        }
    try:
        result = subprocess.run(
            shlex.split(stripped),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return {"stdout": result.stdout, "stderr": result.stderr, "returncode": result.returncode}
    except subprocess.TimeoutExpired:
        return {"error": "Command timed out after 10 seconds"}
    except Exception as exc:
        return {"error": str(exc)}
```

In the agentic loop, you process tool calls until Claude stops requesting them:

```python
import json
import anthropic
from codeqa.tools import SHELL_TOOL, run_shell
from codeqa.client import make_client


def run_agentic(messages: list[dict], model: str) -> str:
    client = make_client()

    while True:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            tools=[SHELL_TOOL],
            messages=messages,
        )

        if response.stop_reason == "end_turn":
            # Extract the final text block
            for block in response.content:
                if block.type == "text":
                    return block.text
            return ""

        if response.stop_reason == "tool_use":
            # Append assistant turn
            messages.append({"role": "assistant", "content": response.content})

            # Process each tool call
            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    result = run_shell(block.input["command"])
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result),
                    })

            # Append tool results as user turn
            messages.append({"role": "user", "content": tool_results})
            # Loop — Claude will continue
```

---

## Config Management: API Key and Model Selection

Two layers of config:

1. **Environment variables** — best for CI and Docker. `ANTHROPIC_API_KEY`, `CODEQA_MODEL`.
2. **OS keyring** — best for developer laptops. Survives shell restarts without dotfile pollution.

Add a `config` subcommand to manage the keyring:

```python
config_app = typer.Typer(help="Manage codeqa configuration.")
app.add_typer(config_app, name="config")


@config_app.command("set-key")
def config_set_key(
    key: str = typer.Option(..., prompt="Anthropic API key", hide_input=True)
):
    """Store the Anthropic API key in the OS keyring."""
    import keyring
    keyring.set_password("codeqa", "api_key", key)
    console.print("[green]API key saved to keyring.[/green]")


@config_app.command("show")
def config_show():
    """Show current configuration (key is masked)."""
    import keyring
    stored = keyring.get_password("codeqa", "api_key")
    env_key = os.environ.get("ANTHROPIC_API_KEY")
    source = "environment" if env_key else ("keyring" if stored else "not set")
    model = os.environ.get("CODEQA_MODEL", "claude-sonnet-4-6")
    console.print(f"API key source: [bold]{source}[/bold]")
    console.print(f"Model: [bold]{model}[/bold]")
```

---

## Multi-Turn Conversation Mode

Multi-turn is where a CLI tool starts feeling like a REPL. You maintain the messages list across prompts, feeding the full history back to Claude on each turn:

```python
# codeqa/conversation.py
from dataclasses import dataclass, field


@dataclass
class Conversation:
    model: str
    system: str = ""
    messages: list[dict] = field(default_factory=list)

    def add_user(self, text: str):
        self.messages.append({"role": "user", "content": text})

    def add_assistant(self, text: str):
        self.messages.append({"role": "assistant", "content": text})

    def reset(self):
        self.messages.clear()
```

The `interactive` command drives a REPL loop:

```python
@app.command()
def interactive(
    file: typer.FileText = typer.Option(None, "--file", "-f"),
    model: str = typer.Option("claude-sonnet-4-6", "--model", "-m", envvar="CODEQA_MODEL"),
):
    """Start a multi-turn Q&A session. Type 'exit' or press Ctrl-C to quit."""
    system = "You are a helpful code review assistant. Be concise and precise."
    if file:
        source = file.read()
        system += f"\n\nThe user has loaded this file for context:\n\n```\n{source}\n```"

    conv = Conversation(model=model, system=system)
    console.rule("[bold blue]codeqa interactive[/bold blue]")
    console.print("Type [bold]exit[/bold] to quit, [bold]reset[/bold] to clear history.\n")

    while True:
        try:
            question = typer.prompt("You")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Bye.[/dim]")
            break

        if question.strip().lower() in ("exit", "quit"):
            console.print("[dim]Bye.[/dim]")
            break
        if question.strip().lower() == "reset":
            conv.reset()
            console.print("[yellow]Conversation reset.[/yellow]")
            continue

        conv.add_user(question)
        answer = stream_answer(
            messages=conv.messages,
            model=model,
            system=conv.system,
        )
        conv.add_assistant(answer)
        console.print()  # blank line after streamed output
```

Because the full `messages` list is passed on every call, Claude remembers every exchange in the session. Memory resets when you type `reset` or exit.

---

## Packaging: PyPI, Homebrew Tap, and Single Binary

### pyproject.toml

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "codeqa"
version = "0.1.0"
description = "Ask Claude questions about your code from the terminal"
requires-python = ">=3.11"
dependencies = [
    "typer[all]>=0.12",
    "anthropic>=0.40",
    "keyring>=25",
    "rich>=13",
]

[project.scripts]
codeqa = "codeqa.cli:app"
```

Publish to PyPI:

```bash
pip install hatch
hatch build
hatch publish
```

Users install with:

```bash
pip install codeqa
# or with uv (faster):
uv tool install codeqa
```

### Homebrew Tap

Create a `homebrew-tap` repo (`github.com/yourname/homebrew-codeqa`) with a formula:

```ruby
# Formula/codeqa.rb
class Codeqa < Formula
  include Language::Python::Virtualenv

  desc "Ask Claude questions about your code from the terminal"
  homepage "https://github.com/yourname/codeqa"
  url "https://files.pythonhosted.org/packages/.../codeqa-0.1.0.tar.gz"
  sha256 "..."
  license "MIT"

  depends_on "python@3.12"

  resource "anthropic" do
    url "https://files.pythonhosted.org/packages/.../anthropic-0.40.0.tar.gz"
    sha256 "..."
  end

  # add resource stanzas for typer, rich, keyring ...

  def install
    virtualenv_install_with_resources
  end

  test do
    assert_match "codeqa", shell_output("#{bin}/codeqa --help")
  end
end
```

Install:

```bash
brew tap yourname/codeqa
brew install codeqa
```

### Single Binary with PyInstaller

For users who cannot or will not use pip:

```bash
pip install pyinstaller
pyinstaller --onefile --name codeqa codeqa/cli.py
```

The `dist/codeqa` binary is self-contained. Ship it in GitHub Releases and let users `curl` it:

```bash
curl -Lo codeqa https://github.com/yourname/codeqa/releases/latest/download/codeqa-linux-amd64
chmod +x codeqa && mv codeqa /usr/local/bin/
```

---

## Full Working Demo: `codeqa`

The complete tool is in the `codeqa/` directory alongside this tutorial. Here is a quick walkthrough:

```bash
# One-shot question
codeqa ask "What does this function do?" --file src/parser.py

# Pipe input
cat src/parser.py | codeqa ask "Are there any edge cases I'm missing?"

# With tool use (Claude can run git/grep to explore)
codeqa explore --file src/parser.py

# Interactive multi-turn session on a whole file
codeqa interactive --file src/parser.py
```

### Shell Completion

Typer generates completion scripts for bash, zsh, and fish:

```bash
codeqa --install-completion
```

---

## Key Takeaways

- **Typer + Rich** is the ergonomic baseline for Python CLIs in 2025. Type annotations handle argument parsing; Rich handles output.
- **Stream everything.** `client.messages.stream()` with `rich.live.Live` is four lines of code that dramatically improves perceived responsiveness.
- **Tool use needs guardrails.** An allow-list on command prefixes plus a 10-second timeout is the minimum viable safety layer for shell access.
- **`keyring` > dotfiles.** OS-level secret storage keeps API keys out of `.env` files that accidentally get committed.
- **`uv tool install`** is now the fastest path for distributing Python CLIs to developers. PyInstaller single binaries cover everyone else.
