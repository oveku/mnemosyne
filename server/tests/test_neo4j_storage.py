"""
Unit tests for Neo4j storage backend.
Requires a running Neo4j instance. Skips if not available.
"""

import json
import pytest
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

NEO4J_URI = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "mnemosyne")

# Check if neo4j driver is available
try:
    from storage.neo4j_storage import Neo4jStorage

    HAS_NEO4J = True
except ImportError:
    HAS_NEO4J = False

pytestmark = pytest.mark.skipif(not HAS_NEO4J, reason="neo4j driver not installed")


@pytest.fixture
async def storage():
    """Create and initialize a Neo4j storage instance."""
    s = Neo4jStorage(uri=NEO4J_URI, user=NEO4J_USER, password=NEO4J_PASSWORD)
    try:
        await s.initialize()
    except Exception as e:
        pytest.skip(f"Neo4j not available: {e}")
    yield s
    # Clean up test data
    async with s._driver.session(database=s.database) as session:
        await session.run(
            "MATCH (m:MemoryItem) WHERE m.title STARTS WITH 'Neo4j Test:' DETACH DELETE m"
        )
        await session.run(
            "MATCH (s:Session {workspace_hint: 'neo4j-pytest'}) DETACH DELETE s"
        )
    await s.close()


@pytest.mark.asyncio
async def test_write_and_search(storage):
    result = await storage.write_memory(
        kind="decision",
        title="Neo4j Test: Graph Storage",
        content="Using Neo4j for knowledge graph memory storage",
        tags=["neo4j", "graph", "test"],
        pinned=True,
    )
    assert result["ok"] is True
    assert result["action"] == "created"

    results = await storage.search_memory("knowledge graph memory", limit=5)
    assert len(results) > 0
    matching = [r for r in results if r["title"] == "Neo4j Test: Graph Storage"]
    assert len(matching) > 0


@pytest.mark.asyncio
async def test_dedup_by_kind_title(storage):
    r1 = await storage.write_memory(
        kind="note",
        title="Neo4j Test: Dedup",
        content="Original content",
    )
    assert r1["ok"] is True
    assert r1["action"] == "created"

    r2 = await storage.write_memory(
        kind="note",
        title="Neo4j Test: Dedup",
        content="Updated content",
    )
    assert r2["ok"] is True
    assert r2["action"] == "updated"


@pytest.mark.asyncio
async def test_bootstrap(storage):
    await storage.write_memory(
        kind="decision",
        title="Neo4j Test: Pinned Bootstrap",
        content="Pinned item for bootstrap test",
        pinned=True,
    )
    await storage.write_memory(
        kind="note",
        title="Neo4j Test: Regular Bootstrap",
        content="Regular item for bootstrap test",
        pinned=False,
    )

    result = await storage.bootstrap(limit_pinned=10, limit_recent=10)
    assert "pinned" in result
    assert "recent" in result
    pinned_titles = [p["title"] for p in result["pinned"]]
    assert "Neo4j Test: Pinned Bootstrap" in pinned_titles


@pytest.mark.asyncio
async def test_commit_and_last_session(storage):
    await storage.commit_session(
        workspace_hint="neo4j-pytest",
        summary="Neo4j test session",
        decisions=["Test with Neo4j"],
        next_steps=["Run more tests"],
    )

    sessions = await storage.last_session(workspace_hint="neo4j-pytest", limit=3)
    assert len(sessions) > 0
    assert sessions[0]["summary"] == "Neo4j test session"


@pytest.mark.asyncio
async def test_tags_as_nodes(storage):
    await storage.write_memory(
        kind="pattern",
        title="Neo4j Test: Tagged Pattern",
        content="Pattern with tag nodes",
        tags=["python", "testing", "neo4j"],
    )

    # Verify tags are stored as relationships
    async with storage._driver.session(database=storage.database) as session:
        result = await session.run(
            """
            MATCH (m:MemoryItem {title: 'Neo4j Test: Tagged Pattern'})-[:TAGGED_WITH]->(t:Tag)
            RETURN collect(t.name) AS tags
            """
        )
        record = await result.single()
        tags = record["tags"]
        assert "python" in tags
        assert "testing" in tags
        assert "neo4j" in tags


