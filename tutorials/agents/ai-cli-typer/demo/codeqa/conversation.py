"""Multi-turn conversation state."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Conversation:
    model: str
    system: str = ""
    messages: list[dict] = field(default_factory=list)

    def add_user(self, text: str) -> None:
        self.messages.append({"role": "user", "content": text})

    def add_assistant(self, text: str) -> None:
        self.messages.append({"role": "assistant", "content": text})

    def reset(self) -> None:
        self.messages.clear()

    @property
    def turn_count(self) -> int:
        return len([m for m in self.messages if m["role"] == "user"])
