"""
Neo4j knowledge graph storage backend for Mnemosyne.

Graph Schema:
  (:MemoryItem {id, kind, title, content, content_compact, created_at, updated_at,
                pinned, importance, workspace_hint, source})
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
import math
import os
from datetime import datetime, timezone
from typing import Any

from neo4j import AsyncGraphDatabase, AsyncDriver

from .base import MemoryStorage, RequestContext, BootstrapMode, ContentPrefer

logger = logging.getLogger(__name__)

VALID_KINDS = {"answer", "decision", "pattern", "command", "note"}

# --- Context pollution mitigation: ranking constants ---
KIND_WEIGHTS: dict[str, float] = {
    "decision": 1.4,
    "pattern": 1.3,
    "command": 1.2,
    "answer": 1.1,
    "note": 0.7,
}
RECENCY_HALF_LIFE_DAYS = 14.0
WORKSPACE_MATCH_BOOST = 1.2
WORKSPACE_MISMATCH_PENALTY = 0.8
# Max chars for auto-generated compact content
AUTO_COMPACT_MAX_CHARS = 200
# Kinds eligible for full content in "hybrid" mode (if short enough)
HYBRID_FULL_KINDS = {"command", "pattern"}
HYBRID_FULL_MAX_CHARS = 300


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _auto_compact(content: str, max_chars: int = AUTO_COMPACT_MAX_CHARS) -> str:
    """Generate a compact snippet from full content.

    Deterministic heuristic: take first ``max_chars`` characters, break at
    the last sentence boundary (period/newline) if possible, append "…".
    """
    content = (content or "").strip()
    if len(content) <= max_chars:
        return content
    truncated = content[:max_chars]
    # Try to break at a sentence boundary
    for sep in ("\n", ". ", "! ", "? "):
        idx = truncated.rfind(sep)
        if idx > max_chars // 2:
            truncated = truncated[: idx + len(sep)].rstrip()
            break
    return truncated + "…"


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return math.ceil(len(text) / 4) if text else 0


def _recency_weight(updated_at: str, half_life_days: float = RECENCY_HALF_LIFE_DAYS) -> float:
    """Half-life decay weight based on age."""
    try:
        updated = datetime.fromisoformat(updated_at)
        age = datetime.now(timezone.utc) - updated
        age_days = max(age.total_seconds() / 86400, 0)
        return 0.5 ** (age_days / half_life_days)
    except (ValueError, TypeError):
        return 0.5  # fallback for unparseable dates


def _score_item(item: dict, workspace_hint: str = "global") -> float:
    """Score a memory item for ranking. Higher = more relevant."""
    kind = item.get("kind", "note")
    w_kind = KIND_WEIGHTS.get(kind, 0.7)
    w_recency = _recency_weight(item.get("updated_at", ""))
    importance = item.get("importance", 50) or 50
    item_workspace = item.get("workspace_hint") or ""
    if workspace_hint and workspace_hint != "global" and item_workspace:
        w_workspace = WORKSPACE_MATCH_BOOST if item_workspace == workspace_hint else WORKSPACE_MISMATCH_PENALTY
    else:
        w_workspace = 1.0
    return w_kind * w_recency * (0.5 + importance / 100) * w_workspace


def _select_content_for_mode(
    item: dict,
    mode: BootstrapMode,
) -> str:
    """Pick the right content string based on bootstrap mode."""
    content_compact = item.get("content_compact") or ""
    content_full = item.get("content") or ""
    if mode == "full":
        return content_full
    if mode == "hybrid":
        kind = item.get("kind", "note")
        if kind in HYBRID_FULL_KINDS and len(content_full) <= HYBRID_FULL_MAX_CHARS:
            return content_full
        return content_compact or _auto_compact(content_full)
    # thin
    return content_compact or _auto_compact(content_full)


def _render_item_thin(item: dict) -> str:
    """Render a memory item in thin format for bootstrap."""
    kind = item.get("kind", "note")
    title = item.get("title", "")
    content = item.get("content", "")
    tags = item.get("tags", "[]")
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except (json.JSONDecodeError, TypeError):
            tags = []
    updated = item.get("updated_at", "")
    tag_str = ",".join(tags) if tags else ""
    lines = [f"[{kind}] {title}"]
    if content:
        # Indent content bullets
        for line in content.split("\n")[:3]:
            line = line.strip()
            if line:
                lines.append(f"  {line}")
    meta_parts = []
    if tag_str:
        meta_parts.append(f"tags: {tag_str}")
    if updated:
        meta_parts.append(f"updated: {updated[:19]}")
    if meta_parts:
        lines.append(f"  {' | '.join(meta_parts)}")
    return "\n".join(lines)


class Neo4jStorage(MemoryStorage):
    def __init__(
        self,
        uri: str = "bolt://localhost:7687",
        user: str = "neo4j",
        password: str = "mnemosyne",
        database: str = "neo4j",
        multi_tenant: bool | None = None,
    ):
        self.uri = uri
        self.user = user
        self.password = password
        self.database = database
        self._driver: AsyncDriver | None = None
        # Feature flag for multi-tenancy; default from env `MNEMOSYNE_MULTI_TENANT`
        if multi_tenant is None:
            env_val = os.environ.get("MNEMOSYNE_MULTI_TENANT", "0").strip()
            self._multi_tenant = env_val in ("1", "true", "True", "yes")
        else:
            self._multi_tenant = bool(multi_tenant)

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
            # Fulltext index for search (includes content_compact)
            try:
                await session.run(
                    "CREATE FULLTEXT INDEX memory_fulltext IF NOT EXISTS "
                    "FOR (m:MemoryItem) ON EACH [m.title, m.content, m.content_compact]"
                )
            except Exception as e:
                # Fulltext index might already exist with different config
                logger.warning("Fulltext index creation: %s", e)

            # Index for workspace_hint scoping
            await session.run(
                "CREATE INDEX memory_item_workspace IF NOT EXISTS "
                "FOR (m:MemoryItem) ON (m.workspace_hint)"
            )

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
            # Space id uniqueness (for multi-tenancy)
            await session.run(
                "CREATE CONSTRAINT space_id_unique IF NOT EXISTS "
                "FOR (s:Space) REQUIRE s.id IS UNIQUE"
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
            await session.run(
                "CREATE INDEX session_space IF NOT EXISTS "
                "FOR (s:Session) ON (s.space_id)"
            )

            # Compound index to enforce per-space dedup by (kind, title)
            await session.run(
                "CREATE INDEX memory_item_space_kind_title IF NOT EXISTS "
                "FOR (m:MemoryItem) ON (m.space_id, m.kind, m.title)"
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
        content_compact: str | None = None,
        workspace_hint: str | None = None,
        importance: int | None = None,
        source: str | None = None,
        context: RequestContext | None = None,
    ) -> dict[str, Any]:
        kind = (kind or "").strip().lower()
        if kind not in VALID_KINDS:
            kind = "note"
        title = title.strip()
        content = content.strip()
        tags = tags or []
        now = _now()

        # Auto-generate compact content if not provided
        if content_compact is None:
            content_compact = _auto_compact(content)
        else:
            content_compact = content_compact.strip()

        # Normalize importance (0-100, default 50)
        if importance is None:
            importance = 50
        importance = max(0, min(100, importance))

        # Normalize source
        source = (source or "agent").strip()

        # Normalize workspace_hint
        workspace_hint = (workspace_hint or "").strip() or None

        async with self._driver.session(database=self.database) as session:
            if self._multi_tenant:
                space_id, _ = self._derive_space_and_allowed(context)
                # Ensure space exists and upsert memory within space scope
                result = await session.run(
                    """
                    MERGE (s:Space {id: $space_id})
                    MERGE (m:MemoryItem {space_id: $space_id, kind: $kind, title: $title})
                    ON CREATE SET
                        m.content = $content,
                        m.content_compact = $content_compact,
                        m.created_at = $now,
                        m.updated_at = $now,
                        m.pinned = $pinned,
                        m.importance = $importance,
                        m.workspace_hint = $workspace_hint,
                        m.source = $source
                    ON MATCH SET
                        m.content = $content,
                        m.content_compact = $content_compact,
                        m.updated_at = $now,
                        m.pinned = $pinned,
                        m.importance = $importance,
                        m.workspace_hint = $workspace_hint,
                        m.source = $source
                    WITH s, m,
                         CASE WHEN m.created_at = $now THEN 'created' ELSE 'updated' END AS action
                    MERGE (s)-[:CONTAINS]->(m)
                    RETURN elementId(m) AS id, action
                    """,
                    space_id=space_id,
                    kind=kind,
                    title=title,
                    content=content,
                    content_compact=content_compact,
                    now=now,
                    pinned=pinned,
                    importance=importance,
                    workspace_hint=workspace_hint,
                    source=source,
                )
            else:
                # Legacy single-tenant behavior
                result = await session.run(
                    """
                    MERGE (m:MemoryItem {kind: $kind, title: $title})
                    ON CREATE SET
                        m.content = $content,
                        m.content_compact = $content_compact,
                        m.created_at = $now,
                        m.updated_at = $now,
                        m.pinned = $pinned,
                        m.importance = $importance,
                        m.workspace_hint = $workspace_hint,
                        m.source = $source
                    ON MATCH SET
                        m.content = $content,
                        m.content_compact = $content_compact,
                        m.updated_at = $now,
                        m.pinned = $pinned,
                        m.importance = $importance,
                        m.workspace_hint = $workspace_hint,
                        m.source = $source
                    WITH m,
                         CASE WHEN m.created_at = $now THEN 'created' ELSE 'updated' END AS action
                    RETURN elementId(m) AS id, action
                    """,
                    kind=kind,
                    title=title,
                    content=content,
                    content_compact=content_compact,
                    now=now,
                    pinned=pinned,
                    importance=importance,
                    workspace_hint=workspace_hint,
                    source=source,
                )
            record = await result.single()
            item_id = record["id"]
            action = record["action"]

            # Remove old tag relationships and create new ones
            if self._multi_tenant:
                await session.run(
                    "MATCH (m:MemoryItem {space_id: $space_id, kind: $kind, title: $title})-[r:TAGGED_WITH]->() DELETE r",
                    space_id=space_id,
                    kind=kind,
                    title=title,
                )
            else:
                await session.run(
                    "MATCH (m:MemoryItem {kind: $kind, title: $title})-[r:TAGGED_WITH]->() DELETE r",
                    kind=kind,
                    title=title,
                )

            for tag_name in tags:
                tag_name = tag_name.strip()
                if tag_name:
                    if self._multi_tenant:
                        await session.run(
                            """
                            MATCH (m:MemoryItem {space_id: $space_id, kind: $kind, title: $title})
                            MERGE (t:Tag {name: $tag})
                            MERGE (m)-[:TAGGED_WITH]->(t)
                            """,
                            space_id=space_id,
                            kind=kind,
                            title=title,
                            tag=tag_name,
                        )
                    else:
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

    async def search_memory(
        self,
        query: str,
        limit: int = 8,
        prefer: ContentPrefer = "full",
        snippet_chars: int = 400,
        context: RequestContext | None = None,
    ) -> list[dict[str, Any]]:
        query = (query or "").strip()
        if not query:
            return []

        limit = max(1, min(limit, 25))

        async with self._driver.session(database=self.database) as session:
            spaces: list[str] | None = None
            if self._multi_tenant:
                _, allowed = self._derive_space_and_allowed(context)
                spaces = allowed
            # Use fulltext index for search
            try:
                if self._multi_tenant:
                    result = await session.run(
                        """
                        CALL db.index.fulltext.queryNodes('memory_fulltext', $search_text)
                        YIELD node, score
                        WHERE node.space_id IN $spaces
                        OPTIONAL MATCH (node)-[:TAGGED_WITH]->(t:Tag)
                        WITH node, score, collect(t.name) AS tags
                        RETURN
                            elementId(node) AS id,
                            node.kind AS kind,
                            node.title AS title,
                            node.content AS content,
                            node.content_compact AS content_compact,
                            tags,
                            node.pinned AS pinned,
                            node.updated_at AS updated_at,
                            node.importance AS importance,
                            node.workspace_hint AS workspace_hint,
                            score
                        ORDER BY score DESC
                        LIMIT $lim
                        """,
                        search_text=query,
                        lim=limit,
                        spaces=spaces,
                    )
                else:
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
                            node.content_compact AS content_compact,
                            tags,
                            node.pinned AS pinned,
                            node.updated_at AS updated_at,
                            node.importance AS importance,
                            node.workspace_hint AS workspace_hint,
                            score
                        ORDER BY score DESC
                        LIMIT $lim
                        """,
                        search_text=query,
                        lim=limit,
                    )
                records = [record.data() async for record in result]
                return self._format_search_results(records, prefer, snippet_chars)
            except Exception as e:
                logger.warning(
                    "Fulltext search failed, falling back to CONTAINS: %s", e
                )
                # Fallback: simple CONTAINS match
                if self._multi_tenant:
                    result = await session.run(
                        """
                        MATCH (m:MemoryItem)
                        WHERE (toLower(m.title) CONTAINS toLower($search_text)
                           OR toLower(m.content) CONTAINS toLower($search_text))
                          AND m.space_id IN $spaces
                        OPTIONAL MATCH (m)-[:TAGGED_WITH]->(t:Tag)
                        WITH m, collect(t.name) AS tags
                        RETURN
                            elementId(m) AS id,
                            m.kind AS kind,
                            m.title AS title,
                            m.content AS content,
                            m.content_compact AS content_compact,
                            tags,
                            m.pinned AS pinned,
                            m.updated_at AS updated_at,
                            m.importance AS importance,
                            m.workspace_hint AS workspace_hint
                        ORDER BY m.updated_at DESC
                        LIMIT $lim
                        """,
                        search_text=query,
                        lim=limit,
                        spaces=spaces,
                    )
                else:
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
                            m.content_compact AS content_compact,
                            tags,
                            m.pinned AS pinned,
                            m.updated_at AS updated_at,
                            m.importance AS importance,
                            m.workspace_hint AS workspace_hint
                        ORDER BY m.updated_at DESC
                        LIMIT $lim
                        """,
                        search_text=query,
                        lim=limit,
                    )
                records = [record.data() async for record in result]
                return self._format_search_results(records, prefer, snippet_chars)

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
        limit_pinned = max(0, min(limit_pinned, 25))
        limit_recent = max(0, min(limit_recent, 50))
        max_items = max(1, min(max_items, 50))
        workspace_hint = (workspace_hint or "global").strip()

        async with self._driver.session(database=self.database) as session:
            spaces: list[str] | None = None
            if self._multi_tenant:
                _, allowed = self._derive_space_and_allowed(context)
                spaces = allowed

            # --- Fetch pinned items ---
            pinned_query_fields = """
                        elementId(m) AS id,
                        m.kind AS kind,
                        m.title AS title,
                        m.content AS content,
                        m.content_compact AS content_compact,
                        tags,
                        m.updated_at AS updated_at,
                        m.importance AS importance,
                        m.workspace_hint AS workspace_hint
            """
            if self._multi_tenant:
                pinned_result = await session.run(
                    f"""
                    MATCH (m:MemoryItem {{pinned: true}})
                    WHERE m.space_id IN $spaces
                    OPTIONAL MATCH (m)-[:TAGGED_WITH]->(t:Tag)
                    WITH m, collect(t.name) AS tags
                    RETURN {pinned_query_fields}
                    ORDER BY m.updated_at DESC
                    LIMIT $limit
                    """,
                    limit=limit_pinned,
                    spaces=spaces,
                )
            else:
                pinned_result = await session.run(
                    f"""
                    MATCH (m:MemoryItem {{pinned: true}})
                    OPTIONAL MATCH (m)-[:TAGGED_WITH]->(t:Tag)
                    WITH m, collect(t.name) AS tags
                    RETURN {pinned_query_fields}
                    ORDER BY m.updated_at DESC
                    LIMIT $limit
                    """,
                    limit=limit_pinned,
                )
            pinned_raw = [r.data() async for r in pinned_result]

            # --- Fetch recent items (over-fetch for ranking) ---
            fetch_limit = max(limit_recent * 3, max_items * 2)
            if self._multi_tenant:
                recent_result = await session.run(
                    f"""
                    MATCH (m:MemoryItem)
                    WHERE m.space_id IN $spaces
                    OPTIONAL MATCH (m)-[:TAGGED_WITH]->(t:Tag)
                    WITH m, collect(t.name) AS tags
                    RETURN {pinned_query_fields}
                    ORDER BY m.updated_at DESC
                    LIMIT $limit
                    """,
                    limit=fetch_limit,
                    spaces=spaces,
                )
            else:
                recent_result = await session.run(
                    f"""
                    MATCH (m:MemoryItem)
                    OPTIONAL MATCH (m)-[:TAGGED_WITH]->(t:Tag)
                    WITH m, collect(t.name) AS tags
                    RETURN {pinned_query_fields}
                    ORDER BY m.updated_at DESC
                    LIMIT $limit
                    """,
                    limit=fetch_limit,
                )
            recent_raw = [r.data() async for r in recent_result]

            # --- Fetch last session (if requested) ---
            last_session_data = None
            if include_sessions:
                session_records = await self.last_session(
                    workspace_hint=workspace_hint, limit=1, context=context
                )
                if session_records:
                    last_session_data = session_records[0]

            # --- Rank & budget (Python-side) ---
            pinned_ids = {p["id"] for p in pinned_raw}
            # Remove pinned from recent candidates
            recent_candidates = [r for r in recent_raw if r["id"] not in pinned_ids]

            # Score and sort recent candidates
            for item in recent_candidates:
                item["_score"] = _score_item(item, workspace_hint)
            recent_candidates.sort(key=lambda x: x["_score"], reverse=True)

            # Apply budgeting
            budget = max_tokens * 4 if max_tokens > 0 else float("inf")  # chars
            used = 0
            pinned_out = []
            recent_out = []

            # Pinned items always included (they're pinned for a reason!) — but shaped
            for item in pinned_raw:
                content_text = _select_content_for_mode(item, mode)
                cost = len(content_text) + len(item.get("title", ""))
                formatted = self._format_bootstrap_item(item, content_text)
                pinned_out.append(formatted)
                used += cost
                if len(pinned_out) >= max_items:
                    break

            # Fill recent with budget
            remaining_slots = max_items - len(pinned_out)
            for item in recent_candidates:
                if remaining_slots <= 0:
                    break
                content_text = _select_content_for_mode(item, mode)
                cost = len(content_text) + len(item.get("title", ""))
                if max_tokens > 0 and used + cost > budget:
                    continue  # skip this item, try smaller ones
                formatted = self._format_bootstrap_item(item, content_text)
                recent_out.append(formatted)
                used += cost
                remaining_slots -= 1

            result = {"pinned": pinned_out, "recent": recent_out}
            if include_sessions:
                result["last_session"] = last_session_data
            return result

    def _format_bootstrap_item(self, raw: dict, content_text: str) -> dict:
        """Format a raw Neo4j record into a bootstrap response item."""
        tags = raw.get("tags", [])
        if isinstance(tags, list):
            tags = json.dumps(tags)
        has_full = bool(raw.get("content") and raw.get("content") != content_text)
        return {
            "id": raw["id"],
            "kind": raw.get("kind", "note"),
            "title": raw.get("title", ""),
            "content": content_text,
            "tags": tags,
            "updated_at": raw.get("updated_at", ""),
            "has_full": has_full,
        }

    def _format_search_results(
        self, records: list[dict], prefer: ContentPrefer, snippet_chars: int
    ) -> list[dict[str, Any]]:
        """Format search results with content preference."""
        results = []
        for r in records:
            content_full = r.get("content") or ""
            content_compact = r.get("content_compact") or ""
            has_full = bool(content_full)

            if prefer == "compact":
                if content_compact:
                    content = content_compact
                else:
                    content = _auto_compact(content_full, max_chars=snippet_chars)
            else:
                content = content_full

            results.append({
                "id": r["id"],
                "kind": r["kind"],
                "title": r["title"],
                "content": content,
                "tags": json.dumps(r.get("tags", [])),
                "pinned": 1 if r.get("pinned") else 0,
                "updated_at": r.get("updated_at", ""),
                "has_full": has_full,
            })
        return results

    async def read_memory(
        self,
        item_id: str,
        prefer: ContentPrefer = "full",
        context: RequestContext | None = None,
    ) -> dict[str, Any] | None:
        """Read a single memory item by its Neo4j element id."""
        async with self._driver.session(database=self.database) as session:
            result = await session.run(
                """
                MATCH (m:MemoryItem)
                WHERE elementId(m) = $item_id
                OPTIONAL MATCH (m)-[:TAGGED_WITH]->(t:Tag)
                WITH m, collect(t.name) AS tags
                RETURN
                    elementId(m) AS id,
                    m.kind AS kind,
                    m.title AS title,
                    m.content AS content,
                    m.content_compact AS content_compact,
                    tags,
                    m.pinned AS pinned,
                    m.updated_at AS updated_at,
                    m.created_at AS created_at,
                    m.importance AS importance,
                    m.workspace_hint AS workspace_hint,
                    m.source AS source
                """,
                item_id=item_id,
            )
            record = await result.single()
            if record is None:
                return None

            r = record.data()
            content_full = r.get("content") or ""
            content_compact = r.get("content_compact") or ""

            if prefer == "compact":
                content = content_compact or _auto_compact(content_full)
            else:
                content = content_full

            return {
                "id": r["id"],
                "kind": r["kind"],
                "title": r["title"],
                "content": content,
                "content_compact": content_compact,
                "content_full": content_full,
                "tags": json.dumps(r.get("tags", [])),
                "pinned": 1 if r.get("pinned") else 0,
                "updated_at": r.get("updated_at", ""),
                "created_at": r.get("created_at", ""),
                "importance": r.get("importance", 50),
                "workspace_hint": r.get("workspace_hint", ""),
                "source": r.get("source", ""),
            }

    async def commit_session(
        self,
        workspace_hint: str,
        summary: str,
        decisions: list[str] | None = None,
        next_steps: list[str] | None = None,
        context: RequestContext | None = None,
    ) -> dict[str, Any]:
        workspace_hint = (workspace_hint or "global").strip()
        summary = (summary or "").strip()
        decisions = decisions or []
        next_steps = next_steps or []
        now = _now()

        async with self._driver.session(database=self.database) as session:
            if self._multi_tenant:
                space_id, _ = self._derive_space_and_allowed(context)
                # Create session node linked to workspace and space
                await session.run(
                    """
                    MERGE (w:Workspace {name: $workspace})
                    MERGE (sp:Space {id: $space_id})
                    CREATE (s:Session {
                        workspace_hint: $workspace,
                        summary: $summary,
                        decisions: $decisions,
                        next_steps: $next_steps,
                        created_at: $now,
                        space_id: $space_id
                    })
                    CREATE (s)-[:IN_WORKSPACE]->(w)
                    CREATE (s)-[:IN_SPACE]->(sp)
                    WITH s, w
                    OPTIONAL MATCH (prev:Session)-[:IN_WORKSPACE]->(w)
                    WHERE prev <> s AND prev.space_id = $space_id
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
                    space_id=space_id,
                )
            else:
                # Legacy single-tenant behavior
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
        self,
        workspace_hint: str = "global",
        limit: int = 3,
        context: RequestContext | None = None,
    ) -> list[dict[str, Any]]:
        workspace_hint = (workspace_hint or "global").strip()
        limit = max(1, min(limit, 10))

        async with self._driver.session(database=self.database) as session:
            if self._multi_tenant:
                _, allowed = self._derive_space_and_allowed(context)
                result = await session.run(
                    """
                    MATCH (s:Session {workspace_hint: $workspace})
                    WHERE s.space_id IN $spaces
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
                    spaces=allowed,
                )
            else:
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

    def _derive_space_and_allowed(
        self, context: RequestContext | None
    ) -> tuple[str, list[str]]:
        ctx = context or {}
        user_id = (ctx.get("user_id") or "").strip()
        space_id = (ctx.get("space_id") or "").strip()
        if not space_id:
            space_id = f"personal:{user_id}" if user_id else "global"
        allowed = ctx.get("allowed_spaces")
        if not isinstance(allowed, list) or not allowed:
            allowed = [space_id]
        return space_id, allowed