@pytest.mark.asyncio
async def test_search_empty_query(storage):
    results = await storage.search_memory("", limit=5)
    assert results == []


# --- Context pollution mitigation tests ---


@pytest.mark.asyncio
async def test_write_with_compact_content(storage):
    """content_compact is stored and retrievable."""
    result = await storage.write_memory(
        kind="decision",
        title="Neo4j Test: Compact Write",
        content="This is a very long detailed explanation of why we "
        "chose Neo4j as our primary storage backend. It includes "
        "performance benchmarks, comparison with alternatives, and "
        "detailed migration notes spanning multiple paragraphs.",
        content_compact="Chose Neo4j: fast graph queries, good ecosystem",
        workspace_hint="mnemosyne",
        importance=80,
        source="agent",
        tags=["neo4j", "test"],
    )
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_auto_compact_generation(storage):
    """When content_compact is not provided, it is auto-generated."""
    long_content = "A" * 500
    result = await storage.write_memory(
        kind="note",
        title="Neo4j Test: Auto Compact",
        content=long_content,
    )
    assert result["ok"] is True

    # Read back and verify compact was auto-generated
    item = await storage.read_memory(result["id"], prefer="compact")
    assert item is not None
    assert len(item["content"]) < len(long_content)
    assert item["content"].endswith("…")


@pytest.mark.asyncio
async def test_read_memory_full(storage):
    """read_memory returns full content when prefer=full."""
    full_content = "Full detailed content for read test"
    compact = "Short version"
    r = await storage.write_memory(
        kind="pattern",
        title="Neo4j Test: Read Full",
        content=full_content,
        content_compact=compact,
    )
    assert r["ok"] is True

    item = await storage.read_memory(r["id"], prefer="full")
    assert item is not None
    assert item["content"] == full_content
    assert item["content_compact"] == compact
    assert item["content_full"] == full_content


@pytest.mark.asyncio
async def test_read_memory_compact(storage):
    """read_memory returns compact content when prefer=compact."""
    r = await storage.write_memory(
        kind="pattern",
        title="Neo4j Test: Read Compact",
        content="Full detailed content here",
        content_compact="Short version",
    )
    item = await storage.read_memory(r["id"], prefer="compact")
    assert item is not None
    assert item["content"] == "Short version"


@pytest.mark.asyncio
async def test_read_memory_not_found(storage):
    """read_memory returns None for unknown ids."""
    item = await storage.read_memory(
        "4:xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx:999999"
    )
    assert item is None


@pytest.mark.asyncio
async def test_bootstrap_thin_mode(storage):
    """Bootstrap in thin mode returns compact content."""
    await storage.write_memory(
        kind="decision",
        title="Neo4j Test: Thin Boot",
        content="Very long " * 100,
        content_compact="Short decision summary",
        pinned=True,
    )
    result = await storage.bootstrap(
        limit_pinned=10,
        limit_recent=10,
        mode="thin",
    )
    assert "pinned" in result
    found = [
        p for p in result["pinned"]
        if p["title"] == "Neo4j Test: Thin Boot"
    ]
    assert len(found) > 0
    assert found[0]["content"] == "Short decision summary"
    assert found[0]["has_full"] is True


@pytest.mark.asyncio
async def test_bootstrap_full_mode_backward_compat(storage):
    """Bootstrap in full mode (default) returns full content."""
    full = "Full content " * 20
    await storage.write_memory(
        kind="note",
        title="Neo4j Test: Full Boot",
        content=full,
        content_compact="Short",
        pinned=True,
    )
    result = await storage.bootstrap(
        limit_pinned=10,
        limit_recent=10,
        mode="full",
    )
    found = [
        p for p in result["pinned"]
        if p["title"] == "Neo4j Test: Full Boot"
    ]
    assert len(found) > 0
    assert found[0]["content"] == full.strip()


@pytest.mark.asyncio
async def test_bootstrap_budget_enforcement(storage):
    """Bootstrap with max_tokens budget does not exceed it."""
    # Write several large items
    for i in range(10):
        await storage.write_memory(
            kind="note",
            title=f"Neo4j Test: Budget {i}",
            content="X" * 500,
            content_compact="Short",
            pinned=False,
        )
    result = await storage.bootstrap(
        limit_pinned=0,
        limit_recent=20,
        mode="thin",
        max_tokens=50,  # Very tight budget: ~200 chars
        max_items=20,
    )
    total_chars = sum(
        len(r["content"]) + len(r["title"])
        for r in result["recent"]
    )
    # Budget is max_tokens * 4 = 200 chars
    assert total_chars <= 200 + 100  # small tolerance for titles


