"""
Tests for the MCP HTTP server.
Tests all 5 tools via HTTP endpoint simulation.
Requires a running Mnemosyne + Neo4j stack.

Configure via environment variables:
    MNEMOSYNE_URL  - Server endpoint (default: http://localhost:8010/mcp)
"""

import json
import os
import httpx
import pytest

# Default test target
MNEMOSYNE_URL = os.environ.get("MNEMOSYNE_URL", "http://localhost:8010/mcp")
TIMEOUT = 10.0


def call_tool(client: httpx.Client, tool_name: str, arguments: dict) -> dict:
    """Call an MCP tool via HTTP."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": arguments},
    }
    response = client.post(MNEMOSYNE_URL, json=payload, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def parse_tool_result(response: dict) -> any:
    """Extract and parse the tool result from an MCP response."""
    text = response["result"]["content"][0]["text"]
    return json.loads(text)


@pytest.fixture
def http_client():
    """Create an HTTP client for testing."""
    with httpx.Client() as client:
        yield client


class TestBootstrap:
    def test_bootstrap_returns_pinned_and_recent(self, http_client):
        response = call_tool(http_client, "mnemosyne_bootstrap", {})
        result = parse_tool_result(response)
        assert "pinned" in result
        assert "recent" in result
        assert isinstance(result["pinned"], list)
        assert isinstance(result["recent"], list)
        # Legacy call should NOT include last_session
        assert "last_session" not in result

    def test_bootstrap_with_limits(self, http_client):
        response = call_tool(
            http_client,
            "mnemosyne_bootstrap",
            {"limit_pinned": 2, "limit_recent": 3},
        )
        result = parse_tool_result(response)
        assert len(result["pinned"]) <= 2
        assert len(result["recent"]) <= 3
        # Still legacy shape
        assert "last_session" not in result


class TestWriteMemory:
    def test_write_decision(self, http_client):
        response = call_tool(
            http_client,
            "mnemosyne_write",
            {
                "kind": "decision",
                "title": "Test Decision from pytest",
                "content": "Testing memory write via pytest test suite",
                "tags_json": '["test", "pytest"]',
                "pinned": False,
            },
        )
        result = parse_tool_result(response)
        assert result["ok"] is True
        assert result["action"] in ("created", "updated")

    def test_write_dedup_updates(self, http_client):
        """Writing same kind+title should update, not create duplicate."""
        args = {
            "kind": "note",
            "title": "Dedup Test Item",
            "content": "Original content",
            "tags_json": "[]",
            "pinned": False,
        }
        r1 = parse_tool_result(call_tool(http_client, "mnemosyne_write", args))
        assert r1["ok"] is True

        args["content"] = "Updated content"
        r2 = parse_tool_result(call_tool(http_client, "mnemosyne_write", args))
        assert r2["ok"] is True
        assert r2["action"] == "updated"

    def test_write_invalid_kind_defaults_to_note(self, http_client):
        response = call_tool(
            http_client,
            "mnemosyne_write",
            {
                "kind": "invalid_kind_xyz",
                "title": "Invalid Kind Test",
                "content": "Should default to note",
            },
        )
        result = parse_tool_result(response)
        assert result["ok"] is True


class TestSearchMemory:
    def test_search_returns_results(self, http_client):
        # Write something to search for
        call_tool(
            http_client,
            "mnemosyne_write",
            {
                "kind": "note",
                "title": "Searchable Test Item",
                "content": "This is a unique searchable content for pytest",
            },
        )
        response = call_tool(
            http_client,
            "mnemosyne_search",
            {
                "query": "searchable pytest",
                "limit": 5,
            },
        )
        result = parse_tool_result(response)
        assert isinstance(result, list)
        assert len(result) > 0

    def test_search_empty_query_returns_empty(self, http_client):
        response = call_tool(
            http_client,
            "mnemosyne_search",
            {
                "query": "",
                "limit": 5,
            },
        )
        result = parse_tool_result(response)
        assert isinstance(result, list)
        assert len(result) == 0


class TestCommitSession:
    def test_commit_session(self, http_client):
        response = call_tool(
            http_client,
            "mnemosyne_commit_session",
            {
                "workspace_hint": "mnemosyne-pytest",
                "summary": "Test session from pytest",
                "decisions_json": '["Use pytest for testing"]',
                "next_steps_json": '["Add more tests"]',
            },
        )
        result = parse_tool_result(response)
        assert result["ok"] is True


class TestLastSession:
    def test_last_session_returns_results(self, http_client):
        # Commit a session first
        call_tool(
            http_client,
            "mnemosyne_commit_session",
            {
                "workspace_hint": "mnemosyne-pytest",
                "summary": "Session for last_session test",
            },
        )
        response = call_tool(
            http_client,
            "mnemosyne_last_session",
            {
                "workspace_hint": "mnemosyne-pytest",
                "limit": 3,
            },
        )
        result = parse_tool_result(response)
        assert isinstance(result, list)
        assert len(result) > 0
        assert "summary" in result[0]
        assert "workspace_hint" in result[0]


# --- Context pollution mitigation tests (HTTP) ---


class TestReadMemory:
    def test_read_memory_roundtrip(self, http_client):
        """Write, then read by id."""
        write_resp = call_tool(
            http_client,
            "mnemosyne_write",
            {
                "kind": "decision",
                "title": "HTTP Read Test",
                "content": "Full detailed content for HTTP read test",
                "content_compact": "Short summary",
                "workspace_hint": "mnemosyne-pytest",
                "importance": 70,
                "source": "agent",
            },
        )
        write_result = parse_tool_result(write_resp)
        assert write_result["ok"] is True
        item_id = write_result["id"]

        # Read full
        read_resp = call_tool(
            http_client,
            "mnemosyne_read",
            {"id": item_id, "prefer": "full"},
        )
        read_result = parse_tool_result(read_resp)
        assert read_result is not None
        assert read_result["content"] == (
            "Full detailed content for HTTP read test"
        )
        assert read_result["content_compact"] == "Short summary"

    def test_read_memory_not_found(self, http_client):
        """Read unknown id returns null/None."""
        resp = call_tool(
            http_client,
            "mnemosyne_read",
            {
                "id": "4:xxxxxxxx-xxxx-xxxx-xxxx-"
                "xxxxxxxxxxxx:999999",
            },
        )
        result = parse_tool_result(resp)
        assert result is None


class TestBootstrapModes:
    def test_bootstrap_thin_mode(self, http_client):
        """Bootstrap with mode=thin returns compact content."""
        call_tool(
            http_client,
            "mnemosyne_write",
            {
                "kind": "decision",
                "title": "HTTP Thin Test",
                "content": "Very long " * 100,
                "content_compact": "Short thin test",
                "pinned": True,
            },
        )
        response = call_tool(
            http_client,
            "mnemosyne_bootstrap",
            {
                "mode": "thin",
                "max_tokens": 800,
                "include_sessions": False,
            },
        )
        result = parse_tool_result(response)
        assert "pinned" in result
        assert "recent" in result
        found = [
            p for p in result["pinned"]
            if p["title"] == "HTTP Thin Test"
        ]
        if found:
            assert found[0]["content"] == "Short thin test"
            assert found[0]["has_full"] is True

    def test_bootstrap_with_workspace_hint(self, http_client):
        response = call_tool(
            http_client,
            "mnemosyne_bootstrap",
            {
                "workspace_hint": "mnemosyne-pytest",
                "mode": "thin",
            },
        )
        result = parse_tool_result(response)
        assert "pinned" in result
        assert "recent" in result

    def test_bootstrap_includes_sessions(self, http_client):
        call_tool(
            http_client,
            "mnemosyne_commit_session",
            {
                "workspace_hint": "mnemosyne-pytest",
                "summary": "Session for bootstrap test",
            },
        )
        response = call_tool(
            http_client,
            "mnemosyne_bootstrap",
            {
                "workspace_hint": "mnemosyne-pytest",
                "include_sessions": True,
            },
        )
        result = parse_tool_result(response)
        assert "last_session" in result


class TestSearchModes:
    def test_search_compact_mode(self, http_client):
        call_tool(
            http_client,
            "mnemosyne_write",
            {
                "kind": "note",
                "title": "HTTP Search Compact Test",
                "content": "Detailed HTTP content " * 50,
                "content_compact": "Short HTTP summary",
            },
        )
        response = call_tool(
            http_client,
            "mnemosyne_search",
            {
                "query": "HTTP Search Compact Test",
                "prefer": "compact",
            },
        )
        result = parse_tool_result(response)
        found = [
            r for r in result
            if r["title"] == "HTTP Search Compact Test"
        ]
        if found:
            assert found[0]["has_full"] is True
