"""
Abstract storage interface for Mnemosyne memory layer.
All storage backends must implement this interface.
"""

from abc import ABC, abstractmethod
from typing import Any, Literal

# Request context carrying identity & scoping info (optional)
# Expected keys:
# - user_id: str | None
# - space_id: str | None
# - allowed_spaces: list[str] | None
RequestContext = dict[str, Any]

# Bootstrap mode controls how much content is returned per item:
#   thin   – compact text only (content_compact or auto-generated snippet)
#   hybrid – full content for short command/pattern items; compact otherwise
#   full   – legacy behavior, returns full content
BootstrapMode = Literal["thin", "hybrid", "full"]

# Content preference for search and read operations
ContentPrefer = Literal["compact", "full"]


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
        content_compact: str | None = None,
        workspace_hint: str | None = None,
        importance: int | None = None,
        source: str | None = None,
        context: RequestContext | None = None,
    ) -> dict[str, Any]:
        """
        Store a memory item. Deduplicates by kind+title (updates if exists).

        New fields (all optional, backward-compatible):
          content_compact – short version for bootstrap/search (auto-generated if missing)
          workspace_hint  – workspace scope for ranking
          importance      – 0-100 priority signal (default 50)
          source          – origin: "manual", "agent", "commit_session", etc.

        Returns {"ok": True, "action": "created"|"updated", "id": <id>}
        """
        ...

    @abstractmethod
    async def read_memory(
        self,
        item_id: str,
        prefer: ContentPrefer = "full",
        context: RequestContext | None = None,
    ) -> dict[str, Any] | None:
        """
        Read a single memory item by its id.
        Returns the item with content based on `prefer` ("full" or "compact").
        Returns None if item not found.
        """
        ...

    @abstractmethod
    async def search_memory(
        self,
        query: str,
        limit: int = 8,
        prefer: ContentPrefer = "full",
        snippet_chars: int = 400,
        context: RequestContext | None = None,
    ) -> list[dict[str, Any]]:
        """
        Full-text search across memory items. Returns ranked results.

        prefer        – "compact" returns content_compact/snippet; "full" returns full content
        snippet_chars – max chars for auto-generated snippets (when no content_compact exists)

        Each result includes `has_full: bool` so agents know they can call read_memory.
        """
        ...

    @abstractmethod
    async def bootstrap(
        self,
        limit_pinned: int = 8,
        limit_recent: int = 10,
        workspace_hint: str = "global",
        mode: BootstrapMode = "full",
        max_tokens: int = 0,
        max_items: int = 15,
        include_sessions: bool = False,
        context: RequestContext | None = None,
    ) -> dict[str, Any]:
        """
        Return startup context: pinned items + recent items.

        New parameters (all optional, backward-compatible):
          workspace_hint   – scope to relevant workspace (default "global")
          mode             – "thin" | "hybrid" | "full" (default "full")
          max_tokens       – budget cap (~4 chars/token); 0 = unlimited (default 0)
          max_items        – hard limit on total items returned
          include_sessions – include last session summary (default False)

        Returns {"pinned": [...], "recent": [...], "last_session": {...} | None}
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
