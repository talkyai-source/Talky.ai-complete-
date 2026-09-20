"""Create an inbound routing config addressed by an internal PBX extension.

``bind_inbound_extension.py`` attaches an extension to a config that already
exists. This creates the config itself, for a tenant that has no public phone
number to build one on -- which is the whole point of extension addressing.

It goes through ``InboundCampaignService.create_campaign``, the same entry point
the API uses, so it inherits every guarantee that path already makes:
idempotency, the campaign direction lock, the config checksum, versioning, audit,
the ownership check (the chosen trunk must log in to the carrier as these exact
digits, with registration enabled) and the global extension uniqueness rule.
Nothing is written directly.

A BASE CAMPAIGN MUST ALREADY EXIST. This deliberately does not invent one: a
campaign carries the agent's prompt, voice and goal, which are product decisions
an operator makes in the app, not defaults a provisioning script should guess.

Usage (on the server, with backend/.env loaded):

    venv/bin/python scripts/create_inbound_extension_campaign.py \\
        --tenant-id <uuid> --actor-user-id <uuid> --actor-role tenant_admin \\
        --campaign-id <uuid> --sip-trunk-id <uuid> \\
        --extension 940003 --name "Reception" --greeting "Hello, ..." \\
        [--timezone Europe/London] [--activate]

``--activate`` runs the lifecycle transition to active afterwards, which is
refused unless every readiness check passes. Without it the config is left
paused so the readiness report can be reviewed first.
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

# Run as `venv/bin/python scripts/create_inbound_extension_campaign.py` -- Python
# puts scripts/ on sys.path, not backend/, so make the app package importable
# (same as the other backend/scripts entry points).
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.domain.services.telephony.inbound_address import (  # noqa: E402
    canonical_extension,
    parse_extension,
)


def _fail(message: str) -> None:
    raise SystemExit(f"refused: {message}")


def build_payload(args: argparse.Namespace) -> Dict[str, Any]:
    """The exact body the API's InboundCampaignCreateRequest would carry."""
    digits = parse_extension(canonical_extension(args.extension))
    if digits is None:
        _fail(f"{args.extension!r} is not a valid internal extension (3-8 digits)")
    payload: Dict[str, Any] = {
        "name": args.name,
        "did_number": canonical_extension(digits),
        "campaign_id": args.campaign_id,
        "sip_trunk_id": args.sip_trunk_id,
        "timezone": args.timezone,
        "opening_mode": args.opening_mode,
        "greeting": args.greeting,
        "business_hours": json.loads(args.business_hours) if args.business_hours else {},
        "after_hours_action": args.after_hours_action,
        "recording_enabled": False,
        "recording_policy": {},
        "transfer_policy": {},
        "qualification_config": {},
    }
    if args.transfer_number:
        payload["transfer_number"] = args.transfer_number
    return payload


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

    payload = build_payload(args)
    pool = await asyncpg.create_pool(
        dsn, min_size=1, max_size=2, init=_register_jsonb_codecs
    )
    try:
        service = InboundCampaignService(pool)
        try:
            created = await service.create_campaign(
                tenant_id=args.tenant_id,
                actor_id=args.actor_user_id,
                actor_role=args.actor_role,
                payload=payload,
                # Deterministic per (tenant, extension): re-running returns the
                # same config instead of creating a second one.
                idempotency_key=f"ext-campaign-{args.tenant_id}-{args.extension}",
            )
        except InboundCampaignError as exc:
            _fail(f"{exc} [{getattr(exc, 'code', 'inbound_error')}]")

        # _serialize_bundle keys the config as "id", not "config_id".
        config_id = created["id"]
        print(f"config_id={config_id}")
        print(f"address={created.get('address')} kind={created.get('address_kind')}")
        print(f"status={created.get('status')} version={created.get('version')}")
        print(f"assignment_status={created.get('assignment_status')}")
        if created.get("idempotent_replay"):
            print("idempotent_replay=True (config already existed; not recreated)")

        readiness = created.get("readiness") or {}
        blockers = list(readiness.get("blockers") or [])
        print(f"readiness_blockers={','.join(blockers) if blockers else 'none'}")

        if args.activate:
            if blockers:
                # set_lifecycle re-reads readiness inside its own transaction
                # after driving the base campaign to running, so some of these
                # clear on the way. Report them and let it decide.
                print(f"attempting activation despite: {','.join(blockers)}")
            try:
                await service.set_lifecycle(
                    tenant_id=args.tenant_id,
                    config_id=config_id,
                    target_status="active",
                    actor_id=args.actor_user_id,
                    actor_role=args.actor_role,
                    idempotency_key=f"ext-activate-{config_id}-{uuid.uuid4()}",
                    expected_version=int(created.get("version") or 1),
                    reason="extension campaign provisioning",
                )
            except InboundCampaignError as exc:
                _fail(f"activation refused: {exc} [{getattr(exc, 'code', '?')}]")
            print("activated=True")
            print("next: run reconcile_asterisk_release.sh to render the route")
        else:
            print("activated=False (re-run with --activate once readiness is clean)")
    finally:
        await pool.close()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--actor-user-id", required=True)
    parser.add_argument("--actor-role", default="tenant_admin")
    parser.add_argument(
        "--campaign-id",
        required=True,
        help="an EXISTING inbound campaign; this script never invents one",
    )
    parser.add_argument("--sip-trunk-id", required=True, help="the trunk for this extension")
    parser.add_argument("--extension", required=True, help="digits only, e.g. 940003")
    parser.add_argument("--name", required=True)
    parser.add_argument("--greeting", required=True)
    parser.add_argument("--timezone", default="UTC")
    parser.add_argument(
        "--opening-mode", default="agent_first", choices=("agent_first", "caller_first")
    )
    parser.add_argument("--business-hours", default=None, help="JSON object")
    parser.add_argument(
        "--after-hours-action", default="voicemail",
    )
    parser.add_argument("--transfer-number", default=None)
    parser.add_argument("--activate", action="store_true")
    args = parser.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
