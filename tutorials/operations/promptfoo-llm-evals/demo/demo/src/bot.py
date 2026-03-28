"""
Claude-powered K8s Q&A bot.

Usage:
    python bot.py "How do I list all pods?"
    echo "What is a namespace?" | python bot.py -
"""

import sys
import anthropic

client = anthropic.Anthropic()

SYSTEM_PROMPT = """You are a helpful assistant specializing in cloud-native infrastructure.
You answer questions about Kubernetes, Helm, Terraform, and related tools.
Be concise and accurate. If you don't know something, say so.
Do not make up commands or configuration options that don't exist."""


def answer(question: str) -> str:
    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": question}],
    )
    return message.content[0].text


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python bot.py <question>", file=sys.stderr)
        sys.exit(1)

    question = sys.argv[1]
    if question == "-":
        question = sys.stdin.read().strip()

    if not question:
        print("Error: empty question", file=sys.stderr)
        sys.exit(1)

    print(answer(question))


if __name__ == "__main__":
    main()
