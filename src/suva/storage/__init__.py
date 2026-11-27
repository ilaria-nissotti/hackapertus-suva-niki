"""Knowledge-base storage behind a repository interface (DATABASE_URL)."""

from suva.storage.base import AuditEntry, Repository
from suva.storage.sqlite import SQLiteRepository, open_repository

__all__ = ["AuditEntry", "Repository", "SQLiteRepository", "open_repository"]
