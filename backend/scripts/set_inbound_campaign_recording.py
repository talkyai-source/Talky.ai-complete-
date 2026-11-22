"""Switch "Record inbound calls" on or off for ONE tenant's incoming campaigns.

Each incoming campaign has its own recording box and recording notice. The
platform switch (set_inbound_recording_switch.py) only permits recording; a
campaign whose own box is unticked records nothing. This tool ticks or unticks
that box for the incoming campaigns of one account.

It goes through ``InboundCampaignService.update_campaign``: the call the website's
edit form ends in (PATCH /inbound-campaigns/{id}), so the product's own checks,
version handling and audit record apply. It changes ONLY ``recording_enabled``,
plus the recording notice for a campaign that has none. It never writes a table
itself. Without ``--apply`` nothing is written: the state is printed.

Who may do this: the endpoint needs the ``inbound:manage`` permission inside the
tenant (tenant admin, partner admin, platform admin). The change is recorded under
the account's oldest active tenant administrator, or ``--actor-user-id`` (checked:
an active member of THAT tenant holding ``inbound:manage``). No name, address or
id of a person is printed.

The service refuses to edit a LIVE (active) campaign: it must be paused first
(code ``pause_before_edit``). By default such a campaign is reported and left
alone. ``--pause-and-resume`` does what the website does by hand: pause (the line
stops taking calls), save the change, activate again (the product's readiness
checks must pass). If activation is refused the campaign is left paused and this
is said plainly.

Before pausing anything the tool proves that activation will succeed: the campaign's own readiness
report (``readiness`` from ``get_campaign``, built by ``InboundCampaignService._readiness`` and run
again by ``set_lifecycle("active")``) must be fully passing as it stands, and every recording
precondition of the new state must pass (platform switch on, account setting not "disabled", a saved
account setting when RECORDING_REQUIRE_NOTICE_PROOF demands one, a notice the product accepts). If
activation is refused after the change, the previous box and notice are put back and the campaign is
activated again. The pause time, the active-again time (UTC) and the gap in seconds are printed.

Usage (on the server, with backend/.env loaded):

    venv/bin/python scripts/set_inbound_campaign_recording.py --tenant 1845a165                       # show
    venv/bin/python scripts/set_inbound_campaign_recording.py --tenant 1845a165 --state on --apply    # switch on
    venv/bin/python scripts/set_inbound_campaign_recording.py --tenant 1845a165 --campaign 3fa85f64 \\
        --state on --apply --pause-and-resume                                                         # one live campaign
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

DEFAULT_REASON = "Owner decision, 20 November 2026: record incoming calls on this account's incoming campaigns"
# Same wording the website form offers (Talk-Leee inbound-campaign-form.tsx DEFAULT_RECORDING_NOTICE).
DEFAULT_NOTICE = "This call may be recorded for quality and training purposes."
_PREFIX = re.compile(r"^[0-9a-fA-F-]{8,36}$")


class Db:
    """The few direct reads the tool needs. Tests replace it with a fake."""

    def __init__(self, pool) -> None:
        self._pool = pool

    async def find_tenants(self, prefix: str) -> list[dict[str, Any]]:
        from app.core.db_utils import acquire_with_tenant

        # Platform-level read: only id and name of tenants.
        async with acquire_with_tenant(self._pool, None) as conn:
            rows = await conn.fetch(
                "SELECT id, business_name AS name FROM tenants "
                "WHERE id::text LIKE $1 || '%' ORDER BY id LIMIT 3",
                prefix.lower(),
            )
        return [{"id": str(r["id"]), "name": r["name"]} for r in rows]

    async def recording_mode(self, tenant_id: str) -> Optional[str]:
        from app.core.db_utils import acquire_with_tenant

        async with acquire_with_tenant(self._pool, None) as conn:
            row = await conn.fetchrow(
                "SELECT default_consent_mode FROM tenant_recording_policy WHERE tenant_id = $1::uuid",
                tenant_id,
            )
        return str(row["default_consent_mode"]) if row else None

    async def pick_actor(self, tenant_id: str) -> Optional[str]:
        """The oldest active, verified tenant administrator of this tenant."""
        from app.core.db_utils import acquire_with_tenant

        async with acquire_with_tenant(self._pool, None) as conn:
            row = await conn.fetchrow(
                "SELECT tu.user_id FROM tenant_users tu "
                "JOIN roles r ON r.id = tu.role_id "
                "JOIN user_profiles p ON p.id = tu.user_id AND p.is_active AND p.is_verified "
                "WHERE tu.tenant_id = $1::uuid AND tu.status = 'active' AND r.name = 'tenant_admin' "
                "ORDER BY tu.created_at, tu.user_id LIMIT 1",
                tenant_id,
            )
        return str(row["user_id"]) if row else None

    async def actor_role(self, tenant_id: str, user_id: str) -> Optional[str]:
        """Role name when the user is an active member of THIS tenant holding inbound:manage, else None."""
        from app.core.db_utils import acquire_with_tenant
        from app.core.security.rbac import Permission, check_permission, get_effective_permissions

        async with acquire_with_tenant(self._pool, None) as conn:
            row = await conn.fetchrow(
                "SELECT r.name FROM tenant_users tu "
                "JOIN roles r ON r.id = tu.role_id "
                "JOIN user_profiles p ON p.id = tu.user_id AND p.is_active AND p.is_verified "
                "WHERE tu.tenant_id = $1::uuid AND tu.user_id = $2::uuid AND tu.status = 'active' "
                "LIMIT 1",
                tenant_id,
                user_id,
            )
        if not row:
            return None
        # The endpoint's own rule: effective permissions read from the database.
        perms = await get_effective_permissions(self._pool, user_id, tenant_id)
        return str(row["name"]) if check_permission(perms, Permission.INBOUND_MANAGE) else None


def _short(value: Any) -> str:
    return str(value)[:8]


def _code(exc: Exception) -> str:
    return str(getattr(exc, "code", None) or type(exc).__name__)


_CONFLICT_PLAIN = ("the product only allows an edit while the campaign's number assignment is paused; "
                   "it is in another state")


def _described(exc: Exception) -> str:
    """The service code, with plain words for assignment_state_conflict."""
    code = _code(exc)
    return f"{code} ({_CONFLICT_PLAIN})" if code == "assignment_state_conflict" else code


def _assignment(c: dict[str, Any]) -> str:
    return str(c.get("assignment_status") or "").strip().lower()


def _number_words(c: dict[str, Any]) -> str:
    """Plain words for the campaign's number assignment (states: active, paused, quarantined, archived)."""
    state = _assignment(c)
    if state in ("active", "paused"):
        return "assigned"
    if state == "archived":
        return "none (assignment archived)"
    if state == "quarantined":
        return "held (quarantined)"
    return state or "unknown"


