"""
Mnemosyne MCP Server

Persistent memory layer for AI coding agents.
Neo4j knowledge graph backend with MCP protocol over HTTP.

Environment variables:
    MNEMOSYNE_BIND        - Bind address (default: "0.0.0.0")
    MNEMOSYNE_PORT        - Port (default: 8010)
    NEO4J_URI             - Bolt URI (default: "bolt://localhost:7687")
    NEO4J_USER            - Username (default: "neo4j")
    NEO4J_PASSWORD        - Password (default: "mnemosyne")
    NEO4J_DATABASE        - Database name (default: "neo4j")
"""

import os
import json
import asyncio
import logging
from typing import Any
from http.server import HTTPServer, BaseHTTPRequestHandler

from storage.base import MemoryStorage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mnemosyne")

# Configuration
BIND = os.environ.get("MNEMOSYNE_BIND", "0.0.0.0")
PORT = int(os.environ.get("MNEMOSYNE_PORT", "8010"))

# Global storage instance
storage: MemoryStorage | None = None
loop: asyncio.AbstractEventLoop | None = None


def _create_storage() -> MemoryStorage:
    """Create Neo4j storage backend."""
    from storage.neo4j_storage import Neo4jStorage

    return Neo4jStorage(
        uri=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        user=os.environ.get("NEO4J_USER", "neo4j"),
        password=os.environ.get("NEO4J_PASSWORD", "mnemosyne"),
        database=os.environ.get("NEO4J_DATABASE", "neo4j"),
    )


def _run_async(coro):
    """Run an async coroutine in the event loop."""
    return loop.run_until_complete(coro)


def _ensure_list(val) -> list[str]:
    """Convert a JSON string or list to a list of strings."""
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
    return []


# MCP tool definitions
TOOLS = [
    {
        "name": "mnemosyne_bootstrap",
        "description": "Return startup context",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit_pinned": {"type": "integer"},
                "limit_recent": {"type": "integer"},
                "workspace_hint": {"type": "string"},
                "mode": {
                    "type": "string",
                    "enum": ["thin", "hybrid", "full"],
                },
                "max_tokens": {"type": "integer"},
                "max_items": {"type": "integer"},
                "include_sessions": {"type": "boolean"},
            },
        },
    },
    {
        "name": "mnemosyne_write",
        "description": "Store memory (deduplicates by kind+title)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "title": {"type": "string"},
                "content": {"type": "string"},
                "tags_json": {"type": "string"},
                "pinned": {"type": "boolean"},
                "content_compact": {"type": "string"},
                "workspace_hint": {"type": "string"},
                "importance": {"type": "integer"},
                "source": {"type": "string"},
            },
            "required": ["kind", "title", "content"],
        },
    },
    {
        "name": "mnemosyne_read",
        "description": "Read a single memory item by id",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "prefer": {
                    "type": "string",
                    "enum": ["full", "compact"],
                },
            },
            "required": ["id"],
        },
    },
    {
        "name": "mnemosyne_search",
        "description": "Search memory",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
                "prefer": {
                    "type": "string",
                    "enum": ["compact", "full"],
                },
                "snippet_chars": {"type": "integer"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "mnemosyne_commit_session",
        "description": "Commit session",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_hint": {"type": "string"},
                "summary": {"type": "string"},
                "decisions_json": {"type": "string"},
                "next_steps_json": {"type": "string"},
            },
            "required": ["workspace_hint", "summary"],
        },
    },
    {
        "name": "mnemosyne_last_session",
        "description": (
            "Get most recent session logs for a workspace"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "workspace_hint": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    },
]


