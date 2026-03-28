"""codeqa — Ask Claude questions about your code from the terminal."""

from __future__ import annotations

import os
import sys

import typer
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from codeqa.client import make_client
from codeqa.conversation import Conversation
from codeqa.tools import SHELL_TOOL, dispatch_tool

app = typer.Typer(
    name="codeqa",
    help="Ask Claude questions about your code from the terminal.",
    no_args_is_help=True,
)
config_app = typer.Typer(help="Manage codeqa configuration.")
app.add_typer(config_app, name="config")

console = Console()

DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_SYSTEM = (
    "You are a concise, expert code reviewer. "
    "Answer questions about code precisely. "
    "Prefer short, focused answers unless depth is requested."
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_messages(question: str, file_content: str | None) -> list[dict]:
    user_content = question
    if file_content:
        user_content = f"Here is the source file:\n\n```\n{file_content}\n```\n\n{question}"
    return [{"role": "user", "content": user_content}]


def _stream_response(
    messages: list[dict],
    model: str,
    system: str = DEFAULT_SYSTEM,
) -> str:
    """Stream a response from Claude, rendering tokens live with Rich."""
    client = make_client()
    full_text = ""
    display = Text()

    with Live(display, refresh_per_second=20, console=console) as live:
        with client.messages.stream(
            model=model,
            max_tokens=4096,
            system=system,
            messages=messages,
        ) as stream:
            for delta in stream.text_stream:
                full_text += delta
                display.append(delta)
                live.update(display)

    return full_text


def _run_agentic(
    messages: list[dict],
    model: str,
    system: str = DEFAULT_SYSTEM,
) -> str:
    """Agentic loop: let Claude call shell tools until it reaches end_turn."""
    client = make_client()
    max_rounds = 10

    for round_num in range(max_rounds):
        with console.status(f"[dim]Thinking (round {round_num + 1})…[/dim]"):
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                system=system,
                tools=[SHELL_TOOL],
                messages=messages,
            )

        if response.stop_reason == "end_turn":
            for block in response.content:
                if block.type == "text":
                    return block.text
            return ""

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []

            for block in response.content:
                if block.type == "tool_use":
                    console.print(
                        f"[dim]  → tool: {block.name}({block.input.get('command', '')})[/dim]"
                    )
                    result_json = dispatch_tool(block.name, block.input)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_json,
                        }
                    )

            messages.append({"role": "user", "content": tool_results})
            continue

        # Unexpected stop reason
        break

    return "[max tool rounds reached — partial answer above]"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@app.command()
def ask(
    question: str = typer.Argument(..., help="Question to ask Claude about the code."),
    file: typer.FileText | None = typer.Option(
        None, "--file", "-f", help="Source file to include as context."
    ),
    model: str = typer.Option(
        DEFAULT_MODEL, "--model", "-m", envvar="CODEQA_MODEL", help="Claude model to use."
    ),
    markdown: bool = typer.Option(True, "--markdown/--plain", help="Render answer as Markdown."),
):
    """Ask a one-shot question, optionally with a file as context.

    Examples:

        codeqa ask "What does this do?" --file src/parser.py

        cat src/parser.py | codeqa ask "Any bugs here?"
    """
    file_content: str | None = None

    if file:
        file_content = file.read()
    elif not sys.stdin.isatty():
        file_content = sys.stdin.read()

    messages = _build_messages(question, file_content)

    console.rule("[bold blue]codeqa[/bold blue]")
    answer = _stream_response(messages, model)
    console.print()

    if markdown:
        console.print(Panel(Markdown(answer), border_style="blue", title="Answer"))
    # (plain text was already streamed live above)


