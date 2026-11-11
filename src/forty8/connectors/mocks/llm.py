"""Deterministic mock LLM. Tasks are registered handlers so each agent's phase can add
its own (slip reading, header mapping, free-text coding) without touching the interface."""

from __future__ import annotations

from typing import Any, Callable

from forty8.models.results import LLMResponse

Handler = Callable[[str, str], dict[str, Any]]  # (instructions, content) -> structured data

_HANDLERS: dict[str, Handler] = {}


def register_task(task: str) -> Callable[[Handler], Handler]:
    def decorator(fn: Handler) -> Handler:
        _HANDLERS[task] = fn
        return fn
    return decorator


class MockLLM:
    def __init__(self, options: dict | None = None):
        self.model = str((options or {}).get("model", "mock-llm"))
        self.calls: list[tuple[str, str]] = []

    def complete(self, task: str, instructions: str, content: str, schema: dict[str, Any] | None = None) -> LLMResponse:
        self.calls.append((task, content))
        handler = _HANDLERS.get(task)
        if handler is None:
            raise NotImplementedError(f"mock LLM has no handler for task '{task}'")
        return LLMResponse(data=handler(instructions, content), model=self.model)


@register_task("echo")
def _echo(instructions: str, content: str) -> dict[str, Any]:
    return {"text": content}
