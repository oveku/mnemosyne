"""
Abstract storage interface for Mnemosyne memory layer.
All storage backends must implement this interface.
"""

from abc import ABC, abstractmethod
from typing import Any

# Request context carrying identity & scoping info (optional)
# Expected keys:
# - user_id: str | None
# - space_id: str | None
# - allowed_spaces: list[str] | None
RequestContext = dict[str, Any]


class MemoryStorage(ABC):
    """Abstract base class for Mnemosyne storage backends."""

    @abstractmethod
    async def initialize(self) -> None:
        """Initialize the storage backend (create tables/indexes)."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close the storage backend connection."""
        ...

    @abstractmethod
    async def write_memory(
        self,
        kind: str,
        title: str,
        content: str,
        tags: list[str] | None = None,
        pinned: bool = False,
        context: RequestContext | None = None,
    ) -> dict[str, Any]:
        """
        Store a memory item. Deduplicates by kind+title (updates if exists).
        Returns {"ok": True, "action": "created"|"updated", "id": <id>}
        """
        ...

    @abstractmethod
    async def search_memory(
        self, query: str, limit: int = 8, context: RequestContext | None = None
    ) -> list[dict[str, Any]]:
        """Full-text search across memory items. Returns ranked results."""
        ...

    @abstractmethod
    async def bootstrap(
        self,
        limit_pinned: int = 8,
        limit_recent: int = 10,
        context: RequestContext | None = None,
    ) -> dict[str, Any]:
        """
        Return startup context: pinned items + recent items.
        Returns {"pinned": [...], "recent": [...]}
        """
        ...

    @abstractmethod
    async def commit_session(
        self,
        workspace_hint: str,
        summary: str,
        decisions: list[str] | None = None,
        next_steps: list[str] | None = None,
        context: RequestContext | None = None,
    ) -> dict[str, Any]:
        """Write an end-of-session summary. Returns {"ok": True}"""
        ...

    @abstractmethod
    async def last_session(
        self,
        workspace_hint: str = "global",
        limit: int = 3,
        context: RequestContext | None = None,
    ) -> list[dict[str, Any]]:
        """Return the most recent session logs for a workspace."""
        ...
