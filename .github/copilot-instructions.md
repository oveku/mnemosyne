---
description: "Mnemosyne memory workflow rules for AI agents"
applyTo: "**"
---

# Mnemosyne Memory Rules

## Start-of-task bootstrap (always)
Call `mnemosyne_bootstrap` at the start of every session.
Treat returned pinned + recent items as real context.
If anything conflicts with the current repo state, prefer the repo state and note the conflict in the session summary.

## During work (when high-value info appears)
Store important information with `mnemosyne_write`:
- **decision**: Architecture choice, tradeoff, convention
- **command**: Exact command that worked (include flags, environment notes)
- **pattern**: Reusable approach or recipe
- **answer**: Conceptual explanation worth remembering
- **note**: Everything else

Use `tags_json` as a JSON array string, e.g. `["mnemosyne","neo4j","docker"]`.

## End-of-task commit (always)
Call `mnemosyne_commit_session` before finishing:
- `workspace_hint`: repo name or "global"
- `summary`: what you did + why
- `decisions_json`: list of decisions made
- `next_steps_json`: list of next steps

## Rules
- Pinned items should be rare (architecture decisions, conventions, non-negotiable rules)
- Prefer compaction over hoarding
- Always include workspace_hint in session commits
