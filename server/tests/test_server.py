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

    def test_bootstrap_with_limits(self, http_client):
        response = call_tool(
            http_client,
            "mnemosyne_bootstrap",
            {"limit_pinned": 2, "limit_recent": 3},
        )
        result = parse_tool_result(response)
        assert len(result["pinned"]) <= 2
        assert len(result["recent"]) <= 3


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
