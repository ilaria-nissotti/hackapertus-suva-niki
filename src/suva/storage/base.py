"""Backend-neutral repository interface. Callers never write SQL directly."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from suva.sources.base import SourceRecord


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

    # --- judgements + coverage ledger (step 3) -----------------------------
    @abstractmethod
    def upsert_judgement(self, rec: "SourceRecord") -> bool:
        """Merge one source sighting. Returns True if the judgement is new.

        Never loses data: text is replaced only by a longer text, publication
        date only fills an unknown one, and the sighting is logged per source.
        """

    @abstractmethod
    def get_judgement(self, key: str) -> dict[str, Any] | None: ...

    @abstractmethod
    def list_judgements(
        self, start: str | None = None, end: str | None = None, dockets: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Judgements by decision-date range and/or docket, without the text column."""

    @abstractmethod
    def judgement_sources(self, key: str) -> list[dict[str, Any]]: ...

    @abstractmethod
    def set_html_path(self, key: str, path: str) -> None: ...

    @abstractmethod
    def close(self) -> None: ...
