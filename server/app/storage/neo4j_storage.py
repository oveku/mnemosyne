"""
Neo4j knowledge graph storage backend for Mnemosyne.

Graph Schema:
  (:MemoryItem {id, kind, title, content, created_at, updated_at, pinned})
    -[:TAGGED_WITH]-> (:Tag {name})
    -[:DECIDED_IN]-> (:Session)
    -[:RELATES_TO]-> (:MemoryItem)

  (:Session {id, workspace_hint, summary, created_at})
    -[:FOLLOWS]-> (:Session)
    -[:IN_WORKSPACE]-> (:Workspace {name})
    -[:HAS_DECISION]-> (decision:string)
    -[:HAS_NEXT_STEP]-> (next_step:string)
"""

import json
import logging
from datetime import datetime, timezone
from typing import Any

from neo4j import AsyncGraphDatabase, AsyncDriver

from .base import MemoryStorage

logger = logging.getLogger(__name__)

VALID_KINDS = {"answer", "decision", "pattern", "command", "note"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Neo4jStorage(MemoryStorage):
    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        user: str = "neo4j",
        password: str = "mnemosyne",
        database: str = "neo4j",
    ):
        self.uri = uri
        self.user = user
        self.password = password
        self.database = database
        self._driver: AsyncDriver | None = None

    async def initialize(self) -> None:
        """Connect to Neo4j and create indexes/constraints."""
        self._driver = AsyncGraphDatabase.driver(
            self.uri, auth=(self.user, self.password)
        )

        # Verify connectivity
        async with self._driver.session(database=self.database) as session:
            await session.run("RETURN 1")

        # Create constraints and indexes
        async with self._driver.session(database=self.database) as session:
            # Unique constraint on MemoryItem kind+title for dedup
            await session.run(
                "CREATE INDEX memory_item_kind_title IF NOT EXISTS "
                "FOR (m:MemoryItem) ON (m.kind, m.title)"
            )
            # Index for pinned lookups
            await session.run(
                "CREATE INDEX memory_item_pinned IF NOT EXISTS "
                "FOR (m:MemoryItem) ON (m.pinned)"
            )
            # Index for updated_at ordering
            await session.run(
                "CREATE INDEX memory_item_updated IF NOT EXISTS "
                "FOR (m:MemoryItem) ON (m.updated_at)"
            )
            # Fulltext index for search
            try:
                await session.run(
                    "CREATE FULLTEXT INDEX memory_fulltext IF NOT EXISTS "
                    "FOR (m:MemoryItem) ON EACH [m.title, m.content]"
                )
            except Exception as e:
                # Fulltext index might already exist with different config
                logger.warning("Fulltext index creation: %s", e)

            # Tag uniqueness
            await session.run(
                "CREATE CONSTRAINT tag_name_unique IF NOT EXISTS "
                "FOR (t:Tag) REQUIRE t.name IS UNIQUE"
            )
            # Workspace uniqueness
            await session.run(
                "CREATE CONSTRAINT workspace_name_unique IF NOT EXISTS "
                "FOR (w:Workspace) REQUIRE w.name IS UNIQUE"
            )
            # Session index
            await session.run(
                "CREATE INDEX session_created IF NOT EXISTS "
                "FOR (s:Session) ON (s.created_at)"
            )
            await session.run(
                "CREATE INDEX session_workspace IF NOT EXISTS "
                "FOR (s:Session) ON (s.workspace_hint)"
            )

        logger.info("Neo4j storage initialized at %s", self.uri)

    async def close(self) -> None:
        if self._driver:
            await self._driver.close()
            self._driver = None

    async def write_memory(
        self,
        kind: str,
        title: str,
        content: str,
        tags: list[str] | None = None,
        pinned: bool = False,
    ) -> dict[str, Any]:
        kind = (kind or "").strip().lower()
        if kind not in VALID_KINDS:
            kind = "note"
        title = title.strip()
        content = content.strip()
        tags = tags or []
        now = _now()

        async with self._driver.session(database=self.database) as session:
            # MERGE on kind+title for dedup (update if exists, create if not)
            result = await session.run(
                """
                MERGE (m:MemoryItem {kind: $kind, title: $title})
                ON CREATE SET
                    m.content = $content,
                    m.created_at = $now,
                    m.updated_at = $now,
                    m.pinned = $pinned
                ON MATCH SET
                    m.content = $content,
                    m.updated_at = $now,
                    m.pinned = $pinned
                WITH m,
                     CASE WHEN m.created_at = $now THEN 'created' ELSE 'updated' END AS action
                RETURN elementId(m) AS id, action
                """,
                kind=kind,
                title=title,
                content=content,
                now=now,
                pinned=pinned,
            )
            record = await result.single()
            item_id = record["id"]
            action = record["action"]

            # Remove old tag relationships and create new ones
            await session.run(
                "MATCH (m:MemoryItem {kind: $kind, title: $title})-[r:TAGGED_WITH]->() DELETE r",
                kind=kind,
                title=title,
            )

            for tag_name in tags:
                tag_name = tag_name.strip()
                if tag_name:
                    await session.run(
                        """
                        MATCH (m:MemoryItem {kind: $kind, title: $title})
                        MERGE (t:Tag {name: $tag})
                        MERGE (m)-[:TAGGED_WITH]->(t)
                        """,
                        kind=kind,
                        title=title,
                        tag=tag_name,
                    )

            return {"ok": True, "action": action, "id": str(item_id)}

    async def search_memory(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        query = (query or "").strip()
        if not query:
            return []

        limit = max(1, min(limit, 25))

        async with self._driver.session(database=self.database) as session:
            # Use fulltext index for search
            try:
                result = await session.run(
                    """
                    CALL db.index.fulltext.queryNodes('memory_fulltext', $search_text)
                    YIELD node, score
                    OPTIONAL MATCH (node)-[:TAGGED_WITH]->(t:Tag)
                    WITH node, score, collect(t.name) AS tags
                    RETURN
                        elementId(node) AS id,
                        node.kind AS kind,
                        node.title AS title,
                        node.content AS content,
                        tags,
                        node.pinned AS pinned,
                        node.updated_at AS updated_at,
                        score
                    ORDER BY score DESC
                    LIMIT $lim
                    """,
                    search_text=query,
                    lim=limit,
                )
                records = [record.data() async for record in result]
                return [
                    {
                        "id": r["id"],
                        "kind": r["kind"],
                        "title": r["title"],
                        "content": r["content"],
                        "tags": json.dumps(r["tags"]),
                        "pinned": 1 if r["pinned"] else 0,
                        "updated_at": r["updated_at"],
                    }
                    for r in records
                ]
            except Exception as e:
                logger.warning(
                    "Fulltext search failed, falling back to CONTAINS: %s", e
                )
                # Fallback: simple CONTAINS match
                result = await session.run(
                    """
                    MATCH (m:MemoryItem)
                    WHERE toLower(m.title) CONTAINS toLower($search_text)
                       OR toLower(m.content) CONTAINS toLower($search_text)
                    OPTIONAL MATCH (m)-[:TAGGED_WITH]->(t:Tag)
                    WITH m, collect(t.name) AS tags
                    RETURN
                        elementId(m) AS id,
                        m.kind AS kind,
                        m.title AS title,
                        m.content AS content,
                        tags,
                        m.pinned AS pinned,
                        m.updated_at AS updated_at
                    ORDER BY m.updated_at DESC
                    LIMIT $lim
                    """,
                    search_text=query,
                    lim=limit,
                )
                records = [record.data() async for record in result]
                return [
                    {
                        "id": r["id"],
                        "kind": r["kind"],
                        "title": r["title"],
                        "content": r["content"],
                        "tags": json.dumps(r["tags"]),
                        "pinned": 1 if r["pinned"] else 0,
                        "updated_at": r["updated_at"],
                    }
                    for r in records
                ]

    async def bootstrap(
        self, limit_pinned: int = 8, limit_recent: int = 10
    ) -> dict[str, Any]:
        limit_pinned = max(0, min(limit_pinned, 25))
        limit_recent = max(0, min(limit_recent, 50))

        async with self._driver.session(database=self.database) as session:
            # Pinned items
            pinned_result = await session.run(
                """
                MATCH (m:MemoryItem {pinned: true})
                OPTIONAL MATCH (m)-[:TAGGED_WITH]->(t:Tag)
                WITH m, collect(t.name) AS tags
                RETURN
                    elementId(m) AS id,
                    m.kind AS kind,
                    m.title AS title,
                    m.content AS content,
                    tags,
                    m.updated_at AS updated_at
                ORDER BY m.updated_at DESC
                LIMIT $limit
                """,
                limit=limit_pinned,
            )
            pinned = [
                {
                    "id": r["id"],
                    "kind": r["kind"],
                    "title": r["title"],
                    "content": r["content"],
                    "tags": json.dumps(r["tags"]),
                    "updated_at": r["updated_at"],
                }
                async for r in pinned_result
            ]

            # Recent items
            recent_result = await session.run(
                """
                MATCH (m:MemoryItem)
                OPTIONAL MATCH (m)-[:TAGGED_WITH]->(t:Tag)
                WITH m, collect(t.name) AS tags
                RETURN
                    elementId(m) AS id,
                    m.kind AS kind,
                    m.title AS title,
                    m.content AS content,
                    tags,
                    m.updated_at AS updated_at
                ORDER BY m.updated_at DESC
                LIMIT $limit
                """,
                limit=limit_recent,
            )
            recent = [
                {
                    "id": r["id"],
                    "kind": r["kind"],
                    "title": r["title"],
                    "content": r["content"],
                    "tags": json.dumps(r["tags"]),
                    "updated_at": r["updated_at"],
                }
                async for r in recent_result
            ]

            return {"pinned": pinned, "recent": recent}

    async def commit_session(
        self,
        workspace_hint: str,
        summary: str,
        decisions: list[str] | None = None,
        next_steps: list[str] | None = None,
    ) -> dict[str, Any]:
        workspace_hint = (workspace_hint or "global").strip()
        summary = (summary or "").strip()
        decisions = decisions or []
        next_steps = next_steps or []
        now = _now()

        async with self._driver.session(database=self.database) as session:
            # Create session node linked to workspace
            await session.run(
                """
                MERGE (w:Workspace {name: $workspace})
                CREATE (s:Session {
                    workspace_hint: $workspace,
                    summary: $summary,
                    decisions: $decisions,
                    next_steps: $next_steps,
                    created_at: $now
                })
                CREATE (s)-[:IN_WORKSPACE]->(w)
                WITH s, w
                OPTIONAL MATCH (prev:Session)-[:IN_WORKSPACE]->(w)
                WHERE prev <> s
                WITH s, prev
                ORDER BY prev.created_at DESC
                LIMIT 1
                FOREACH (_ IN CASE WHEN prev IS NOT NULL THEN [1] ELSE [] END |
                    CREATE (s)-[:FOLLOWS]->(prev)
                )
                """,
                workspace=workspace_hint,
                summary=summary,
                decisions=json.dumps(decisions),
                next_steps=json.dumps(next_steps),
                now=now,
            )

            return {"ok": True}

    async def last_session(
        self, workspace_hint: str = "global", limit: int = 3
    ) -> list[dict[str, Any]]:
        workspace_hint = (workspace_hint or "global").strip()
        limit = max(1, min(limit, 10))

        async with self._driver.session(database=self.database) as session:
            result = await session.run(
                """
                MATCH (s:Session {workspace_hint: $workspace})
                RETURN
                    elementId(s) AS id,
                    s.created_at AS created_at,
                    s.workspace_hint AS workspace_hint,
                    s.summary AS summary,
                    s.decisions AS decisions,
                    s.next_steps AS next_steps
                ORDER BY s.created_at DESC
                LIMIT $limit
                """,
                workspace=workspace_hint,
                limit=limit,
            )
            records = [record.data() async for record in result]
            return [
                {
                    "id": r["id"],
                    "created_at": r["created_at"],
                    "workspace_hint": r["workspace_hint"],
                    "summary": r["summary"],
                    "decisions": (
                        json.loads(r["decisions"])
                        if isinstance(r["decisions"], str)
                        else r["decisions"]
                    ),
                    "next_steps": (
                        json.loads(r["next_steps"])
                        if isinstance(r["next_steps"], str)
                        else r["next_steps"]
                    ),
                }
                for r in records
            ]
