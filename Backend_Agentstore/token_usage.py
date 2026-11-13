from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator
from uuid import uuid4


def _token_count(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class TokenUsageEvent:
    provider_response_id: str
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int


@dataclass
class TokenUsageCollector:
    events: list[TokenUsageEvent] = field(default_factory=list)

    def add(self, *, response_id: Any, model: Any, usage: Any) -> None:
        if not isinstance(usage, dict):
            return
        input_tokens = _token_count(usage.get("input_tokens", usage.get("prompt_tokens")))
        output_tokens = _token_count(usage.get("output_tokens", usage.get("completion_tokens")))
        total_tokens = _token_count(usage.get("total_tokens"))
        if total_tokens == 0:
            total_tokens = input_tokens + output_tokens
        if total_tokens == 0:
            return
        self.events.append(
            TokenUsageEvent(
                provider_response_id=str(response_id or f"unidentified-{uuid4()}"),
                model=str(model or "unknown"),
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
            )
        )

    def summary(self) -> dict[str, int]:
        return {
            "input_tokens": sum(event.input_tokens for event in self.events),
            "output_tokens": sum(event.output_tokens for event in self.events),
            "total_tokens": sum(event.total_tokens for event in self.events),
        }


_active_collector: ContextVar[TokenUsageCollector | None] = ContextVar(
    "active_token_usage_collector",
    default=None,
)


@contextmanager
def capture_token_usage() -> Iterator[TokenUsageCollector]:
    collector = TokenUsageCollector()
    reset_token = _active_collector.set(collector)
    try:
        yield collector
    finally:
        _active_collector.reset(reset_token)


def record_token_usage(*, response_id: Any, model: Any, usage: Any) -> None:
    collector = _active_collector.get()
    if collector is not None:
        collector.add(response_id=response_id, model=model, usage=usage)