NO_NUMBER_TEXT = ("has no phone number assigned (its number assignment is archived), so it cannot take calls; "
                  "nothing to switch. Assign a number on the campaign's page first; recording can be switched on then.")


def notice_proof_required() -> bool:
    """The product's own rule (read from the environment at call time)."""
    from app.domain.services.recording_policy_service import notice_proof_required as rule

    return rule()


def _notice_problem(text: Optional[str]) -> Optional[str]:
    """The product's own message when a notice would be refused, else None."""
    from app.domain.services.inbound_campaign_service import RECORDING_NOTICE_UNSUPPORTED_MESSAGE
    from app.domain.services.recording_policy_service import contains_unsupported_dtmf_opt_out

    return RECORDING_NOTICE_UNSUPPORTED_MESSAGE if contains_unsupported_dtmf_opt_out(text) else None


def _show_campaign(label: str, c: dict[str, Any]) -> None:
    notice = str(c.get("consent_message") or "").strip()
    saved = f'yes ("{notice[:60]}")' if notice else "no"
    live = "yes" if str(c["status"]).lower() == "active" else "no"
    print(
        f'{label}: campaign {_short(c["id"])} "{c["name"]}" status={c["status"]} live={live} '
        f'number={_number_words(c)} recording={"on" if c["recording_enabled"] else "off"} '
        f'notice_saved={saved} version={c["version"]}'
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.strftime("%H:%M:%S") + " UTC"


def _print_gap(paused_at: datetime, active_at: Optional[datetime]) -> None:
    if active_at is None:
        print(f"paused at {_stamp(paused_at)}; NOT active again")
    else:
        seconds = (active_at - paused_at).total_seconds()
        print(f"paused at {_stamp(paused_at)}; active again at {_stamp(active_at)}; gap {seconds:.1f} seconds")


def _decide(c: dict[str, Any], state: str, *, controls, mode, notice_arg: str, pause: bool):
    """What --apply would do to one campaign: (kind, reason, notice_to_save).

    kind: "already" | "change" | "refuse" | "needs_flag" | "no_number". Everything that makes a live
    campaign unsafe to pause is decided HERE, before anything is written.
    """
    wanted = state == "on"
    if _assignment(c) == "archived":
        return "no_number", NO_NUMBER_TEXT, None
    if bool(c["recording_enabled"]) is wanted:
        return "already", "", None
    if _assignment(c) == "quarantined":
        return "refuse", "its number is held by a platform quarantine (assignment_quarantined); clear the quarantine first", None
    notice = None
    if wanted:
        if mode == "disabled":
            return "refuse", "the account's recording setting is \"disabled\" (its own choice)", None
        if not controls["recording_enabled"]:
            return "refuse", "the platform recording switch is off; switch it on first (set_inbound_recording_switch.py)", None
        existing = str(c.get("consent_message") or "").strip()
        notice = None if existing else notice_arg
        problem = _notice_problem(existing or notice_arg)
        if problem:
            return "refuse", problem, None
        if not (existing or str(notice_arg or "").strip()):
            return "refuse", "no recording notice is saved and none was given", None
    if str(c["status"]).lower() == "active":
        if not pause:
            return "needs_flag", "the campaign is live and the product only edits a paused campaign (pause_before_edit)", None
        # About to pause a live line: prove the product will let it go live again.
        readiness = c.get("readiness")
        if not isinstance(readiness, dict) or "ready" not in readiness:
            return "refuse", "the campaign's readiness could not be read, so re-activation cannot be proven", None
        if not readiness["ready"]:
            names = ", ".join(str(b.get("code")) for b in readiness.get("blockers") or []) or "unknown"
            return "refuse", f"the campaign would not pass its own readiness check as it stands: {names}", None
        if wanted and mode is None and notice_proof_required():
            return "refuse", ("the product requires a saved account recording setting before recording "
                              "incoming calls, and none is saved; re-activation would be refused"), None
    return "change", "", notice


async def _resume_after_failure(service, tenant_id, cid, actor, role, args, base, version=None):
    """Activate again. Returns None on success, else the refusing exception."""
    try:
        if version is None:
            version = int((await service.get_campaign(tenant_id=tenant_id, config_id=cid))["version"])
        await service.set_lifecycle(
            tenant_id=tenant_id, config_id=cid, target_status="active", actor_id=actor,
            actor_role=role, idempotency_key=f"{base}-resume-v{version}",
            expected_version=version, reason=args.reason,
        )
    except Exception as exc:
        return exc
    return None


async def _change(service, who, tenant_id: str, c: dict[str, Any], args, notice: Optional[str]) -> tuple[str, bool]:
    """Apply one campaign. Returns (text after `campaign <id> "<name>": `, succeeded)."""
    actor, role = who
    cid = str(c["id"])
    wanted = args.state == "on"
    live = str(c["status"]).lower() == "active"
    version = int(c["version"])
    base = f"inbound-campaign-recording-{args.state}-{cid}"
    payload: dict[str, Any] = {"expected_version": version, "recording_enabled": wanted, "reason": args.reason}
    if notice is not None:
        payload["consent_message"] = notice

    async def readback() -> tuple[str, bool]:
        after = await service.get_campaign(tenant_id=tenant_id, config_id=cid)
        _show_campaign("after", after)
        if bool(after["recording_enabled"]) is not wanted:
            return "not changed: the stored state does not match what was asked", False
        return f"recording is now {args.state}", True

    if not live:
        try:
            await service.update_campaign(
                tenant_id=tenant_id, config_id=cid, actor_id=actor, actor_role=role,
                payload=payload, idempotency_key=f"{base}-v{version}",
            )
        except Exception as exc:  # service errors carry a code; the text may name people
            return f"not changed: {_described(exc)}", False
        return await readback()

    # Live: pause, update, activate. Nothing else happens between the pause and the activation.
    try:
        paused = await service.set_lifecycle(
            tenant_id=tenant_id, config_id=cid, target_status="paused", actor_id=actor,
            actor_role=role, idempotency_key=f"{base}-pause-v{version}",
            expected_version=version, reason=args.reason,
        )
    except Exception as exc:
        return f"not changed: {_code(exc)}", False
    paused_at = _now()
    payload["expected_version"] = version = int(paused["version"])
    try:
        updated = await service.update_campaign(
            tenant_id=tenant_id, config_id=cid, actor_id=actor, actor_role=role,
            payload=payload, idempotency_key=f"{base}-v{version}",
        )
    except Exception as exc:
        update_code = _described(exc)
        refused = await _resume_after_failure(service, tenant_id, cid, actor, role, args, base)
        if refused is not None:
            _print_gap(paused_at, None)
            return f"CAMPAIGN STILL PAUSED: {_code(refused)} (the change itself failed: {update_code})", False
        _print_gap(paused_at, _now())
        return f"not changed: {update_code}; it was paused by this tool and is live again", False
    version = int(updated["version"])
    refused = await _resume_after_failure(service, tenant_id, cid, actor, role, args, base, version=version)
    if refused is None:
        _print_gap(paused_at, _now())
        return await readback()
    activation_code = _code(refused)
    # Activation was refused: put the previous box and notice back, then activate again.
    restore_error = None
    try:
        restored = await service.update_campaign(
            tenant_id=tenant_id, config_id=cid, actor_id=actor, actor_role=role,
            payload={"expected_version": version, "recording_enabled": bool(c["recording_enabled"]),
                     "consent_message": c.get("consent_message"), "reason": args.reason},
            idempotency_key=f"{base}-restore-v{version}",
        )
        version = int(restored["version"])
    except Exception as exc:
        restore_error = exc
        version = None  # unknown: re-read before activating
    again = await _resume_after_failure(service, tenant_id, cid, actor, role, args, f"{base}-again", version=version)
    if again is not None:
        _print_gap(paused_at, None)
        return f"CAMPAIGN STILL PAUSED: {_code(again)}", False
    _print_gap(paused_at, _now())
    if restore_error is not None:
        return (f"not changed: activation was refused ({activation_code}); restoring the previous setting "
                f"failed ({_code(restore_error)}); the campaign is active again with the NEW setting"), False
    return (f"not changed: activation was refused ({activation_code}); the previous setting was "
            "restored and the campaign is active again"), False


async def run(args: argparse.Namespace, *, service=None, pool=None, db=None) -> int:
    """``service`` and ``db`` are injected by tests; the real run builds them."""
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
        db = Db(pool)
    try:
        if not _PREFIX.match(args.tenant or ""):
            print("--tenant must be the account id or its first 8 or more characters; nothing written")
            return 1
        tenants = await db.find_tenants(args.tenant)
        if len(tenants) != 1:
            print(f"--tenant {args.tenant} matches {'no account' if not tenants else 'more than one account'}; nothing written")
            return 1
        tenant_id, tenant_name = tenants[0]["id"], tenants[0]["name"]
        controls = await service.get_platform_controls()
        mode = await db.recording_mode(tenant_id)
        print(f'account {_short(tenant_id)} "{tenant_name}"')
        print(f"platform recording switch: {'on' if controls['recording_enabled'] else 'off'}")
        print(f"account recording setting: {mode if mode else 'no saved setting'}")

        campaigns = await service.list_campaigns(tenant_id=tenant_id, include_archived=False)
        if args.campaign:
            if not _PREFIX.match(args.campaign):
                print("--campaign must be the campaign id or its first 8 or more characters; nothing written")
                return 1
            campaigns = [c for c in campaigns if str(c["id"]).startswith(args.campaign.lower())]
            if len(campaigns) != 1:
                print(f"--campaign {args.campaign} matches {'no incoming campaign' if not campaigns else 'more than one incoming campaign'} of this account; nothing written")
                return 1
        if not campaigns:
            print("this account has no incoming campaign")

        # What --apply would do. Without --state the preview is for "--state on".
        state = args.state or "on"
        plans = []
        for c in campaigns:
            kind, reason, notice = _decide(
                c, state, controls=controls, mode=mode, notice_arg=args.notice, pause=args.pause_and_resume
            )
            plans.append((c, kind, reason, notice))
            _show_campaign("now", c)
            if kind == "already":
                what = f"already {state}"
            elif kind == "no_number":
                what = reason
            elif kind == "needs_flag":
                what = "would need --pause-and-resume because the campaign is live"
            elif kind == "refuse":
                what = f"would be refused: {reason}"
            else:
                what = f"would switch recording {state}" + (
                    " (pauses the live campaign, then activates it again)" if str(c["status"]).lower() == "active" else ""
                )
            print(f'  with --state {state} --apply: {what}')
        if args.state is None:
            return 0

        lines: list[str] = []
        failed = False
        already = no_number = switched = 0
        todo = []
        for c, kind, reason, notice in plans:
            head = f'campaign {_short(c["id"])} "{c["name"]}": '
            if kind == "already":
                lines.append(head + f"already {args.state}; nothing to do")
                already += 1
            elif kind == "no_number":
                lines.append(head + reason)
                no_number += 1
            elif kind in ("refuse", "needs_flag"):
                lines.append(head + f"not changed: {reason}")
                failed = True
            else:
                todo.append((c, notice))
                if not args.apply:
                    lines.append(head + f"would switch recording {args.state}; nothing written (add --apply)")

        if args.apply and todo:
            actor = args.actor_user_id or await db.pick_actor(tenant_id)
            if not actor:
                print("no active tenant administrator of this account exists; nothing written")
                return 1
            role = await db.actor_role(tenant_id, actor)
            if not role:
                print("that user is not an active member of this account holding the inbound manage right; nothing written")
                return 1
            for c, notice in todo:
                head = f'campaign {_short(c["id"])} "{c["name"]}": '
                text, ok = await _change(service, (actor, role), tenant_id, c, args, notice)
                lines.append(head + text)
                if not ok:
                    failed = True
                else:
                    switched += 1
        for line in lines:
            print(line)
        if args.apply:
            print(f"switched {args.state}: {switched}; already {args.state}: {already}; "
                  f"no phone number (cannot take calls): {no_number}")
        if not args.apply and not failed and todo:
            print("nothing written (add --apply)")
        return 1 if failed else 0
    finally:
        if own_pool is not None:
            await own_pool.close()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tenant", required=True, help="account id, or its first 8 or more characters")
    parser.add_argument("--campaign", default=None,
                        help="incoming campaign id, or its first 8 or more characters (default: all of the account)")
    parser.add_argument("--state", choices=("on", "off"), default=None,
                        help="what the recording box should be; without it the state is only shown")
    parser.add_argument("--apply", action="store_true", help="write the change (default: show only)")
    parser.add_argument("--pause-and-resume", action="store_true",
                        help="for a LIVE campaign: pause it, save the change, activate it again")
    parser.add_argument("--notice", default=DEFAULT_NOTICE,
                        help="recording notice saved for a campaign that has none")
    parser.add_argument("--actor-user-id", default=None,
                        help="member of the account the change is recorded under (default: its oldest tenant administrator)")
    parser.add_argument("--reason", default=DEFAULT_REASON, help="recorded with the change (3 to 1000 characters)")
    args = parser.parse_args(argv)
    if not 3 <= len(args.reason.strip()) <= 1000:
        parser.error("--reason must be 3 to 1000 characters")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