@pytest.mark.asyncio
async def test_bootstrap_includes_last_session(storage):
    """Bootstrap with include_sessions=True returns session."""
    await storage.commit_session(
        workspace_hint="neo4j-pytest",
        summary="Session for bootstrap session test",
    )
    result = await storage.bootstrap(
        limit_pinned=5,
        limit_recent=5,
        workspace_hint="neo4j-pytest",
        include_sessions=True,
    )
    assert "last_session" in result
    if result["last_session"]:
        assert "summary" in result["last_session"]


@pytest.mark.asyncio
async def test_bootstrap_hybrid_mode(storage):
    """Hybrid mode returns full for short commands, compact for long notes."""
    await storage.write_memory(
        kind="command",
        title="Neo4j Test: Hybrid Cmd",
        content="docker compose up -d",
        content_compact="docker compose up",
        pinned=True,
    )
    await storage.write_memory(
        kind="note",
        title="Neo4j Test: Hybrid Note",
        content="Very long note " * 100,
        content_compact="Short note summary",
        pinned=True,
    )
    result = await storage.bootstrap(
        limit_pinned=10,
        limit_recent=10,
        mode="hybrid",
    )
    cmd = [
        p for p in result["pinned"]
        if p["title"] == "Neo4j Test: Hybrid Cmd"
    ]
    note = [
        p for p in result["pinned"]
        if p["title"] == "Neo4j Test: Hybrid Note"
    ]
    if cmd:
        # Short command should get full content in hybrid
        assert cmd[0]["content"] == "docker compose up -d"
    if note:
        # Long note should get compact content in hybrid
        assert note[0]["content"] == "Short note summary"


@pytest.mark.asyncio
async def test_search_compact_mode(storage):
    """Search with prefer=compact returns compact/snippet content."""
    await storage.write_memory(
        kind="note",
        title="Neo4j Test: Search Compact",
        content="Detailed searchable content " * 50,
        content_compact="Short searchable summary",
    )
    results = await storage.search_memory(
        "searchable",
        limit=5,
        prefer="compact",
    )
    found = [
        r for r in results
        if r["title"] == "Neo4j Test: Search Compact"
    ]
    if found:
        assert found[0]["content"] == "Short searchable summary"
        assert found[0]["has_full"] is True


@pytest.mark.asyncio
async def test_search_full_mode(storage):
    """Search with prefer=full returns full content."""
    full = "Full search content " * 10
    await storage.write_memory(
        kind="note",
        title="Neo4j Test: Search Full",
        content=full,
        content_compact="Short",
    )
    results = await storage.search_memory(
        "Full search content",
        limit=5,
        prefer="full",
    )
    found = [
        r for r in results
        if r["title"] == "Neo4j Test: Search Full"
    ]
    if found:
        assert found[0]["content"] == full.strip()


@pytest.mark.asyncio
async def test_write_read_roundtrip(storage):
    """Write with full+compact, bootstrap returns compact, read returns full."""
    full_content = "Detailed explanation " * 30
    compact = "Brief summary of the explanation"
    r = await storage.write_memory(
        kind="decision",
        title="Neo4j Test: Roundtrip",
        content=full_content,
        content_compact=compact,
        workspace_hint="mnemosyne",
        importance=75,
        pinned=True,
    )
    # Bootstrap in thin mode: should get compact
    boot = await storage.bootstrap(
        limit_pinned=10,
        limit_recent=5,
        mode="thin",
    )
    found = [
        p for p in boot["pinned"]
        if p["title"] == "Neo4j Test: Roundtrip"
    ]
    assert len(found) > 0
    assert found[0]["content"] == compact
    assert found[0]["has_full"] is True

    # Read by id: should get full
    item = await storage.read_memory(r["id"], prefer="full")
    assert item is not None
    assert item["content"] == full_content.strip()
    assert item["importance"] == 75
    assert item["workspace_hint"] == "mnemosyne"
