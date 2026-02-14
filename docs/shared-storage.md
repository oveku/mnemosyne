# Mnemosyne Multi‑Tenant + Shared Storage Design

Status: Draft
Target branch: design/shared-storage

## Goals
- Isolate each user’s private memories from others (strong tenant isolation)
- Enable "shared spaces" where multiple users can read/write shared memories
- Preserve current features (dedup by kind+title, tags, bootstrap, search, sessions)
- Keep Neo4j as the default backend; allow future alternative backends
- Be deployable locally and in cloud (Azure preferred but not required)

## Non‑Goals (for initial phase)
- Cross‑backend migrations (Neo4j → other) in this phase
- Fine‑grained per‑edge ACLs beyond space scoping

## Tenancy Model Overview
We introduce the concept of a Space:
- Personal Space: one per user (private by default)
- Shared Space: a collaborative space with explicit membership (multiple users)

Every memory and its edges live in exactly one Space. Users can be members of multiple Spaces (their personal + any shared ones). All reads/writes are scoped to one or more allowed Spaces.

## Data Model (Neo4j)
Labels and key properties:
- (User {id})
- (Space {id, type: 'personal'|'shared', name?, created_at})
- (MemoryItem {id, kind, title, content, pinned, created_at, updated_at, space_id})
- (Tag {name})
- (Session {id, workspace_hint, summary, decisions, next_steps, created_at, space_id})

Relationships:
- (User)-[:MEMBER_OF]->(Space)
- (Space)-[:CONTAINS]->(MemoryItem)
- (MemoryItem)-[:TAGGED_WITH]->(Tag)
- (Session)-[:IN_SPACE]->(Space)
- (Session)-[:FOLLOWS]->(Session)

Notes:
- We retain `Workspace` concept for session grouping, but sessions are now also scoped to a `space_id`.
- We keep `dedup` semantics by enforcing uniqueness per Space for `(kind, title)`.

## Cypher Patterns
Create or update memory (dedup by kind+title in a space):
```
MERGE (s:Space {id: $space_id})
MERGE (m:MemoryItem {space_id: $space_id, kind: $kind, title: $title})
ON CREATE SET m.content=$content, m.created_at=$now, m.updated_at=$now, m.pinned=$pinned
ON MATCH  SET m.content=$content, m.updated_at=$now, m.pinned=$pinned
WITH m
MATCH (s:Space {id: $space_id})
MERGE (s)-[:CONTAINS]->(m)
```

Search (fulltext then filter by allowed spaces):
```
CALL db.index.fulltext.queryNodes('memory_fulltext', $q)
YIELD node, score
WHERE node.space_id IN $allowed_spaces
OPTIONAL MATCH (node)-[:TAGGED_WITH]->(t:Tag)
RETURN ... ORDER BY score DESC LIMIT $lim
```

Bootstrap within spaces:
```
MATCH (m:MemoryItem {pinned: true})
WHERE m.space_id IN $allowed_spaces
...
```

## Constraints and Indexes
- Uniqueness per space: `CREATE INDEX memory_item_unique IF NOT EXISTS FOR (m:MemoryItem) ON (m.space_id, m.kind, m.title)`
- Updated ordering: `CREATE INDEX memory_item_updated IF NOT EXISTS FOR (m:MemoryItem) ON (m.updated_at)` (exists)
- Pinned: `CREATE INDEX memory_item_pinned IF NOT EXISTS FOR (m:MemoryItem) ON (m.pinned)` (exists)
- Fulltext: reuse existing `memory_fulltext` and filter on `space_id` in queries
- Space: `CREATE CONSTRAINT space_id_unique IF NOT EXISTS FOR (s:Space) REQUIRE s.id IS UNIQUE`
- Tag: already unique by `name`

## API Changes (proposed)
Add a lightweight request context with identity:
- `user_id`: caller’s stable ID (e.g., AAD `oid`, email hash, or UUID)
- `space_id` (optional): target space for writes; if omitted, default to user’s personal space
- `allowed_spaces`: derived from membership for reads (personal + shared)

Minimal surface changes:
- `write_memory(..., context: RequestContext)`
- `search_memory(query, limit, context)`
- `bootstrap(limit_pinned, limit_recent, context)`
- `commit_session(workspace_hint, summary, ..., context)`
- `last_session(workspace_hint, limit, context)`

MCP server additions:
- Accept `X-User-Id` header (or `Authorization` bearer) → resolve to `user_id`
- Optional `X-Space-Id` header for targeting a shared space on writes
- Compute `allowed_spaces` from graph: `(User {id})-[:MEMBER_OF]->(Space)`

VS Code Extension additions:
- New settings: `mnemosyne.userId`, `mnemosyne.spaceId` (optional), later: token-based auth
- Send headers with each request; avoid storing secrets in settings by default

## Security Considerations
- All queries must include `space_id` scoping; enforce centrally in storage layer
- Deny cross-space relationships except via explicit copy/link flows
- Prefer server-side auth (bearer token → `user_id`) for cloud; header fallback for local/dev
- Secrets in production via environment or Key Vault; never commit credentials

## Azure Architecture (Option Set)
1) Managed Neo4j (Neo4j AuraDB on Azure)
- Pros: fully managed, backups, metrics, private endpoints
- Deploy MCP server on Azure Container Apps or App Service
- Use Managed Identity + Key Vault for secrets

2) Self-managed Neo4j on AKS
- Pros: full control; Cons: ops overhead
- MCP server in same VNet; private IP connectivity

3) Alternative Backend (future): Azure Cosmos DB (Gremlin API)
- Requires a new `CosmosGremlinStorage` implementing `MemoryStorage`
- Similar `space_id` scoping pattern; plan as a follow-up RFC

Networking and Identity:
- Azure AD (Entra ID) for OIDC/JWT → map `oid` to `user_id`
- Private endpoints for Neo4j; deny public access in production
- App Insights for observability (no PII in logs)

## Migration Plan
- Create `Space` for existing deployment: `space_id = 'personal:<admin-or-local>'`
- Backfill existing `MemoryItem`/`Session` with `space_id`
- Add constraints and indexes
- Ship feature-flag to toggle multi-tenancy until fully migrated

## Testing Plan
- Unit: storage scoping (no leakage across spaces), dedup per space
- Integration: MCP end-to-end with `X-User-Id` and `X-Space-Id`
- Security: attempt cross-space reads/writes → expect deny/empty

## Open Source Considerations
- Default to local/dev-safe config; document `docker-compose` setup
- No telemetry by default; clear contribution guidelines for schema changes
- Clear instructions for running with/without Azure

## Next Steps
1) Update `MemoryStorage` interface with `RequestContext`
2) Implement scoping in `Neo4jStorage` queries
3) Add headers handling in MCP server; plumb to storage
4) Extension: config for `userId`/`spaceId`; send headers
5) Write migration script and tests
