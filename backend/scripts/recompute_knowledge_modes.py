"""Recompute ``campaigns.knowledge_mode`` from each campaign's ready sources.

WHY THIS EXISTS

``knowledge_mode`` is decided once, at upload time, by ``budget.choose_mode``
against the campaign's model. Two defects meant that decision was made with the
wrong inputs:

* the upload endpoint read only ``campaigns.knowledge_model``, an optional
  override that is null for every campaign nothing explicitly set it on, so the
  budget usually received no model at all; and
* ``budget.MODEL_CONTEXT_WINDOWS`` listed only models the product had already
  retired, so both live models fell through to the 8192-token default.

The result was an inline budget of ~2.6k tokens (~10 KB of text). A knowledge
base larger than that was pushed out of ``inline`` into ``map_retrieve`` or
``retrieve``, where the agent only surfaces an answer when the caller's wording
matches the search. That reads, on the phone, as an agent that does not know
what was uploaded.

Fixing the code does not move existing rows, because the mode is stored, not
derived. This script re-derives it. It re-reads the same sums the ingest path
uses and calls the same ``choose_mode``, so it can never disagree with a fresh
upload.

SAFETY

* ``--dry-run`` (the default) writes nothing and prints the plan.
* Only ``campaigns.knowledge_mode`` is written. Nodes, sources and every other
  column are untouched.
* A campaign with no ready sources is left alone rather than forced to 'none',
  so a campaign mid-upload is never stamped by a race.
* Every statement carries an explicit tenant predicate.
* This is a cross-tenant maintenance task, so each connection sets
  ``app.bypass_rls`` explicitly rather than relying on the app role happening
  to hold BYPASSRLS. Without it the canonical tenant policy (migration 0038)
  matches nothing, and the script would report success having read zero rows.

Usage (on the server, with backend/.env loaded):

    venv/bin/python scripts/recompute_knowledge_modes.py                # plan
    venv/bin/python scripts/recompute_knowledge_modes.py --apply        # write
    venv/bin/python scripts/recompute_knowledge_modes.py --apply \
        --tenant-id <uuid>                                              # one tenant
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import NamedTuple, Optional

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


_PLAN_SQL = """
SELECT c.id::text        AS campaign_id,
       c.tenant_id::text AS tenant_id,
       c.name            AS campaign_name,
       c.knowledge_mode  AS current_mode,
       COALESCE(c.knowledge_model, a.llm_model) AS model,
       COALESCE(s.ready_tokens, 0)  AS ready_tokens,
       COALESCE(s.ready_sources, 0) AS ready_sources
