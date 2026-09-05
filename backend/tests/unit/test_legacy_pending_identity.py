from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_recovery_only_uses_matching_unambiguous_durable_primary_leg():
    from app.domain.services.telephony.legacy_pending_identity import reconcile_pending_identity

    conn = AsyncMock()
    conn.execute.return_value = "UPDATE 1"
    conn.fetchval.return_value = 0
    await reconcile_pending_identity(conn, "asterisk")
    sql, provider = conn.execute.call_args.args
    assert provider == "asterisk"
    assert "leg_type='pstn_outbound'" in sql
    assert "NOT EXISTS" in sql
    assert "IS DISTINCT FROM $1" in sql
    assert "leg.provider_leg_id" in sql
    assert "status='termination_pending'" in sql
    assert "c.tenant_id=identity.tenant_id" in sql
    assert "SKIP LOCKED" in sql
    assert "outcome=" not in sql
    assert "duration_seconds=" not in sql


@pytest.mark.asyncio
async def test_missing_proof_is_reported_without_claiming_termination(caplog):
    from app.domain.services.telephony.legacy_pending_identity import reconcile_pending_identity

    conn = AsyncMock()
    conn.execute.return_value = "UPDATE 0"
    conn.fetchval.return_value = 1
    await reconcile_pending_identity(conn, "asterisk")
    assert "termination_pending_unrecoverable_identity count=1" in caplog.text


@pytest.mark.asyncio
async def test_another_adapter_cannot_adopt_asterisk_identity():
    from app.domain.services.telephony.legacy_pending_identity import reconcile_pending_identity

    conn = AsyncMock()
    with pytest.raises(ValueError, match="asterisk"):
        await reconcile_pending_identity(conn, "vonage")
    conn.execute.assert_not_called()
