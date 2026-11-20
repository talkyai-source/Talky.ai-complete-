"""Switch recording of INBOUND calls on or off for the whole platform.

The switch is ``platform_runtime_controls.inbound_recording_enabled``. While it
is off no inbound call is recorded, whatever a campaign is set to. Switching it
on does not by itself record anything: each inbound campaign still decides for
itself, and the account's recording setting and the spoken notice still apply
to every call.

This goes through ``InboundCampaignService.set_platform_controls``: the same
checked, versioned and audited path the admin panel uses. It changes ONLY the
recording switch; the other three inbound switches are sent back exactly as
they were read. Without ``--apply`` nothing is written: the state is printed.

The change is recorded under a platform administrator's account. Give one with
``--actor-user-id``; without it the longest-standing active, verified platform
administrator is used. No id, name or address is printed.

Usage (on the server, with backend/.env loaded):

    venv/bin/python scripts/set_inbound_recording_switch.py                     # show
    venv/bin/python scripts/set_inbound_recording_switch.py --state on --apply  # on
    venv/bin/python scripts/set_inbound_recording_switch.py --state off --apply # off
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

# Run as `venv/bin/python scripts/set_inbound_recording_switch.py`: make both the
# app package (backend/) and the sibling switch script (scripts/) importable.
_SCRIPTS = Path(__file__).resolve().parent
for _path in (_SCRIPTS.parent, _SCRIPTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

# One way to print the four switches and to choose the administrator, shared with
# the transfer switch so the two tools cannot drift apart.
from set_inbound_transfer_switch import _pick_platform_admin, _show  # noqa: E402

DEFAULT_REASON = "Owner decision, 20 November 2026: recording on inbound calls"


async def run(args: argparse.Namespace, *, service=None, pool=None) -> int:
    """``service`` and ``pool`` are injected by tests; the real run builds them."""
    own_pool = None
    if service is None:
        import asyncpg

        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            print("DATABASE_URL is not set (load backend/.env first)")
            return 2
        from app.core.db import _register_jsonb_codecs
        from app.domain.services.inbound_campaign_service import InboundCampaignService

        own_pool = pool = await asyncpg.create_pool(
            dsn, min_size=1, max_size=2, init=_register_jsonb_codecs
        )
        service = InboundCampaignService(pool)
    try:
        current = await service.get_platform_controls()
        _show("now", current)
        if args.state is None:
            return 0
        wanted = args.state == "on"
        if bool(current["recording_enabled"]) is wanted:
            print(f"recording of inbound calls is already {args.state}; nothing to do")
            return 0
        if wanted and not current["inbound_enabled"]:
            print("note: the master inbound switch is off, so no inbound call is taken at all")
        if not args.apply:
            print(f"would switch recording of inbound calls {args.state}; nothing written (add --apply)")
            return 0
        actor = args.actor_user_id or await _pick_platform_admin(pool)
        if not actor:
            print("no active, verified platform administrator account exists; nothing written")
            return 1
        payload = {
            "inbound_enabled": bool(current["inbound_enabled"]),
            "recording_enabled": wanted,
            "transfer_enabled": bool(current["transfer_enabled"]),
            "settlement_enabled": bool(current["settlement_enabled"]),
            "reason": args.reason,
            "expected_version": int(current["version"]),
        }
        try:
            result = await service.set_platform_controls(
                actor_id=actor,
                actor_role="platform_admin",
                payload=payload,
                # One key per state and version: running this again is a replay, not a second change.
                idempotency_key=f"inbound-recording-{args.state}-v{int(current['version'])}",
            )
        except Exception as exc:  # the service's own errors carry a code; print that, not the text
            print(f"not switched: {getattr(exc, 'code', None) or type(exc).__name__}")
            return 1
        _show("after", result)
        if bool(result["recording_enabled"]) is not wanted:
            print("not switched: the stored state does not match what was asked")
            return 1
        print(f"recording of inbound calls is now {args.state}")
        return 0
    finally:
        if own_pool is not None:
            await own_pool.close()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state", choices=("on", "off"), default=None,
                        help="what the recording switch should be; without it the state is only shown")
    parser.add_argument("--apply", action="store_true", help="write the change (default: show only)")
    parser.add_argument("--actor-user-id", default=None,
                        help="platform administrator the change is recorded under")
    parser.add_argument("--reason", default=DEFAULT_REASON, help="recorded with the change (3 to 1000 characters)")
    args = parser.parse_args(argv)
    if not 3 <= len(args.reason.strip()) <= 1000:
        parser.error("--reason must be 3 to 1000 characters")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