FROM campaigns c
LEFT JOIN tenant_ai_configs a ON a.tenant_id = c.tenant_id
LEFT JOIN (
    SELECT campaign_id,
           tenant_id,
           SUM(token_count) AS ready_tokens,
           COUNT(*)         AS ready_sources
    FROM campaign_knowledge_sources
    WHERE status = 'ready'
    GROUP BY campaign_id, tenant_id
) s ON s.campaign_id = c.id AND s.tenant_id = c.tenant_id
WHERE ($1::uuid IS NULL OR c.tenant_id = $1::uuid)
ORDER BY c.tenant_id, c.created_at
"""


class PlannedChange(NamedTuple):
    campaign_id: str
    tenant_id: str
    campaign_name: str
    current_mode: str
    wanted_mode: str
    ready_tokens: int


def plan_changes(rows) -> tuple[list[PlannedChange], int]:
    """Decide which campaigns need a new mode. Pure, so it is testable.

    Returns (changes, skipped_no_ready_source). A campaign with no ready source
    is SKIPPED rather than forced to 'none': one mid-upload must never be
    stamped by a race with its own ingest transaction.
    """
    from app.services.scripts.knowledge.budget import choose_mode

    changes: list[PlannedChange] = []
    skipped = 0
    for row in rows:
        if int(row["ready_sources"] or 0) == 0:
            skipped += 1
            continue
        wanted = choose_mode(int(row["ready_tokens"] or 0), row["model"])
        current = (row["current_mode"] or "none").strip().lower()
        if wanted != current:
            changes.append(
                PlannedChange(
                    campaign_id=row["campaign_id"],
                    tenant_id=row["tenant_id"],
                    campaign_name=str(row["campaign_name"] or "")[:28],
                    current_mode=current,
                    wanted_mode=wanted,
                    ready_tokens=int(row["ready_tokens"] or 0),
                )
            )
    return changes, skipped


async def _run(args: argparse.Namespace) -> int:
    import asyncpg

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set (load backend/.env first)")

    from app.services.scripts.knowledge.budget import (
        context_window_for,
        inline_budget_for,
    )

    async def _bypass_rls(conn) -> None:
        # The canonical policy is
        #   COALESCE(NULLIF(current_setting('app.bypass_rls', TRUE),'')::boolean, FALSE)
        #     OR tenant_id = NULLIF(current_setting('app.current_tenant_id', TRUE),'')::uuid
        # With neither GUC set, a role without BYPASSRLS sees no rows at all --
        # and an empty read here is indistinguishable from "nothing to do".
        #
        # setup=, not init=: asyncpg issues RESET ALL when a connection is
        # released, so an init= SET is gone after the first release. The plan
        # read below and the --apply writes are separate acquires, so under
        # init= the writes ran with RLS in force (found 2026-09-23; this
        # script had only ever been run in plan mode, and it counts writes
        # from the database's own status, so it could not have reported a
        # false success).
        await conn.execute("SET app.bypass_rls = 'true'")

    pool = await asyncpg.create_pool(
        dsn, min_size=1, max_size=2, setup=_bypass_rls
    )
    try:
        rows = await pool.fetch(_PLAN_SQL, args.tenant_id)
        if not rows:
            # Distinguish "no campaigns" from "RLS hid everything".
            total = await pool.fetchval("SELECT COUNT(*) FROM campaigns")
            print(
                "no campaigns visible; campaigns table reports "
                f"{total} row(s). If that count is 0 but you expect data, the "
                "bypass GUC did not take effect and this run proves nothing."
            )
            return 0
        changes, skipped_no_sources = plan_changes(rows)

        print(f"campaigns inspected      : {len(rows)}")
        print(f"skipped (no ready source): {skipped_no_sources}")
        print(f"would change             : {len(changes)}")
        if rows:
            sample_model = rows[0]["model"]
            print(
                f"budget check             : model={sample_model or '(none)'} "
                f"window={context_window_for(sample_model)} "
                f"inline_budget={inline_budget_for(sample_model)} tokens"
            )
        print()
        for change in changes:
            print(
                f"  {change.campaign_id[:8]}  {change.tenant_id[:8]}  "
                f"{change.campaign_name:<28} "
                f"{change.current_mode:>12} -> {change.wanted_mode:<12} "
                f"tokens={change.ready_tokens}"
            )

        if not args.apply:
            print()
            print("DRY RUN — nothing written. Re-run with --apply to write.")
            return 0
        if not changes:
            print()
            print("nothing to change")
            return 0

        written = 0
        async with pool.acquire() as conn:
            async with conn.transaction():
                for change in changes:
                    result = await conn.execute(
                        "UPDATE campaigns SET knowledge_mode = $3, updated_at = NOW() "
                        "WHERE id = $1::uuid AND tenant_id = $2::uuid",
                        change.campaign_id,
                        change.tenant_id,
                        change.wanted_mode,
                    )
                    if result.endswith("1"):
                        written += 1
        print()
        print(f"APPLIED — {written} campaign(s) updated")
        return 0
    finally:
        await pool.close()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the changes; without it the script only prints the plan",
    )
    parser.add_argument(
        "--tenant-id",
        default=None,
        help="restrict to one tenant; omit to cover every campaign",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
