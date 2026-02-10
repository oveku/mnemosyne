#!/usr/bin/env python3
"""Quick health check for Mnemosyne MCP server."""
import json
import os
import urllib.request

url = os.environ.get("MNEMOSYNE_URL", "http://localhost:8010/mcp")

# Test 1: Ping
payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"}).encode()
req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
resp = urllib.request.urlopen(req)
print(f"Ping: {resp.read().decode()}")

# Test 2: Bootstrap
payload = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "mnemosyne_bootstrap", "arguments": {"limit_pinned": 2, "limit_recent": 2}}}).encode()
req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
resp = urllib.request.urlopen(req)
result = json.loads(resp.read().decode())
content = json.loads(result["result"]["content"][0]["text"])
print(f"Bootstrap: pinned={len(content.get('pinned', []))}, recent={len(content.get('recent', []))}")
print("ALL OK")
