# Mnemosyne — Installation Guide

A step-by-step guide to get Mnemosyne running from scratch. No prior experience with Neo4j or MCP required.

---

## What You're Installing

Mnemosyne is a memory layer for AI coding agents. It gives your AI assistant (GitHub Copilot, Claude, etc.) persistent memory across sessions. It consists of two containers managed by Docker Compose:

| Container | What it does |
|-----------|-------------|
| **Neo4j** | Graph database that stores your memories, tags, sessions |
| **Mnemosyne MCP** | Python server that exposes memory tools via the MCP protocol |

You do **not** need to install Neo4j separately — Docker Compose handles everything.

---

## Prerequisites

You need exactly **two things** installed:

### 1. Git

- **Windows**: Download from [git-scm.com](https://git-scm.com/download/win) or run `winget install Git.Git`
- **macOS**: Run `xcode-select --install` or `brew install git`
- **Linux**: `sudo apt install git` (Debian/Ubuntu) or `sudo dnf install git` (Fedora)

### 2. Docker Desktop

- **Windows / macOS**: Download from [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/)
- **Linux**: Install Docker Engine + Docker Compose plugin:
  ```bash
  # Ubuntu/Debian
  sudo apt install docker.io docker-compose-v2
  sudo usermod -aG docker $USER   # then log out and back in
  ```

Verify both are working:

```bash
git --version        # should print something like "git version 2.x"
docker --version     # should print "Docker version 2x.x"
docker compose version  # should print "Docker Compose version v2.x"
```

---

## Installation

### Step 1: Clone the repository

```bash
git clone https://github.com/oveku/mnemosyne.git
cd mnemosyne
```

### Step 2: Start the services

```bash
cd server
docker compose up -d
```

That's it. This will:

1. Pull the Neo4j 5 Community Edition image (~500 MB first time)
2. Build the Mnemosyne MCP server image (~200 MB first time)
3. Start Neo4j, wait for it to be healthy
4. Start the MCP server connected to Neo4j

First startup takes 1–3 minutes (image download + Neo4j initialization). Subsequent starts take about 10 seconds.

### Step 3: Verify it's running

```bash
docker compose ps
```

You should see two containers with status `Up` / `healthy`:

```
NAME              STATUS
mnemosyne-neo4j   Up (healthy)
mnemosyne-mcp     Up
```

Test the MCP server with a quick ping:

```bash
curl -X POST http://localhost:8010/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"ping"}'
```

On Windows PowerShell:

```powershell
Invoke-RestMethod -Uri "http://localhost:8010/mcp" -Method Post `
  -ContentType "application/json" `
  -Body '{"jsonrpc":"2.0","id":1,"method":"ping"}'
```

You should get a JSON response back — that means Mnemosyne is running.

### Step 4: Connect VS Code

Add Mnemosyne to your **user-level** VS Code MCP configuration:

| OS | File location |
|----|---------------|
| Windows | `%APPDATA%\Code\User\mcp.json` |
| macOS | `~/Library/Application Support/Code/User/mcp.json` |
| Linux | `~/.config/Code/User/mcp.json` |

Create or edit that file:

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

> **Running on a different machine?** Replace `localhost` with the IP or hostname of the machine running Docker (e.g. `http://192.168.1.100:8010/mcp`).

Restart VS Code. Your AI assistant now has access to the five Mnemosyne memory tools.

---

## Done!

At this point Mnemosyne is fully operational. Your AI coding agent can now:

- **Bootstrap** — Load context at the start of each session
- **Write** — Store decisions, patterns, commands, and notes
- **Search** — Full-text search across all memories
- **Commit Session** — Save what happened and what comes next
- **Last Session** — Recall the previous session for any workspace

To teach your AI agent how to use memory, add this to your project's `.github/copilot-instructions.md` — see the one in this repository for an example.

---

## Optional: VS Code Extension

The extension auto-bootstraps on startup and auto-commits on close. It's optional — the MCP tools work without it.

```bash
cd extension
npm install
npm run compile
npx @vscode/vsce package --no-dependencies
code --install-extension mnemosyne-vscode-1.0.1.vsix
```

> Requires [Node.js](https://nodejs.org/) 18+ and npm.

After installing, open VS Code Settings and set `mnemosyne.serverUrl` to `http://localhost:8010/mcp` (or your server's address).

---

## Optional: Custom Configuration

The defaults work out of the box. If you want to change the Neo4j password, port, or other settings, create a `.env` file in the `server/` directory:

```bash
cp .env.example server/.env
```

Then edit `server/.env`:

```ini
# Change the Neo4j password (both containers will use this)
NEO4J_PASSWORD=your-secure-password

# Change the MCP server port
MNEMOSYNE_PORT=9090
```

After changing, restart the services:

```bash
cd server
docker compose down
docker compose up -d
```

Full list of settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `NEO4J_USER` | `neo4j` | Neo4j username |
| `NEO4J_PASSWORD` | `mnemosyne` | Neo4j password |
| `NEO4J_DATABASE` | `neo4j` | Neo4j database name |
| `MNEMOSYNE_PORT` | `8010` | MCP server port |

---

## Optional: Neo4j Browser

Neo4j comes with a built-in web UI for browsing your knowledge graph. Open in your browser:

```
http://localhost:7474
```

Login with `neo4j` / `mnemosyne` (or whatever you set in `.env`). You can run Cypher queries like:

```cypher
// See all memories
MATCH (m:MemoryItem) RETURN m ORDER BY m.updated_at DESC LIMIT 20

// See all tags
MATCH (m:MemoryItem)-[:TAGGED_WITH]->(t:Tag) RETURN m.title, collect(t.name) AS tags

// See session history for a workspace
MATCH (s:Session {workspace_hint: 'my-project'}) RETURN s ORDER BY s.created_at DESC
```

---

## Stopping and Starting

```bash
cd server

# Stop (keeps data)
docker compose down

# Start again
docker compose up -d

# View logs
docker compose logs -f

# View MCP server logs only
docker logs -f mnemosyne-mcp
```

Your data is persisted in `server/data/` and survives restarts.

---

## Uninstalling

```bash
cd server

# Stop containers and remove volumes
docker compose down

# Remove stored data (IRREVERSIBLE)
rm -rf data/

# Remove cloned repo
cd ../..
rm -rf mnemosyne/
```

---

## Deploying to a Remote Server

If you want Mnemosyne on a server (Raspberry Pi, cloud VM, NAS, etc.) instead of your local machine:

1. Copy the `server/` directory to the remote host
2. Create a `.env` file with your desired password
3. Run `docker compose up -d` on the remote host
4. Point your VS Code MCP config to `http://remote-ip:8010/mcp`

A PowerShell deployment script is included for convenience:

```powershell
cd deploy
.\deploy.ps1 -SshHost user@your-server -RemoteDir /opt/mnemosyne
```

---

## Troubleshooting

### "Cannot connect to the Docker daemon"

Docker Desktop isn't running. Start it from your applications menu (Windows/macOS) or run `sudo systemctl start docker` (Linux).

### "Port 7474 / 7687 / 8010 already in use"

Another service is using those ports. Either stop it or change Mnemosyne's ports in `.env`:

```ini
MNEMOSYNE_PORT=9090
```

For Neo4j ports, edit `docker-compose.yml` directly.

### "MCP server returns connection refused"

Neo4j might still be starting up (it takes ~30 seconds). Wait and try again. Check logs:

```bash
docker compose logs neo4j
docker compose logs mnemosyne-mcp
```

### "curl/Invoke-RestMethod not found" (Windows)

Use PowerShell (not Command Prompt). Or install curl via `winget install cURL.cURL`.

### Container restarts in a loop

Check the logs for the failing container:

```bash
docker logs mnemosyne-mcp
docker logs mnemosyne-neo4j
```

Common cause: insufficient memory. Neo4j needs ~512 MB RAM. If running on a low-memory device, reduce heap sizes in `docker-compose.yml`.

### Reset everything and start fresh

```bash
cd server
docker compose down
rm -rf data/
docker compose up -d
```
