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
