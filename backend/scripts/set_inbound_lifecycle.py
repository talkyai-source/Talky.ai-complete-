"""Move an inbound routing config through its lifecycle: active, paused, archived.

Creation and activation were coupled inside
``create_inbound_extension_campaign.py``, which is wrong in two ways. A config
that was created but not activated -- because the process died, or because a
readiness check was still failing -- had no way forward except re-running
creation, and creation is idempotency-keyed on the request, so re-running it
with any different input is correctly refused rather than silently doing
something else. Activation deserves its own verb.

Goes through ``InboundCampaignService.set_lifecycle``, which drives the base
campaign to ``running``, re-reads readiness inside the same transaction, and
moves the config and its assignment together. If readiness refuses, every
failing check is printed, because "activation refused" on its own tells an
operator nothing they can act on.

Usage (on the server, with backend/.env loaded):

    venv/bin/python scripts/set_inbound_lifecycle.py \\
        --tenant-id <uuid> --actor-user-id <uuid> \\
        --config-id <uuid> --status active
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path
from typing import Optional

# Run as `venv/bin/python scripts/set_inbound_lifecycle.py` -- Python puts
# scripts/ on sys.path, not backend/, so make the app package importable.
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
        InboundReadinessError,
    )

    pool = await asyncpg.create_pool(
        dsn, min_size=1, max_size=2, init=_register_jsonb_codecs
    )
    try:
        service = InboundCampaignService(pool)

        current = await service.get_campaign(
            tenant_id=args.tenant_id, config_id=args.config_id
        )
        print(f"name={current.get('name')}")
        print(f"address={current.get('address')} kind={current.get('address_kind')}")
        print(
            f"before: config={current.get('status')} "
            f"assignment={current.get('assignment_status')} "
            f"version={current.get('version')}"
        )
        if current.get("status") == args.status:
            print(f"already {args.status}; nothing to do")
            return 0

        try:
            result = await service.set_lifecycle(
                tenant_id=args.tenant_id,
                config_id=args.config_id,
                target_status=args.status,
                actor_id=args.actor_user_id,
                actor_role=args.actor_role,
                idempotency_key=f"lifecycle-{args.config_id}-{args.status}-{uuid.uuid4()}",
                expected_version=int(
                    args.expected_version or current.get("version") or 1
                ),
                reason=args.reason,
            )
        except InboundReadinessError as exc:
            # Say WHICH checks refused. "Not ready" alone is unactionable.
            readiness = getattr(exc, "readiness", None) or {}
            print("refused: readiness is not satisfied")
            for check in readiness.get("checks", []):
                if not check.get("passed"):
                    print(f"  BLOCKED {check.get('key')}: {check.get('detail')}")
            return 1
        except InboundCampaignError as exc:
            raise SystemExit(f"refused: {exc} [{getattr(exc, 'code', '?')}]")

        print(
            f"after:  config={result.get('status')} "
            f"assignment={result.get('assignment_status')} "
            f"version={result.get('version')}"
        )
        if args.status == "active":
            print("next: run reconcile_asterisk_release.sh to render the route")
        return 0
    finally:
        await pool.close()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--actor-user-id", required=True)
    parser.add_argument("--actor-role", default="tenant_admin")
    parser.add_argument("--config-id", required=True)
    parser.add_argument(
        "--status", required=True, choices=("active", "paused", "archived")
    )
    parser.add_argument("--expected-version", type=int, default=None)
    parser.add_argument("--reason", default="operator lifecycle change")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
