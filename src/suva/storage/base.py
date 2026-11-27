"""Backend-neutral repository interface. Callers never write SQL directly."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class AuditEntry:
    """One append-only audit record (LLM call or reviewer action)."""

    kind: str  # "llm_call" | "reviewer_action" | ...
    actor: str  # model name or reviewer name
    payload: dict[str, Any] = field(default_factory=dict)


class Repository(ABC):
    @abstractmethod
    def migrate(self) -> list[str]:
        """Apply pending migrations; return the names applied."""

    @abstractmethod
    def append_audit(self, entry: AuditEntry) -> int:
        """Append an audit record; return its id. Records cannot be changed."""

    @abstractmethod
    def list_audit(self, kind: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Most recent audit records first."""

    @abstractmethod
    def close(self) -> None: ...
