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

    # --- supplied data (step 2) -------------------------------------------
    @abstractmethod
    def upsert_example(self, row: dict[str, Any]) -> None:
        """Insert/update an example summary by decision identity. Protects assigned examples."""

    @abstractmethod
    def list_examples(self, split: str | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    def set_example_split(self, decision_id: str, split: str) -> None:
        """Assign fewshot/eval. Raises if the decision already has a different split."""

    @abstractmethod
    def upsert_known_positive(self, row: dict[str, Any]) -> None: ...

    @abstractmethod
    def list_known_positives(self, court: str | None = None) -> list[dict[str, Any]]: ...

    @abstractmethod
    def replace_supplied(self, examples: list[dict], positives: list[dict]) -> None:
        """Atomically replace the supplied-data snapshot."""

    @abstractmethod
    def freeze_examples(self, manifest: dict) -> dict[str, int]:
        """Atomically validate and persist a split manifest."""

    @abstractmethod
    def get_example_manifest(self) -> dict | None: ...

    @abstractmethod
    def close(self) -> None: ...
