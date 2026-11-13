#!/usr/bin/env python3
"""Audited DSAR export/erase preview; no destructive apply is implemented.

Run from backend/ with DATABASE_URL in the operator's process environment and
the application's existing audit signing configuration. Existing settings
loading still belongs to AuditLogger. Keep export archives private: they
intentionally contain the selected subject's PII.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from uuid import UUID

import asyncpg

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.domain.services.audit_logger import AuditLogger  # noqa: E402
from app.domain.services.dsar_service import DsarScope, DsarService  # noqa: E402


def _row_limit(value):
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Row limit must be an integer") from exc
    if not 1 <= number <= 10000:
        raise argparse.ArgumentTypeError("Row limit must be between 1 and 10000")
    return number


def build_parser():
    parser = argparse.ArgumentParser(description="Audited DSAR export or dry-run erase counts; no apply action.")
    actions = parser.add_subparsers(dest="action", required=True)
    for action in ("preview", "export"):
        sub = actions.add_parser(action)
        sub.add_argument("--tenant", type=UUID, required=True)
        sub.add_argument("--actor", type=UUID, required=True, help="Current platform-admin user UUID.")
        selector = sub.add_mutually_exclusive_group(required=True)
        selector.add_argument("--phone", help="Phone including its +country code.")
        selector.add_argument("--email")
        selector.add_argument("--whole-tenant", action="store_true")
        sub.add_argument("--row-limit", type=_row_limit, default=250)
        if action == "export":
            sub.add_argument("--output", type=Path, required=True, help="New private ZIP destination; never overwritten.")
    return parser


async def execute(args, *, service):
    scope = DsarScope(tenant_id=args.tenant, phone=args.phone, email=args.email, whole_tenant=args.whole_tenant)
    if args.action == "preview":
        manifest = await service.preview(scope, actor_id=args.actor)
    else:
        result = await service.export(scope, actor_id=args.actor, output=args.output)
        manifest = result.manifest
    # Counts/coverage only. Raw selector, rows, keys and archive path are not
    # printed (a caller may have placed PII in their own output filename).
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


async def run(args):
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL must be supplied in the process environment")
    pool = await asyncpg.create_pool(
        database_url.replace("postgresql+asyncpg://", "postgresql://", 1),
        min_size=1, max_size=1, timeout=10, command_timeout=15,
    )
    try:
        return await execute(args, service=DsarService(pool, audit=AuditLogger(pool), max_rows=args.row_limit))
    finally:
        await pool.close()


def main():
    args = build_parser().parse_args()
    try:
        return asyncio.run(run(args))
    except Exception as exc:
        # Provider/SQL errors may contain PII, DSNs or secrets. Only the type is
        # exposed; a failure is never represented as a completed export.
        print(f"DSAR stopped ({type(exc).__name__}); no destructive action was performed.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
