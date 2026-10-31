"""Edit the content of an inbound routing config: greeting, hours, after-hours.

Content and address are separate concerns. This only touches content, and it
refuses to move a config onto a different address -- that is the assignment
workflow's job, and the service enforces it independently.

Goes through ``InboundCampaignService.update_campaign``, so it inherits the
version check (optimistic concurrency), the paused-assignment precondition, the
config checksum, audit and the readiness recomputation.

Only the flags you pass are changed; everything else keeps its current value,
because ``update_campaign`` merges onto the config as it stands.

Usage (on the server, with backend/.env loaded):

    venv/bin/python scripts/update_inbound_config.py \\
        --tenant-id <uuid> --actor-user-id <uuid> --config-id <uuid> \\
        --after-hours-message "Sorry, we are closed right now." \\
        [--greeting "..."] [--after-hours-action voicemail]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))


async def _run(args: argparse.Namespace) -> int:
    import asyncpg

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set (load backend/.env first)")

    from app.core.db import _register_jsonb_codecs
    from app.domain.services.inbound_campaign_service import (
        InboundCampaignError,
        InboundCampaignService,
    )

    pool = await asyncpg.create_pool(
        dsn, min_size=1, max_size=2, init=_register_jsonb_codecs
    )
    try:
        service = InboundCampaignService(pool)
        before = await service.get_campaign(
            tenant_id=args.tenant_id, config_id=args.config_id
        )
        print(f"name={before.get('name')}")
        print(f"address={before.get('address')} kind={before.get('address_kind')}")
        print(f"before: status={before.get('status')} version={before.get('version')}")

        payload: Dict[str, Any] = {}
        if args.greeting is not None:
            payload["greeting"] = args.greeting
        if args.after_hours_action is not None:
            payload["after_hours_action"] = args.after_hours_action
        if args.business_hours_json is not None:
            # Wholesale replacement, so a malformed schedule can be reset. An
            # EMPTY object is explicitly 24/7; a non-empty one without
            # weekly_schedule is malformed (business_hours._weekly_windows).
            payload["business_hours"] = json.loads(args.business_hours_json)
        if args.after_hours_message is not None:
            # after_hours_message lives INSIDE business_hours, so the existing
            # object must be carried forward rather than replaced.
            hours = dict(payload.get("business_hours", before.get("business_hours") or {}))
            hours["after_hours_message"] = args.after_hours_message
            payload["business_hours"] = hours
        if args.transfer_number is not None:
            payload["transfer_number"] = args.transfer_number
        if not payload:
            raise SystemExit("nothing to change: pass at least one field")
        print(f"changing: {', '.join(sorted(payload))}")
        # update_campaign is optimistically concurrent: it refuses without the
        # version the caller believes it is editing, so a concurrent edit cannot
        # be silently overwritten. Default to the version just read.
        payload["expected_version"] = int(
            args.expected_version or before.get("version") or 0
        )

        try:
            after = await service.update_campaign(
                tenant_id=args.tenant_id,
                config_id=args.config_id,
                actor_id=args.actor_user_id,
                actor_role=args.actor_role,
                payload=payload,
                idempotency_key=f"update-{args.config_id}-{uuid.uuid4()}",
            )
        except InboundCampaignError as exc:
            raise SystemExit(f"refused: {exc} [{getattr(exc, 'code', '?')}]")

        print(f"after:  status={after.get('status')} version={after.get('version')}")
        readiness = after.get("readiness") or {}
        blockers = list(readiness.get("blockers") or [])
        if blockers:
            print("remaining readiness blockers:")
            for check in readiness.get("checks", []):
                if not check.get("passed"):
                    print(f"  BLOCKED {check.get('key')}: {check.get('detail')}")
        else:
            print("readiness: clean — ready to activate")
        return 0
    finally:
        await pool.close()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--actor-user-id", required=True)
    parser.add_argument("--actor-role", default="tenant_admin")
    parser.add_argument("--config-id", required=True)
    parser.add_argument("--greeting", default=None)
    parser.add_argument("--after-hours-action", default=None)
    parser.add_argument("--after-hours-message", default=None)
    parser.add_argument(
        "--business-hours-json",
        default=None,
        help='replace business_hours wholesale; "{}" means 24/7',
    )
    parser.add_argument("--transfer-number", default=None)
    parser.add_argument(
        "--expected-version",
        type=int,
        default=None,
        help="optimistic concurrency; defaults to the version just read",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
