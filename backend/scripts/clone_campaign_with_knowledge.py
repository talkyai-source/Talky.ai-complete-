"""Clone a campaign and re-publish its knowledge document onto the copy.

WHY CLONE RATHER THAN BUILD FROM SCRATCH

A campaign that a voice agent can actually run needs more than the two columns
the schema insists on. ``campaigns.voice_id`` defaults to the literal string
``'default'``, which is not a voice and has broken live calls before, and a
usable agent needs a real system prompt. Copying an existing campaign the
business already ran carries all of that across intact, so the copy differs only
in the fields this script overrides.

Every column is copied by name from ``information_schema``, so a column added
later is carried too and this does not silently drop settings.

KNOWLEDGE

Knowledge belongs to ONE campaign and is never shared, so the clone starts with
none. The original markdown is still held in ``campaign_knowledge_sources.raw_md``,
so it is re-published onto the copy through ``ingest_markdown`` -- the same entry
point the upload endpoint uses. That re-parses, re-enriches and re-indexes it,
and writes ``campaigns.knowledge_mode`` from the model-aware budget, so the copy
is indistinguishable from a fresh upload.

Usage (on the server, with backend/.env loaded):

    venv/bin/python scripts/clone_campaign_with_knowledge.py \\
        --tenant-id <uuid> --source-campaign-id <uuid> \\
        --new-name "Estimation (knowledge test)" [--status draft] \\
        [--dry-run]
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

# Never copied: identity, lifecycle timestamps, and the fields this script sets.
_NEVER_COPY = {
    "id",
    "name",
    "status",
    "created_at",
    "updated_at",
    "knowledge_mode",   # re-derived by the ingest, not inherited
}


async def _run(args: argparse.Namespace) -> int:
    import asyncpg

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set (load backend/.env first)")

    from app.core.db import _register_jsonb_codecs

    async def _init(conn) -> None:
        # EVERY connection, not just the ones this script remembers to set it
        # on. The tenant-model lookup below ran on a fresh pooled connection
        # with no GUC and was silently hidden by the row-level policy, so the
        # ingest budgeted against no model at all and said so (2026-09-22).
        await _register_jsonb_codecs(conn)
        await conn.execute("SET app.bypass_rls = 'true'")

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3, init=_init)
    try:
        async with pool.acquire() as conn:
            source = await conn.fetchrow(
                "SELECT id, name, status, direction, voice_id, knowledge_mode, "
                "       COALESCE(length(system_prompt), 0) AS prompt_chars "
                "FROM campaigns WHERE id = $1::uuid AND tenant_id = $2::uuid",
                args.source_campaign_id,
                args.tenant_id,
            )
            if source is None:
                raise SystemExit(
                    "source campaign not found on that tenant: "
                    f"{args.source_campaign_id}"
                )
            print("source campaign:")
            print(f"  name           {source['name']}")
            print(f"  status         {source['status']}")
            print(f"  direction      {source['direction']}")
            print(f"  voice_id       {source['voice_id']}")
            print(f"  prompt_chars   {source['prompt_chars']}")
            if not source["prompt_chars"]:
                print("  !! the source has an EMPTY system prompt; the copy will too")
            if str(source["voice_id"] or "") in ("", "default"):
                raise SystemExit(
                    "source voice_id is 'default', which is not a real voice and "
                    "has broken live calls before. Pick a different source."
                )

            doc = await conn.fetchrow(
                "SELECT id, filename, raw_md, token_count "
                "FROM campaign_knowledge_sources "
                "WHERE campaign_id = $1::uuid AND tenant_id = $2::uuid "
                "  AND status = 'ready' "
                "ORDER BY created_at DESC LIMIT 1",
                args.source_campaign_id,
                args.tenant_id,
            )
            if doc is None:
                print("  (no published knowledge document on the source)")
            else:
                print(
                    f"  knowledge      {doc['filename']} "
                    f"({len(doc['raw_md'])} chars, {doc['token_count']} tokens)"
                )

            columns = [
                r["column_name"]
                for r in await conn.fetch(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='campaigns' "
                    "ORDER BY ordinal_position"
                )
                if r["column_name"] not in _NEVER_COPY
            ]
            if not columns:
                raise SystemExit("could not read the campaigns column list")
            col_sql = ", ".join(f'"{c}"' for c in columns)

            if args.dry_run:
                print()
                print(f"DRY RUN — would copy {len(columns)} column(s) into a new")
                print(f"campaign named {args.new_name!r} with status={args.status!r},")
                print("then re-publish the knowledge document onto it.")
                return 0

            new_id = await conn.fetchval(
                f"""
                INSERT INTO campaigns (name, status, {col_sql})
                SELECT $3, $4, {col_sql}
                FROM campaigns WHERE id = $1::uuid AND tenant_id = $2::uuid
                RETURNING id
                """,
                args.source_campaign_id,
                args.tenant_id,
                args.new_name,
                args.status,
            )
            if new_id is None:
                raise SystemExit("insert returned no id")
            print()
            print(f"created campaign {new_id}  name={args.new_name!r} status={args.status}")

        if doc is not None:
            from app.services.scripts.knowledge.ingest_service import ingest_markdown

            model = await pool.fetchval(
                "SELECT llm_model FROM tenant_ai_configs WHERE tenant_id = $1::uuid",
                args.tenant_id,
            )
            if not model:
                print(
                    "  !! no llm_model on this tenant's AI config — the budget "
                    "will fall back to the conservative default window"
                )
            print(f"publishing knowledge (budget model: {model or '(none)'}) …")
            result = await ingest_markdown(
                pool,
                campaign_id=str(new_id),
                tenant_id=args.tenant_id,
                raw_md=doc["raw_md"],
                filename=doc["filename"],
                model=model,
            )
            print(
                f"  published: {result.get('node_count')} node(s), "
                f"{result.get('token_count')} tokens, mode={result.get('mode')}"
            )

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT name, status, direction, voice_id, knowledge_mode, "
                "  (SELECT count(*) FROM campaign_knowledge_nodes n "
                "     WHERE n.campaign_id = c.id) AS nodes "
                "FROM campaigns c WHERE c.id = $1::uuid",
                new_id,
            )
            print()
            print("read-back:")
            for key in ("name", "status", "direction", "voice_id", "knowledge_mode", "nodes"):
                print(f"  {key:<16}{row[key]}")
            if str(row["knowledge_mode"]) == "none" or not row["nodes"]:
                print("  !! the copy has NO usable knowledge — investigate before testing")
        return 0
    finally:
        await pool.close()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--source-campaign-id", required=True)
    parser.add_argument("--new-name", required=True)
    parser.add_argument("--status", default="draft")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