def handle_tool_call(tool_name: str, arguments: dict, context: dict | None = None) -> Any:
    """Route a tool call to the appropriate storage method."""
    if tool_name == "mnemosyne_bootstrap":
        return _run_async(
            storage.bootstrap(
                arguments.get("limit_pinned", 8),
                arguments.get("limit_recent", 10),
                workspace_hint=arguments.get(
                    "workspace_hint", "global"
                ),
                mode=arguments.get("mode", "full"),
                max_tokens=arguments.get("max_tokens", 0),
                max_items=arguments.get("max_items", 15),
                include_sessions=arguments.get(
                    "include_sessions", False
                ),
                context=context,
            )
        )
    elif tool_name == "mnemosyne_write":
        tags = _ensure_list(arguments.get("tags_json", "[]"))
        return _run_async(
            storage.write_memory(
                arguments["kind"],
                arguments["title"],
                arguments["content"],
                tags=tags,
                pinned=arguments.get("pinned", False),
                content_compact=arguments.get("content_compact"),
                workspace_hint=arguments.get("workspace_hint"),
                importance=arguments.get("importance"),
                source=arguments.get("source"),
                context=context,
            )
        )
    elif tool_name == "mnemosyne_read":
        return _run_async(
            storage.read_memory(
                arguments["id"],
                prefer=arguments.get("prefer", "full"),
                context=context,
            )
        )
    elif tool_name == "mnemosyne_search":
        return _run_async(
            storage.search_memory(
                arguments["query"],
                arguments.get("limit", 8),
                prefer=arguments.get("prefer", "full"),
                snippet_chars=arguments.get(
                    "snippet_chars", 400
                ),
                context=context,
            )
        )
    elif tool_name == "mnemosyne_commit_session":
        decisions = _ensure_list(
            arguments.get("decisions_json", "[]")
        )
        next_steps = _ensure_list(
            arguments.get("next_steps_json", "[]")
        )
        return _run_async(
            storage.commit_session(
                arguments["workspace_hint"],
                arguments["summary"],
                decisions=decisions,
                next_steps=next_steps,
                context=context,
            )
        )
    elif tool_name == "mnemosyne_last_session":
        return _run_async(
            storage.last_session(
                arguments.get("workspace_hint", "global"),
                arguments.get("limit", 3),
                context=context,
            )
        )
    else:
        raise ValueError(f"Unknown tool: {tool_name}")


class MCPHandler(BaseHTTPRequestHandler):
    """HTTP handler for MCP JSON-RPC requests."""

    def do_POST(self):
        if self.path != "/mcp":
            self.send_response(404)
            self.end_headers()
            return

        request = {}
        try:
            content_length = int(self.headers["Content-Length"])
            body = self.rfile.read(content_length)
            request = json.loads(body)
            method = request.get("method")
            params = request.get("params", {})

            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "mnemosyne", "version": "2.0.0"},
                }
            elif method in ("notifications/initialized", "initialized"):
                result = {}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                tool_name = params.get("name")
                arguments = params.get("arguments", {})
                # Construct request context from headers (optional; dev-friendly)
                user_id = self.headers.get("X-User-Id")
                space_id = self.headers.get("X-Space-Id")
                allowed_spaces: list[str] | None = None
                if space_id:
                    allowed_spaces = [space_id]
                elif user_id:
                    allowed_spaces = [f"personal:{user_id}"]
                context = {
                    "user_id": user_id,
                    "space_id": space_id,
                    "allowed_spaces": allowed_spaces,
                }

                tool_result = handle_tool_call(tool_name, arguments, context)
                result = {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(tool_result, ensure_ascii=False),
                        }
                    ]
                }
            else:
                result = {"error": f"Unknown method: {method}"}

            response = {
                "jsonrpc": "2.0",
                "id": request.get("id"),
                "result": result,
            }
            self._send_json(200, response)

        except Exception as e:
            logger.exception("Error handling request")
            error_response = {
                "jsonrpc": "2.0",
                "id": request.get("id", 1),
                "error": {"code": -32603, "message": str(e)},
            }
            self._send_json(500, error_response)

    def _send_json(self, status: int, data: dict):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def log_message(self, format, *args):
        logger.info("%s - %s", self.address_string(), format % args)


if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    storage = _create_storage()
    _run_async(storage.initialize())

    logger.info(
        "Mnemosyne MCP server starting on %s:%d (neo4j)",
        BIND,
        PORT,
    )
    server = HTTPServer((BIND, PORT), MCPHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        _run_async(storage.close())
        loop.close()
