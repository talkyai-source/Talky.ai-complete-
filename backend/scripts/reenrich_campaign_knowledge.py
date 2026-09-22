"""Fill in the enrichment a campaign's knowledge never received.

WHY THIS EXISTS

The enricher spent a month calling ``llama-3.1-8b-instant``, which Groq removed
around 2026-08-17. Enrichment is fail-soft, so every upload in that window
published its nodes bare: no summary, no spoken answer, no keywords, no example
questions. Fixed 2026-09-22 (185f6e9c, 98f50a1f) -- but only for documents
uploaded AFTER the fix. Two live campaigns still carry stripped nodes:

    dojo            2953b876   43 nodes, 0 enriched, ingested 2026-09-15
    Estimation new  a3788fa7   27 nodes, 0 enriched, ingested 2026-09-21

WHY NOT JUST RE-UPLOAD

Re-ingesting deletes the nodes and rebuilds them. On a campaign that is running
and taking calls that opens a window in which the agent has no knowledge at
all, and ``ingest_markdown``'s own guard refuses a campaign that has calls --
correctly, because re-ingesting is destructive.

Nothing needs rebuilding. The headings and bodies are already right; only the
four enrichment columns are empty. This script fills them IN PLACE and never
deletes a row, so there is no window and the guard does not apply.

It also recomputes ``search_text`` and ``search_tsv``. That matters more than it
looks: ``_search_text`` folds keywords and example questions into the indexed
text, and ``retrieval.py`` matches a caller's phrasing against it with
``word_similarity``. An unenriched campaign is therefore not merely missing its
spoken answers -- its fuzzy matching is degraded too, and that is exactly the
rescue path for a caller who words a question oddly.

Usage (on the server, with backend/.env loaded):

    venv/bin/python scripts/reenrich_campaign_knowledge.py \
        --tenant-id <uuid> --campaign-id <uuid> [--all] [--dry-run]

``--all`` re-enriches every node; the default touches only nodes whose summary
is empty, so topping up a partially enriched document costs nothing.
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

# The enricher returns structurally invalid JSON above this size; 8 is the value
# the 2026-09-22 fix settled on after 25 produced unparseable arrays.
_BATCH = 8


async def _run(args: argparse.Namespace) -> int:
    import asyncpg

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set (load backend/.env first)")

    from app.core.db import _register_jsonb_codecs
    from app.services.scripts.knowledge.enricher import enrich_nodes
    from app.services.scripts.knowledge.ingest_service import _search_text
    from app.services.scripts.knowledge.md_tree import ParsedNode

    async def _init(conn) -> None:
        # Codecs are a property of the connection and survive a reset.
        await _register_jsonb_codecs(conn)

    async def _setup(conn) -> None:
        # The GUC must be re-applied on EVERY acquire, not once per connection.
        # asyncpg issues RESET ALL when a connection is released to the pool, so
        # an ``init=`` SET survives only until the first release. The first run
        # of this script set it in ``init`` alone: the opening read saw all 43
        # rows, every later acquire ran with RLS in force, and the UPDATEs
        # matched zero rows -- silently, because asyncpg does not raise when a
        # statement affects nothing. It reported 34 writes and wrote none.
        await conn.execute("SET app.bypass_rls = 'true'")

    pool = await asyncpg.create_pool(
        dsn, min_size=1, max_size=3, init=_init, setup=_setup
    )
    try:
        async with pool.acquire() as conn:
            campaign = await conn.fetchrow(
                "SELECT name, status, knowledge_mode FROM campaigns "
                "WHERE id = $1::uuid AND tenant_id = $2::uuid",
                args.campaign_id,
                args.tenant_id,
            )
            if campaign is None:
                raise SystemExit(
                    f"campaign not found on that tenant: {args.campaign_id}"
                )

            live = await conn.fetchval(
                "SELECT count(*) FROM calls WHERE campaign_id = $1::uuid "
                "AND status IN ('ringing','in_progress','active','initiated')",
                args.campaign_id,
            )
            if live:
                raise SystemExit(
                    f"REFUSING: {live} call(s) in flight on this campaign. "
                    "Re-run when the line is clear."
                )

            rows = await conn.fetch(
                "SELECT id, heading, content, depth, position, path, "
                "       COALESCE(summary, '') AS summary "
                "FROM campaign_knowledge_nodes "
                "WHERE campaign_id = $1::uuid AND tenant_id = $2::uuid "
                "ORDER BY position, id",
                args.campaign_id,
                args.tenant_id,
            )

        total = len(rows)
        targets = [r for r in rows if args.all or not r["summary"].strip()]
        print(f"campaign          {campaign['name']} ({campaign['status']})")
        print(f"knowledge_mode    {campaign['knowledge_mode']}")
        print(f"nodes             {total} total, {total - len(targets)} already enriched")
        print(f"to enrich         {len(targets)}")
        if not targets:
            print("nothing to do")
            return 0

        if args.dry_run:
            print()
            print(
                "DRY RUN -- no writes. Would enrich in batches of "
                f"{_BATCH} and recompute search_text/search_tsv for each."
            )
            for r in targets[:5]:
                print(f"  - {r['heading'][:70]}")
            if len(targets) > 5:
                print(f"  ... and {len(targets) - 5} more")
            return 0

        done = 0
        failed = 0
        for start in range(0, len(targets), _BATCH):
            batch = targets[start : start + _BATCH]
            parsed = [
                ParsedNode(
                    heading=r["heading"] or "",
                    depth=r["depth"] or 1,
                    content=r["content"] or "",
                    position=r["position"] or 0,
                    path=r["path"] or "",
                )
                for r in batch
            ]
            try:
                enriched = await enrich_nodes(parsed)
            except Exception as exc:  # noqa: BLE001 - one bad batch must not stop the rest
                print(f"  batch {start // _BATCH + 1}: FAILED ({exc})")
                failed += len(batch)
                continue
            if len(enriched) != len(batch):
                print(f"  batch {start // _BATCH + 1}: count mismatch, skipped")
                failed += len(batch)
                continue

            async with pool.acquire() as conn:
                async with conn.transaction():
                    for row, e in zip(batch, enriched):
                        if not (e.summary or e.voice_answer):
                            failed += 1
                            continue
                        search_text = _search_text(
                            row["heading"] or "",
                            row["content"] or "",
                            e.keywords,
                            e.example_questions,
                        )
                        status = await conn.execute(
                            "UPDATE campaign_knowledge_nodes SET "
                            "  summary = $2, voice_answer = $3, keywords = $4, "
                            "  example_questions = $5, search_text = $6, "
                            "  search_tsv = to_tsvector('english', $6), "
                            "  updated_at = now() "
                            "WHERE id = $1",
                            row["id"],
                            e.summary,
                            e.voice_answer,
                            list(e.keywords or []),
                            list(e.example_questions or []),
                            search_text,
                        )
                        # Count what the DATABASE says it changed, not how many
                        # times we called execute. Under RLS an UPDATE that
                        # matches no row succeeds and returns "UPDATE 0", so
                        # counting calls reported 34 writes against 0 rows.
                        if status.strip() != "UPDATE 1":
                            raise RuntimeError(
                                f"UPDATE affected no row ({status.strip()!r}) for "
                                f"node {row['id']} — refusing to continue and "
                                "report writes that did not happen"
                            )
                        done += 1
            print(f"  batch {start // _BATCH + 1}: {done} enriched so far")

        async with pool.acquire() as conn:
            after = await conn.fetchrow(
                "SELECT count(*) AS n, "
                "  count(*) FILTER (WHERE summary IS NOT NULL AND summary <> '') AS enriched, "
                "  count(*) FILTER (WHERE voice_answer IS NOT NULL AND voice_answer <> '') AS spoken "
                "FROM campaign_knowledge_nodes "
                "WHERE campaign_id = $1::uuid AND tenant_id = $2::uuid",
                args.campaign_id,
                args.tenant_id,
            )
        print()
        print("read-back:")
        print(f"  nodes              {after['n']}")
        print(f"  with summary       {after['enriched']}")
        print(f"  with voice_answer  {after['spoken']}")
        print(f"  written this run   {done}, not enriched {failed}")
        if after["enriched"] == 0:
            print(
                "  !! still zero enriched -- the enricher returned nothing. "
                "Check the model id and the provider key before trusting this."
            )
            return 1
        return 0
    finally:
        await pool.close()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument(
        "--all",
        action="store_true",
        help="re-enrich every node, not only the bare ones",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
