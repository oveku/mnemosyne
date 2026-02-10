# Mnemosyne — Architecture & Roadmap

## Project Identity

**Mnemosyne** (Μνημοσύνη) — Titaness of Memory, mother of the Muses.
She is the persistent memory layer for AI coding agents. She remembers what happened,
what was decided, and what comes next — across sessions, workspaces, and projects.

## Architecture

```
┌──────────────┐       HTTP       ┌──────────────┐       Bolt       ┌──────────┐
│  VS Code     │ ───────────────> │  MCP Server  │ ───────────────> │  Neo4j   │
│  Extension   │     :8010/mcp    │  (Python)    │      :7687       │  (Graph) │
└──────────────┘                  └──────────────┘                  └──────────┘
       │                                │
       │ stdio (alternative)            │
       └──> mnemosyne_proxy.py ─────────┘
```

### Components

1. **MCP Server** (`server/app/server.py`) — Python HTTP server implementing MCP JSON-RPC
   - 5 tools: bootstrap, write, search, commit_session, last_session
   - Neo4j knowledge graph storage backend
   - Configurable via environment variables (see `.env.example`)

2. **Neo4j Storage** (`server/app/storage/neo4j_storage.py`) — Knowledge graph backend
   - Memory items with fulltext search indexes
   - Tag nodes and workspace nodes with typed relationships
   - Session chaining via FOLLOWS relationships
   - Deduplication by kind + title via MERGE

3. **VS Code Extension** (`extension/`) — TypeScript extension
   - Auto-bootstrap on VS Code startup (loads memory context)
   - Auto-commit on VS Code close (saves session state)
   - Command palette commands for manual control

4. **Stdio Proxy** (`server/mnemosyne_proxy.py`) — MCP transport bridge
   - Bridges VS Code's stdio MCP client to the remote HTTP server
   - Uses the official MCP Python library

5. **Deployment** (`deploy/`) — Docker Compose + scripts
   - Neo4j 5 Community container
   - Mnemosyne MCP server container
   - PowerShell deployment and backup scripts

## Graph Schema

```
(:MemoryItem {kind, title, content, pinned, created_at, updated_at})
  -[:TAGGED_WITH]-> (:Tag {name})
  -[:DECIDED_IN]-> (:Session)
  -[:RELATES_TO]-> (:MemoryItem)

(:Session {workspace_hint, summary, decisions, next_steps, created_at})
  -[:FOLLOWS]-> (:Session)
  -[:IN_WORKSPACE]-> (:Workspace {name})
```

### Why Neo4j?

A knowledge graph enables:

- **Relationships** — Decisions link to the sessions they were made in,
  the files they affect, and the patterns they follow
- **Traversal** — "Show me all decisions about Docker deployment" traverses
  relationships rather than keyword-matching
- **Context Windows** — Bootstrap can follow relationship paths to load
  the most relevant context, not just recent/pinned items
- **Cross-Project Memory** — Link memories across workspaces via shared
  concepts, tools, servers, and patterns

## MCP Configuration

User-level VS Code MCP config:

**Windows:** `%APPDATA%\Code\User\mcp.json`
**Linux/macOS:** `~/.config/Code/User/mcp.json`

```json
{
  "servers": {
    "mnemosyne": {
      "type": "http",
      "url": "http://your-server:8010/mcp"
    }
  }
}
```

## Roadmap

- [x] Phase 1: Standalone repo with abstract storage interface
- [x] Phase 2: Neo4j knowledge graph backend
- [x] Phase 3: Remove legacy storage, go public-ready
- [ ] Phase 4: Enhanced graph queries (relationship traversal, semantic search)
- [ ] Phase 5: Multi-agent memory sharing with access control
- [ ] Phase 6: Memory compaction and summarization
