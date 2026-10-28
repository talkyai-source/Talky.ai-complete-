"""Serialize login issuance with formal holds and primary membership changes.

The profile row is the fence: admin writes already lock it after the audit
chain and administrator-authority locks. Authentication takes no admin locks
and must acquire this row before locking a challenge, refresh token or session.
The caller keeps the same transaction through proof revalidation and issuance.
"""
import asyncio

from app.core.security.principal import PrincipalUnavailable


async def lock_user_credential_issuance(conn, user_id):
    if not conn.is_in_transaction():
        raise RuntimeError("Credential issuance requires an active transaction")
    row = await asyncio.wait_for(conn.fetchrow(
        "SELECT id,password_hash,mfa_enabled FROM user_profiles WHERE id=$1::uuid FOR UPDATE",
        str(user_id),
    ), timeout=5.0)
    if row is None:
        raise PrincipalUnavailable("account_unavailable")
    return row


async def require_current_passkey_ceremony(conn, user_id, ceremony_id):
    """An anonymous ceremony cannot be bulk-revoked for one user.

Once the credential lookup identifies the candidate user, reject ceremonies from before
or during any formal hold, including after restoration, or before a primary
membership change. The membership cutoff comes from its mandatory business
audit, committed with the change; an audit event's generic timestamp is not a
credential cutoff. These timestamps use the database clock and the profile
fence serializes the change with completion.
"""
    stale = await conn.fetchval(
        """SELECT EXISTS(
            SELECT 1 FROM webauthn_challenges challenge
            WHERE challenge.id=$2::uuid AND (
                EXISTS (
                    SELECT 1 FROM suspension_events hold
                    WHERE hold.target_type='user' AND hold.target_id=$1::uuid
                      AND GREATEST(hold.suspended_at,
                          COALESCE(hold.restored_at,hold.suspended_at)) >= challenge.created_at
                ) OR EXISTS (
                    SELECT 1 FROM audit_logs cutoff
                    WHERE cutoff.resource_type='user' AND cutoff.resource_id=$1::uuid
                      AND cutoff.action='user_primary_membership_configured'
                      AND cutoff.metadata->'primary_membership_version'='1'::jsonb
                      AND (cutoff.metadata->>'credential_cutoff_at')::timestamptz
                          >= challenge.created_at
                )
            ))""",
        str(user_id), str(ceremony_id),
    )
    if stale:
        raise PrincipalUnavailable("authentication_ceremony_ended")


async def lock_authenticated_credential_change(conn, request, current_user):
    """Fence an already authenticated credential write against a formal hold.

    The dependency's fixed session must still be valid after acquiring the
    profile lock. A hold followed by restoration cannot revive an in-flight
    request authenticated with a now-revoked session.
    """
    from fastapi import HTTPException
    from app.core.security.principal import load_session_principal

    try:
        profile = await lock_user_credential_issuance(conn, current_user.id)
        state = request.state
        if str(getattr(state, "authenticated_user_id", "")) != str(current_user.id):
            raise PrincipalUnavailable("session_context_changed")
        principal = await load_session_principal(conn, {
            "sub": str(current_user.id),
            "sid": getattr(state, "authenticated_session_id", None),
            "tenant_id": current_user.tenant_id,
        })
        return {**dict(profile), "role": principal["role"]}
    except PrincipalUnavailable as exc:
        raise HTTPException(401, "Your login session has ended. Please sign in again.") from exc
    except Exception as exc:
        raise HTTPException(503, "Account verification is temporarily unavailable") from exc
