# Mnemosyne VS Code Extension

Auto-bootstrap memory context on VS Code startup and auto-commit sessions on close.

## Commands

| Command | Description |
|---------|-------------|
| `Mnemosyne: Show Bootstrap Context` | Display current pinned and recent memory items |
| `Mnemosyne: Set Session Summary` | Write a session summary for later commit |
| `Mnemosyne: Commit Session Now` | Immediately commit the current session to Mnemosyne |

## Settings

| Setting | Default | Description |
|---------|---------|-------------|
| `mnemosyne.serverUrl` | `""` | Mnemosyne MCP server endpoint (e.g. `http://your-server:8010/mcp`) |
| `mnemosyne.autoBootstrap` | `true` | Auto-load memory context on startup |
| `mnemosyne.autoCommitOnClose` | `true` | Auto-commit session when VS Code closes |
| `mnemosyne.workspaceHint` | `""` | Override workspace identifier (defaults to folder name) |

## Installation

```bash
cd extension
npm install
npm run package
code --install-extension mnemosyne-vscode-1.0.1.vsix
```
