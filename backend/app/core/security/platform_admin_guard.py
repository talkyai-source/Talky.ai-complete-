"""Transaction-scoped protection for changes to platform administrator authority.

Acquire the shared lock before any user row lock. All callers use the current
transaction, so authority checks, the remaining-admin count and the mutation
commit together. The lock is shared across processes, not just API workers.
"""

import asyncio

from fastapi import HTTPException

from app.core.security.principal import PrincipalUnavailable, load_current_principal


async def lock_user_administration(conn) -> None:
    await conn.execute(
        "SELECT pg_advisory_xact_lock("
        "hashtextextended('talky:platform-admin-authority', 0))"
    )


async def require_current_platform_admin(conn, actor_id) -> None:
    try:
        principal = await load_current_principal(conn, actor_id)
    except PrincipalUnavailable as exc:
        raise HTTPException(
            status_code=403, detail="Current platform admin authority is required."
        ) from exc
    if principal["role"] != "platform_admin":
        raise HTTPException(
            status_code=403, detail="Current platform admin authority is required."
        )


async def require_platform_admin_mutation(conn, actor_id) -> None:
    """Admit an Admin mutation under the existing authority lock.

    Call before resource/idempotency locks inside a READ COMMITTED transaction. The
    successful savepoint retains the advisory lock until that transaction ends.
    Tenant-scoped callers temporarily bypass RLS only for the canonical actor
    read; savepoint rollback restores their scope on denial, SQL error or timeout.
    No global audit-chain lock may be acquired after this authority lock.
    """
    if not conn.is_in_transaction():
        raise HTTPException(status_code=503, detail="Administrator authority requires an active transaction.")

    async def admit():
        async with conn.transaction():
            # The fence cannot refresh an older repeatable/serializable snapshot.
            # Refuse that context rather than read authority from before revocation.
            if await conn.fetchval("SHOW transaction_isolation") != "read committed":
                raise HTTPException(
                    status_code=503, detail="Current administrator authority requires a fresh transaction snapshot."
                )
            await lock_user_administration(conn)
            previous = await conn.fetchval("SELECT current_setting('app.bypass_rls', TRUE)")
            if previous != "on":
                await conn.execute("SELECT set_config('app.bypass_rls', $1, TRUE)", "on")
            await require_current_platform_admin(conn, actor_id)
            if previous != "on":
                await conn.execute("SELECT set_config('app.bypass_rls', $1, TRUE)", previous or "")

    try:
        # Bound ordinary contention/read waiting. Connection cancellation and
        # rollback can take additional time during a transport failure.
        await asyncio.wait_for(admit(), timeout=3.0)
    except HTTPException:
        raise
    except Exception as exc:
        # CancelledError is deliberately not caught: caller cancellation must
        # unwind the owning transaction rather than admit a business action.
        raise HTTPException(
            status_code=503, detail="Current administrator authority could not be confirmed."
        ) from exc


async def count_active_platform_admins(conn, *, exclude_id=None) -> int:
    return await conn.fetchval(
        "SELECT count(*) FROM user_profiles "
        "WHERE role = 'platform_admin' AND is_active = TRUE AND is_verified = TRUE "
        "AND ($1::uuid IS NULL OR id <> $1::uuid)",
        str(exclude_id) if exclude_id is not None else None,
    )
