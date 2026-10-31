#!/usr/bin/env python3
"""Freeze the TTS provider on campaigns saved with a voice_id but no provider.

    python scripts/backfill_campaign_tts_provider.py                 # dry run
    python scripts/backfill_campaign_tts_provider.py --apply         # write
    python scripts/backfill_campaign_tts_provider.py --tenant <uuid> # one tenant

WHY THIS EXISTS (aiopt-2)
-------------------------
A campaign row with ``voice_id`` set and ``tts_provider`` NULL is paired at
call time with whatever TTS provider the tenant has NOW. If the tenant
switched engines after the campaign was saved, the voice id is sent to the
wrong engine. New saves now pin the provider; this repairs older rows.

INFERENCE (shown per row)
-------------------------
* ``voice_shape``    the voice id's shape names exactly one engine (UUID ->
                     cartesia, ``aura-`` -> deepgram, ``-Chirp`` -> google).
* ``tenant_current`` the shape is ambiguous (e.g. ElevenLabs ids): the tenant's
                     CURRENT provider from ``tenant_ai_configs``.
* ``unresolved``     neither is available: the row is left NULL.

Dry run is the default; nothing is written without ``--apply``. With
``--apply`` every UPDATE carries an explicit ``tenant_id`` predicate (and
``tts_provider IS NULL``) inside one transaction.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from typing import Any, Iterable, Optional

sys.path.insert(0, os.getcwd())
from app.domain.services.telephony_session_config import voice_id_provider_hint  # noqa: E402

BASIS_SHAPE = "voice_shape"
BASIS_TENANT = "tenant_current"
BASIS_UNRESOLVED = "unresolved"


@dataclass(frozen=True)
class RowPlan:
    campaign_id: str
    tenant_id: str
    voice_id: str
    provider: Optional[str]
    basis: str


def infer_provider(voice_id: Optional[str], tenant_provider: Optional[str]) -> tuple[Optional[str], str]:
    """(provider, basis) for one campaign row. Pure."""
    hint = voice_id_provider_hint(voice_id)
    if hint:
        return hint, BASIS_SHAPE
    tenant = str(getattr(tenant_provider, "value", tenant_provider) or "").strip().lower()
    if tenant:
        return tenant, BASIS_TENANT
    return None, BASIS_UNRESOLVED


def plan_rows(rows: Iterable[dict], tenant_providers: dict[str, Optional[str]]) -> list[RowPlan]:
    plans: list[RowPlan] = []
    for row in rows:
        tenant_id = str(row["tenant_id"])
        provider, basis = infer_provider(row.get("voice_id"), tenant_providers.get(tenant_id))
        plans.append(RowPlan(str(row["id"]), tenant_id, str(row.get("voice_id") or ""), provider, basis))
    return plans


def dsn() -> str:
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        try:
            from app.core.config import get_settings

            raw = get_settings().database_url
        except Exception:  # noqa: BLE001
            raw = None
    if not raw:
        sys.exit("DATABASE_URL is required. Try: set -a && . ./.env && set +a")
    return raw.replace("postgresql+asyncpg", "postgresql")


async def run(args: Any) -> int:
    import asyncpg

    conn = await asyncpg.connect(dsn(), timeout=15)
    try:
        sql = (
            "SELECT id::text AS id, tenant_id::text AS tenant_id, voice_id FROM campaigns "
            "WHERE voice_id IS NOT NULL AND btrim(voice_id) <> '' AND tts_provider IS NULL"
        )
        params: list = []
        if args.tenant:
            sql += " AND tenant_id = $1::uuid"
            params.append(args.tenant)
        rows = [dict(r) for r in await conn.fetch(sql, *params)]
        tenant_providers: dict[str, Optional[str]] = {}
        if rows:
            cfg = await conn.fetch(
                "SELECT tenant_id::text AS tenant_id, tts_provider FROM tenant_ai_configs "
                "WHERE tenant_id::text = ANY($1::text[])",
                sorted({str(r["tenant_id"]) for r in rows}),
            )
            tenant_providers = {str(r["tenant_id"]): r["tts_provider"] for r in cfg}

        plans = plan_rows(rows, tenant_providers)
        print(f"campaigns with voice_id and NULL tts_provider: {len(plans)}")
        for p in plans[: args.limit]:
            print(
                f"  tenant={p.tenant_id} campaign={p.campaign_id} voice={p.voice_id!r} "
                f"-> {p.provider or 'NULL (left alone)'}  [{p.basis}]"
            )
        if len(plans) > args.limit:
            print(f"  ... and {len(plans) - args.limit} more")

        writable = [p for p in plans if p.provider]
        print(f"\nresolvable: {len(writable)}  unresolved: {len(plans) - len(writable)}")
        if not args.apply:
            print("DRY RUN - nothing written. Re-run with --apply to write.")
            return 0

        written = 0
        async with conn.transaction():
            for p in writable:
                status = await conn.execute(
                    "UPDATE campaigns SET tts_provider = $3 "
                    "WHERE id = $1::uuid AND tenant_id = $2::uuid AND tts_provider IS NULL",
                    p.campaign_id,
                    p.tenant_id,
                    p.provider,
                )
                if status.endswith(" 1"):
                    written += 1
        print(f"APPLIED - {written} campaigns updated.")
        return 0
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pin tts_provider on campaigns that have a voice_id but none (dry run by default).",
    )
    parser.add_argument("--apply", action="store_true", help="Actually write. Without this the script only reports.")
    parser.add_argument("--tenant", default=None, help="Restrict to one tenant_id (UUID).")
    parser.add_argument("--limit", type=int, default=100, help="Rows to print (default 100).")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
