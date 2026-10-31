"""Register a phone number on a tenant and verify it, through the supported path.

Goes through ``TenantPhoneNumberService`` rather than writing the table, so it
inherits the normaliser, the idempotency on ``(tenant_id, e164)``, the
platform-admin check on verification and the audit trail. Re-running it is safe:
``create_pending`` returns the existing row instead of inserting a duplicate,
and a number already verified is left as it is.

WHAT THIS DOES AND DOES NOT DO

Registering a number makes it selectable on that account: it can be picked as a
caller ID and it appears in the inbound campaign form's number list. It does NOT
move where the carrier delivers calls. Inbound routing follows the SIP account
the carrier sends the call to, so a number whose calls arrive on another
tenant's SIP account still reaches that tenant until the carrier re-points it.

Attaching the number to a campaign is a separate, deliberate step done in the
app. The database already enforces that a number can be attached to only ONE
campaign at a time, platform-wide: see the partial unique index
``uq_inbound_live_canonical_did`` on ``inbound_did_assignments``. Detaching
(archiving the assignment) is what frees it for another campaign.

Usage (on the server, with backend/.env loaded):

    venv/bin/python scripts/add_tenant_phone_number.py \\
        --tenant-id <uuid> --e164 +442046132300 --provider blaze \\
        --actor-user-id <platform-admin-uuid> \\
        --proof-reference "owner request 2026-09-22" \\
        --label "UK DID"

Pass --no-verify to leave it in pending_verification for review.
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


async def _run(args: argparse.Namespace) -> int:
    import asyncpg

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set (load backend/.env first)")

    from app.core.db import _register_jsonb_codecs
    from app.domain.models.tenant_phone_number import VerificationMethod
    from app.domain.services.tenant_phone_number_service import (
        TenantPhoneNumberError,
        TenantPhoneNumberService,
    )

    # mark_verified stores ``method.value``, so it needs the ENUM, not the raw
    # string. Passing the string crashed with AttributeError after the number
    # had already been registered, leaving it stranded in
    # pending_verification (observed against production 2026-09-22).
    try:
        method = VerificationMethod(args.method)
    except ValueError:
        raise SystemExit(
            "unknown --method %r; expected one of: %s"
            % (args.method, ", ".join(m.value for m in VerificationMethod))
        )

    pool = await asyncpg.create_pool(
        dsn, min_size=1, max_size=2, init=_register_jsonb_codecs
    )
    try:
        svc = TenantPhoneNumberService(pool)

        existing = await svc.list_for_tenant(args.tenant_id)
        print(f"numbers on this tenant before: {len(existing)}")
        for row in existing:
            print(f"  {row.e164:<16} {row.status}")

        try:
            created = await svc.create_pending(
                tenant_id=args.tenant_id,
                e164=args.e164,
                provider=args.provider,
                label=args.label,
                metadata=None,
                actor_id=args.actor_user_id,
                actor_role=args.actor_role,
            )
        except TenantPhoneNumberError as exc:
            raise SystemExit(f"refused: {exc}")
        print(f"registered: {created.e164} id={created.id} status={created.status}")

        if args.no_verify:
            print("left pending_verification (--no-verify)")
        elif str(created.status) == "verified":
            print("already verified — nothing to do")
        else:
            # Reached both on a first run and on a re-run for a number left in
            # pending_verification by an earlier failure, which is why
            # create_pending returning the existing row matters.
            try:
                verified = await svc.mark_verified(
                    tenant_id=args.tenant_id,
                    did_id=str(created.id),
                    method=method,
                    verified_by=args.verified_by,
                    proof_reference=args.proof_reference,
                    stir_shaken_token=args.stir_shaken_token,
                    proof_notes=args.proof_notes,
                    actor_id=args.actor_user_id,
                    actor_role=args.actor_role,
                )
            except TenantPhoneNumberError as exc:
                raise SystemExit(
                    f"registered but NOT verified: {exc}\n"
                    "  (verification needs a platform administrator actor)"
                )
            print(f"verified:   {verified.e164} status={verified.status}")

        after = await svc.list_for_tenant(args.tenant_id)
        print(f"numbers on this tenant after: {len(after)}")
        for row in after:
            print(f"  {row.e164:<16} {row.status:<22} {row.label or ''}")
        print()
        print(
            "NOTE: this makes the number selectable on the account. It does not "
            "change where the carrier delivers calls, and it does not attach the "
            "number to a campaign. Attach it in the app; the database allows only "
            "one live attachment per number, platform-wide."
        )
        return 0
    finally:
        await pool.close()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--e164", required=True)
    parser.add_argument("--provider", default="blaze")
    parser.add_argument("--label", default=None)
    parser.add_argument("--actor-user-id", required=True)
    parser.add_argument("--actor-role", default="platform_admin")
    parser.add_argument("--verified-by", default="ops-admin")
    parser.add_argument(
        "--method",
        default="manual_admin",
        help="sms_code | carrier_api | manual_admin | letter_of_authorization",
    )
    parser.add_argument(
        "--proof-reference",
        default=None,
        help="required unless --no-verify; recorded on the audit trail",
    )
    parser.add_argument("--proof-notes", default=None)
    parser.add_argument("--stir-shaken-token", default=None)
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="register only, leave in pending_verification",
    )
    args = parser.parse_args(argv)
    if not args.no_verify and not (args.proof_reference or "").strip():
        parser.error("--proof-reference is required unless --no-verify is passed")
    return asyncio.run(_run(args))


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
