# Mnemosyne

![Mnemosyne](docs/mnemosyne_linkedin_image.png)

**Persistent memory layer for AI coding agents**, built on the [Model Context Protocol](https://modelcontextprotocol.io) (MCP) and backed by a [Neo4j](https://neo4j.com) knowledge graph.

Mnemosyne (Μνημοσύνη) — Titaness of Memory — remembers what happened, what was decided, and what comes next across sessions, workspaces, and projects.

## Features

- **Bootstrap** — Loads pinned and recent memories at session start, with configurable modes (thin/hybrid/full) and token budgeting to keep context lean
- **Write** — Stores decisions, commands, patterns, answers, and notes (deduplicates by kind + title), with optional compact content for efficient retrieval
- **Read** — Retrieves a single memory item by ID with full or compact content on demand
- **Search** — Full-text search across all stored memories via Neo4j fulltext indexes, with snippet mode for lighter results
- **Commit Session** — Saves session summaries with decisions and next steps
- **Last Session** — Recalls what happened in the previous session for any workspace
- **Context Pollution Prevention** — Three-lever system (write-time hygiene, store-time structure, read-time shaping) keeps your AI's context window focused on high-signal information
- **Knowledge Graph** — Memories, tags, sessions, and workspaces are graph nodes with typed relationships

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

| Component | Location | Description |
|-----------|----------|-------------|
| MCP Server | `server/` | Python HTTP server implementing MCP JSON-RPC protocol |
| Neo4j Storage | `server/app/storage/` | Knowledge graph backend with fulltext search |
| VS Code Extension | `extension/` | Auto-bootstrap on startup, auto-commit on close |
| Stdio Proxy | `server/mnemosyne_proxy.py` | Bridges stdio MCP transport to HTTP server |
| Deployment | `deploy/` | Docker Compose + deployment scripts |

## Quick Start

> **First time?** See the full [Installation Guide](docs/INSTALL.md) with prerequisites and troubleshooting.

### 1. Start the server

```bash
git clone https://github.com/oveku/mnemosyne.git
cd mnemosyne/server
docker compose up -d
```

This starts Neo4j and the MCP server. No configuration needed — it works out of the box.

### 2. Connect VS Code

Add to your user-level MCP config (`%APPDATA%\Code\User\mcp.json` on Windows, `~/.config/Code/User/mcp.json` on Linux):

```json
{
  "servers": {
    "mnemosyne": {
      "type": "http",
      "url": "http://localhost:8010/mcp"
    }
  }
}
```

Restart VS Code. Your AI assistant now has persistent memory.

> Running on a different machine? Replace `localhost` with its IP address.

### 3. VS Code Extension (optional)

Auto-bootstraps memory on startup and auto-commits on close:

```bash
cd extension && npm install && npm run package
code --install-extension mnemosyne-vscode-1.0.1.vsix
```

Then set `mnemosyne.serverUrl` to `http://localhost:8010/mcp` in VS Code Settings.
```
Open Settings: Ctrl+,
In the search bar, type: mnemosyne.serverUrl (or just mnemosyne)
Find the setting Mnemosyne: Server Url
Set it to: http://localhost:8010/mcp
```

## Configuration

No configuration is needed for local use. To customize, create a `.env` file in `server/`:

```bash
cp .env.example server/.env
```

| Variable | Default | Description |
|----------|---------|-------------|
| `NEO4J_PASSWORD` | `mnemosyne` | Neo4j password |
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `MNEMOSYNE_PORT` | `8010` | MCP server port |

To test that memories are stored, open localhost:7474 (or whatever you set up if you changed it), log in, and run the following in your browser. If you have created one or more memories, it should show up.
```
MATCH (m:MemoryItem)
WITH m
ORDER BY m.updated_at DESC
LIMIT 200
OPTIONAL MATCH (m)-[r:TAGGED_WITH]->(t:Tag)
RETURN m, r, t;
```

## Graph Schema

```
(:MemoryItem {kind, title, content, content_compact, pinned, importance,
              workspace_hint, source, created_at, updated_at})
  -[:TAGGED_WITH]-> (:Tag {name})

(:Session {workspace_hint, summary, decisions, next_steps, created_at})
  -[:IN_WORKSPACE]-> (:Workspace {name})
  -[:FOLLOWS]-> (:Session)
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `mnemosyne_bootstrap` | Returns pinned + recent memory items for session context. Supports `mode` (thin/hybrid/full), `max_tokens` budget, and `workspace_hint` scoping |
| `mnemosyne_write` | Stores a memory item (deduplicates by kind + title). Accepts optional `content_compact`, `importance`, `workspace_hint`, and `source` |
| `mnemosyne_read` | Retrieves a single memory item by ID with full or compact content |
| `mnemosyne_search` | Full-text search across all memories. Returns compact snippets by default with `has_full` indicator |
| `mnemosyne_commit_session` | Commits end-of-session summary with decisions and next steps |
| `mnemosyne_last_session` | Returns the most recent sessions for a workspace |

## Context Pollution Prevention

As your memory store grows, naively loading everything into the AI's context window wastes tokens on low-signal content — stale notes, verbose logs, irrelevant decisions. Mnemosyne v1.0.1 addresses this with a three-lever system:

**Lever A — Write-time hygiene.** Each memory kind has a clear contract: decisions capture one decision with rationale, patterns describe a reusable approach, commands store a verified snippet. When content is long, the server auto-generates a compact summary (first ~200 characters at a sentence boundary) so bootstrap never needs to load the full text.

**Lever B — Store-time structure.** Memory items carry `content_compact` alongside full content, plus `importance` (0–100), `workspace_hint`, and `source` fields. This metadata powers smart ranking without requiring an LLM at read time.

**Lever C — Read-time shaping.** Bootstrap ranks items by `kind_weight × recency_decay × importance × workspace_match` and respects a configurable token budget. Three modes control verbosity:
- **thin** — compact content only (default for the VS Code extension)
- **hybrid** — full content for short commands/patterns, compact for everything else
- **full** — legacy behavior, returns everything

When an agent needs the full detail behind a compact summary, it calls `mnemosyne_read` with the item's ID — load little, recall everything.

## Testing

```bash
cd server
pip install pytest pytest-asyncio httpx neo4j

# Neo4j storage tests (requires a running Neo4j instance)
NEO4J_URI=bolt://localhost:7687 pytest tests/test_neo4j_storage.py -v

# Server integration tests (requires running Mnemosyne + Neo4j)
MNEMOSYNE_URL=http://localhost:8010/mcp pytest tests/test_server.py -v
```

## Deployment

Deploy to a remote server via SSH:

```powershell
cd deploy
.\deploy.ps1 -SshHost user@your-server -RemoteDir /opt/mnemosyne
```

See [deploy/deploy.ps1](deploy/deploy.ps1) for all options.

## Repository Structure

```
mnemosyne/
├── .env.example            # Environment variable template
├── README.md               # This file
├── PLAN.md                 # Architecture and roadmap
├── server/
│   ├── docker-compose.yml  # Neo4j + MCP server containers
│   ├── Dockerfile          # MCP server container image
│   ├── mnemosyne_proxy.py  # Stdio-to-HTTP MCP proxy
│   ├── app/
│   │   ├── server.py       # Main HTTP MCP server (6 tools)
│   │   ├── requirements.txt
│   │   └── storage/
│   │       ├── base.py           # Abstract storage interface
│   │       └── neo4j_storage.py  # Neo4j knowledge graph backend
│   └── tests/
│       ├── conftest.py
│       ├── test_neo4j_storage.py
│       ├── test_server.py
│       └── health_check.py
├── extension/              # VS Code extension
│   ├── src/extension.ts
│   ├── package.json
│   └── README.md
├── deploy/
│   ├── deploy.ps1          # SSH deployment script
│   └── backup.ps1          # Neo4j backup script
└── docs/
    ├── INSTALL.md          # Full installation guide
    ├── shared-storage.md   # Multi-tenant shared spaces design
    └── visual-identity.md  # Brand guidelines and image prompts
```

## License

MIT
