---
description: Run a READ-ONLY SQL query against the production Postgres on the Hetzner server.
argument-hint: "<SQL SELECT query>"
allowed-tools: Bash
---
Run a READ-ONLY query against production Postgres. Server access is in the
`server-ssh-access` memory; `DATABASE_URL` is in `/opt/talky/backend/.env`.

SAFETY: refuse anything that is not a single `SELECT` (no INSERT/UPDATE/DELETE/
DROP/ALTER/TRUNCATE/GRANT). If $ARGUMENTS contains a write, stop and ask.

Run it via the venv python + asyncpg (strip `+asyncpg`/`+psycopg` from the URL):
read `DATABASE_URL` from the env file, connect, `fetch` the query, print rows.

Key tables:
- `tenant_ai_configs` — per-tenant `llm_provider`, `llm_model`, `llm_temperature`,
  `llm_max_tokens` (the AI Options settings that drive live calls).
- `campaigns` — `id`, `tenant_id`, `script_config` (jsonb: persona_type,
  company_name, agent_names, campaign_slots, additional_instructions, knowledge_driven).
  Dojo = `b6a61ac6…`.
- `calls` — `transcript` (text), `transcript_json` (jsonb), timing/status.

Query to run: $ARGUMENTS
