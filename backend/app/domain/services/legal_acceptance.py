"""Versioned acceptance receipts; legal supplies the version identifiers/text."""
from fastapi import HTTPException
from app.core import config


def current_versions():
    settings = config.get_settings()
    return (
        (getattr(settings, "legal_terms_version", None) or "").strip() or None,
        (getattr(settings, "legal_privacy_version", None) or "").strip() or None,
    )


def validate_signup_acceptance(terms_version, privacy_version):
    required = getattr(config.get_settings(), "legal_acceptance_required", False)
    if not terms_version and not privacy_version and not required:
        return None
    current = current_versions()
    if not all(current):
        raise HTTPException(status_code=503, detail="Legal acceptance versions are not configured")
    if (terms_version, privacy_version) != current:
        raise HTTPException(status_code=422, detail={
            "code": "legal_acceptance_required",
            "message": "Accept the current Terms and Privacy Policy to continue",
            "terms_version": current[0], "privacy_version": current[1],
        })
    return current


async def record_acceptance(conn, *, tenant_id, user_id, versions, ip_address, user_agent):
    """Use the caller's account transaction; failure must roll back signup."""
    if versions is None:
        return
    await conn.execute(
        """INSERT INTO legal_acceptances
               (tenant_id, user_id, terms_version, privacy_version, accepted_at, ip_address, user_agent)
           VALUES ($1::uuid, $2::uuid, $3, $4, NOW(), $5, $6)""",
        tenant_id, user_id, versions[0], versions[1],
        str(ip_address or "")[:64] or None, str(user_agent or "")[:512] or None,
    )


async def acceptance_status(conn, *, tenant_id, user_id):
    terms, privacy = current_versions()
    required = False
    if terms and privacy and tenant_id:
        receipt = await conn.fetchrow(
            """SELECT terms_version, privacy_version FROM legal_acceptances
               WHERE tenant_id = $1::uuid AND user_id = $2::uuid
               ORDER BY accepted_at DESC, id DESC LIMIT 1""",
            tenant_id, user_id,
        )
        required = not receipt or (receipt["terms_version"], receipt["privacy_version"]) != (terms, privacy)
    return {"legal_acceptance_required": bool(required),
            "terms_version": terms, "privacy_version": privacy}
