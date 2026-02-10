# Mnemosyne

![Mnemosyne](docs/mnemosyne_linkedin_image.png)

**Persistent memory layer for AI coding agents**, built on the [Model Context Protocol](https://modelcontextprotocol.io) (MCP) and backed by a [Neo4j](https://neo4j.com) knowledge graph.

Mnemosyne (Μνημοσύνη) — Titaness of Memory — remembers what happened, what was decided, and what comes next across sessions, workspaces, and projects.

## Features

- **Bootstrap** — Loads pinned and recent memories at the start of every AI session
- **Write** — Stores decisions, commands, patterns, answers, and notes (deduplicates by kind + title)
- **Search** — Full-text search across all stored memories via Neo4j fulltext indexes
- **Commit Session** — Saves session summaries with decisions and next steps
- **Last Session** — Recalls what happened in the previous session for any workspace
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
code --install-extension mnemosyne-vscode-0.1.0.vsix
```

Then set `mnemosyne.serverUrl` to `http://localhost:8010/mcp` in VS Code Settings.

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

## Graph Schema

```
(:MemoryItem {kind, title, content, pinned, created_at, updated_at})
  -[:TAGGED_WITH]-> (:Tag {name})

(:Session {workspace_hint, summary, decisions, next_steps, created_at})
  -[:IN_WORKSPACE]-> (:Workspace {name})
  -[:FOLLOWS]-> (:Session)
```

## MCP Tools

| Tool | Description |
|------|-------------|
| `mnemosyne_bootstrap` | Returns pinned + recent memory items for session context |
| `mnemosyne_write` | Stores a memory item (deduplicates by kind + title) |
| `mnemosyne_search` | Full-text search across all memories |
| `mnemosyne_commit_session` | Commits end-of-session summary with decisions and next steps |
| `mnemosyne_last_session` | Returns the most recent sessions for a workspace |

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
│   │   ├── server.py       # Main HTTP MCP server
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
    └── visual-identity.md  # Brand guidelines and image prompts
```

## License

MIT