@app.command()
def explore(
    question: str = typer.Argument(
        "Explore this codebase and summarize its structure, key components, and any obvious issues.",
        help="Question or directive for Claude.",
    ),
    file: typer.FileText | None = typer.Option(
        None, "--file", "-f", help="Seed file to give Claude initial context."
    ),
    model: str = typer.Option(
        DEFAULT_MODEL, "--model", "-m", envvar="CODEQA_MODEL", help="Claude model to use."
    ),
):
    """Let Claude explore the codebase using shell tools (git, grep, find, etc.)."""
    file_content: str | None = None
    if file:
        file_content = file.read()

    messages = _build_messages(question, file_content)

    system = (
        DEFAULT_SYSTEM
        + "\n\nYou have access to a 'run_shell' tool. Use it to explore the repository "
        "with read-only commands (ls, find, grep, git log/diff/show, cat, wc, head, tail, stat). "
        "Gather evidence before answering. Cite specific files and line numbers."
    )

    console.rule("[bold blue]codeqa explore[/bold blue]")
    answer = _run_agentic(messages, model, system)
    console.print()
    console.print(Panel(Markdown(answer), border_style="green", title="Exploration Result"))


@app.command()
def interactive(
    file: typer.FileText | None = typer.Option(
        None, "--file", "-f", help="Source file to load into session context."
    ),
    model: str = typer.Option(
        DEFAULT_MODEL, "--model", "-m", envvar="CODEQA_MODEL", help="Claude model to use."
    ),
):
    """Start a multi-turn Q&A session on a file or codebase.

    Type 'exit' or press Ctrl-C to quit. Type 'reset' to clear history.
    """
    system = DEFAULT_SYSTEM
    if file:
        source = file.read()
        system += (
            f"\n\nThe user has loaded this file for context:\n\n```\n{source}\n```\n"
            "Answer questions about it directly. You may quote or reference specific lines."
        )

    conv = Conversation(model=model, system=system)
    console.rule("[bold blue]codeqa interactive[/bold blue]")
    if file:
        console.print(f"[dim]File loaded: {file.name}[/dim]")
    console.print("Type [bold]exit[/bold] to quit · [bold]reset[/bold] to clear history\n")

    while True:
        try:
            question = typer.prompt("You")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[dim]Bye.[/dim]")
            break

        cmd = question.strip().lower()
        if cmd in ("exit", "quit", "q"):
            console.print("[dim]Bye.[/dim]")
            break
        if cmd == "reset":
            conv.reset()
            console.print("[yellow]Conversation reset.[/yellow]\n")
            continue
        if cmd in ("help", "?"):
            console.print(
                "[dim]Commands: exit · reset · (anything else is sent to Claude)[/dim]\n"
            )
            continue

        conv.add_user(question)
        console.print()
        answer = _stream_response(conv.messages, model, conv.system)
        conv.add_assistant(answer)
        console.print(f"\n[dim](turn {conv.turn_count})[/dim]\n")


# ---------------------------------------------------------------------------
# Config subcommands
# ---------------------------------------------------------------------------


@config_app.command("set-key")
def config_set_key(
    key: str = typer.Option(..., prompt="Anthropic API key", hide_input=True),
):
    """Store the Anthropic API key in the OS keyring."""
    try:
        import keyring  # type: ignore

        keyring.set_password("codeqa", "api_key", key)
        console.print("[green]API key saved to OS keyring.[/green]")
    except Exception as exc:
        console.print(f"[red]Failed to save key: {exc}[/red]")
        console.print("[dim]Fallback: set ANTHROPIC_API_KEY in your environment.[/dim]")
        raise typer.Exit(1) from exc


@config_app.command("show")
def config_show():
    """Show current configuration."""
    env_key = os.environ.get("ANTHROPIC_API_KEY")
    stored_key = None
    try:
        import keyring  # type: ignore

        stored_key = keyring.get_password("codeqa", "api_key")
    except Exception:
        pass

    if env_key:
        source = "environment (ANTHROPIC_API_KEY)"
        masked = env_key[:8] + "…" if len(env_key) > 8 else "***"
    elif stored_key:
        source = "OS keyring"
        masked = stored_key[:8] + "…" if len(stored_key) > 8 else "***"
    else:
        source = "[red]not set[/red]"
        masked = "—"

    model = os.environ.get("CODEQA_MODEL", DEFAULT_MODEL)
    console.print(f"API key source : [bold]{source}[/bold]")
    console.print(f"API key preview: {masked}")
    console.print(f"Model          : [bold]{model}[/bold]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
