"""Actual migrated-schema tenant session revocation regression.

The earlier fixture sliced an obsolete baseline and fabricated a queue table.
Use the explicit disposable migrated database and current actor/audit contract.
No provider or notification requests are made.
"""
from uuid import uuid4
import pytest

from tests.integration.test_admin_authority_invariant import authority_db  # noqa: F401
from tests.integration.test_formal_account_holds import account_db,hold  # noqa: F401

pytestmark=pytest.mark.integration


async def test_an_immediate_tenant_suspension_commits_and_revokes_only_its_sessions(account_db):
    db=account_db
    outsider,other_tenant=uuid4(),uuid4()
    try:
        await db.owner.execute("INSERT INTO tenants(id,business_name) VALUES($1,'Synthetic outside hold scope')",other_tenant)
        await db.owner.execute(
            "INSERT INTO user_profiles(id,email,tenant_id,role,is_active,is_verified,email_verified_at) VALUES($1,$2,$3,'user',TRUE,TRUE,NOW())",
            outsider,f"{outsider}@example.invalid",other_tenant)
        await db.owner.execute(
            "INSERT INTO tenant_users(user_id,tenant_id,role_id,status) SELECT $1,$2,id,'active' FROM roles WHERE name='user'",
            outsider,other_tenant)
        for user in (db.target,outsider):
            await db.owner.execute(
                "INSERT INTO security_sessions(user_id,session_token_hash,expires_at) VALUES($1,$2,NOW()+INTERVAL '1 day')",
                user,uuid4().hex)
        result=await hold(db)
        assert result.audit_event_id and result.revoked_sessions==1
        sessions={row["user_id"]:row for row in await db.owner.fetch(
            "SELECT user_id,revoked,revoked_at,revoke_reason FROM security_sessions WHERE user_id=ANY($1::uuid[])",
            [db.target,outsider])}
        assert (sessions[db.target]["revoked"],sessions[db.target]["revoke_reason"])==(True,"tenant_suspended")
        assert sessions[db.target]["revoked_at"] is not None
        assert (sessions[outsider]["revoked"],sessions[outsider]["revoked_at"])==(False,None)
    finally:
        await db.owner.execute("DELETE FROM tenant_users WHERE user_id=$1",outsider)
        await db.owner.execute("DELETE FROM user_profiles WHERE id=$1",outsider)
        await db.owner.execute("DELETE FROM tenants WHERE id=$1",other_tenant)
