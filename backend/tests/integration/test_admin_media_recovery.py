"""Normal-auth recovery reads and receipts on actual restricted PostgreSQL.

Reuses the existing exact-owned media fixture. Its external erasers are
synthetic; real local filesystem/browser acceptance is a separate gate.
"""
import json
import re
from uuid import UUID, uuid4

import pytest

from tests.integration.test_admin_real_auth_journey import journey_db  # noqa: F401
from tests.integration.test_admin_users_tenants_crud import call, crud_db, signed_in  # noqa: F401
from tests.integration.test_admin_media_current_authority import (
    media_authority_db, delete, resource, REASON,  # noqa: F401
)

pytestmark = pytest.mark.integration
RUN_EVIDENCE = []
SAFE_FIELDS = {"id", "resource_type", "resource_id", "tenant_id", "call_id", "original_actor_id",
    "reason", "status", "attempt_count", "created_at", "updated_at", "object_deleted_at", "completed_at"}


async def history(db, client, **params):
    return await call(db, client, "GET", "/admin/media-deletions", params=params)


@pytest.mark.parametrize("kind", ["recordings", "feedback"])
async def test_deleted_resource_legacy_replay_alias_and_original_history(media_authority_db, kind):  # noqa: F811
    db = media_authority_db
    resource_type, resource_id = resource(db, kind)
    key = "media-recovery:" + uuid4().hex
    async with signed_in(db) as first, signed_in(db, 2) as second:
        deleted = await delete(db, first, kind, key)
        assert deleted.status_code == 200
        receipt = deleted.json()
        assert receipt["outcome"] == "completed" and receipt["resource_type"] == resource_type
        assert receipt["resource_id"] == str(resource_id)
        intent_id = UUID(receipt["deletion_intent_id"])
        original = dict(await db.owner.fetchrow("SELECT * FROM admin_media_deletion_intents WHERE id=$1", intent_id))
        # A schema-valid historical cached response has no new typed fields.
        legacy = {"detail": "Original legacy completion", "deletion_intent_id": str(intent_id)}
        await db.owner.execute("UPDATE admin_media_deletion_intents SET response_body=$2::jsonb WHERE id=$1", intent_id, json.dumps(legacy))
        replay = await delete(db, first, kind, key)
        assert replay.status_code == 200
        assert replay.json() == {**receipt, "detail": legacy["detail"]}
        alias_reason = "Different current operator reviewed existing saved result"
        alias = await call(db, second, "DELETE", f"/admin/{kind}/{resource_id}",
            json={"reason": alias_reason}, headers={"Idempotency-Key": "new-actor-alias:" + uuid4().hex})
        assert alias.status_code == 200 and alias.json() == replay.json()
        assert len(db.eraser_calls) == 1
        exact = await call(db, second, "GET", f"/admin/media-deletions/{intent_id}")
        assert exact.status_code == 200
        saved = exact.json()
        assert set(saved) == SAFE_FIELDS
        assert saved["status"] == "completed" and saved["completed_at"] and saved["object_deleted_at"]
        assert saved["original_actor_id"] == str(db.users[0]) and saved["reason"] == REASON
        assert saved["resource_id"] == str(resource_id) and saved["call_id"] == str(db.media_call_id)
        assert saved["tenant_id"] == str(db.tenants[1])
        listed = await history(db, first, resource_type=resource_type, resource_id=str(resource_id), limit=1)
        assert listed.status_code == 200 and listed.json() == {"items": [saved], "has_more": False, "next_cursor": None}
        stored = dict(await db.owner.fetchrow("SELECT * FROM admin_media_deletion_intents WHERE id=$1", intent_id))
        assert stored["actor_id"] == original["actor_id"] and stored["reason"] == original["reason"]
        assert stored["attempt_count"] == original["attempt_count"] == 1
        assert stored["completed_at"] == original["completed_at"]
        body = stored["response_body"]
        assert (json.loads(body) if isinstance(body, str) else body) == legacy, "Read/replay must not rewrite history"
        keys = await db.owner.fetch("SELECT actor_id,request_reason FROM admin_media_deletion_request_keys WHERE intent_id=$1", intent_id)
        assert len(keys) == 2 and {row["actor_id"] for row in keys} == {db.users[0], db.users[2]}
        RUN_EVIDENCE.append({"case": "legacy_alias_recovery", "kind": kind, "intent_id": str(intent_id),
                             "eraser_calls": 1, "aliases": 2, "original_actor_reason_preserved": True})


@pytest.mark.parametrize("kind", ["recordings", "feedback"])
async def test_absent_without_intent_is_not_completed(media_authority_db, kind):  # noqa: F811
    db = media_authority_db
    missing = uuid4()
    async with signed_in(db) as admin:
        response = await call(db, admin, "DELETE", f"/admin/{kind}/{missing}",
            json={"reason": REASON}, headers={"Idempotency-Key": "absent:" + uuid4().hex})
        assert response.status_code == 200
        assert response.json()["outcome"] == "absent" and response.json()["deletion_intent_id"] is None
        assert response.json()["resource_id"] == str(missing) and "no saved deletion outcome" in response.json()["detail"]
        listed = await history(db, admin, resource_id=str(missing))
        assert listed.status_code == 200 and listed.json() == {"items": [], "has_more": False, "next_cursor": None}
        assert not db.eraser_calls
        assert await db.owner.fetchval("SELECT count(*) FROM admin_media_deletion_intents WHERE call_id=$1", db.media_call_id) == 0


async def test_failed_saved_stage_and_active_hold_do_not_claim_completion(media_authority_db):  # noqa: F811
    db = media_authority_db
    key = "failed-held:" + uuid4().hex
    db.storage_fails = True
    async with signed_in(db) as admin:
        response = await delete(db, admin, "recordings", key)
        assert response.status_code == 503
        row = dict(await db.owner.fetchrow("SELECT * FROM admin_media_deletion_intents WHERE call_id=$1", db.media_call_id))
        await db.owner.execute("""INSERT INTO suspension_events(suspension_id,target_type,target_id,
            suspension_type,reason_category,reason_description,is_active)
            VALUES($1,'tenant',$2,'COMPLIANCE','synthetic','Synthetic media legal hold',TRUE)""", uuid4(), db.tenants[1])
        saved = await call(db, admin, "GET", f"/admin/media-deletions/{row['id']}")
        assert saved.status_code == 200 and set(saved.json()) == SAFE_FIELDS
        assert saved.json()["status"] == "failed" and saved.json()["completed_at"] is None
        db.storage_fails = False
        blocked = await delete(db, admin, "recordings", key)
        assert blocked.status_code == 423 and len(db.eraser_calls) == 1
        after = dict(await db.owner.fetchrow("SELECT * FROM admin_media_deletion_intents WHERE id=$1", row["id"]))
        assert after == row, "History and held retry cannot advance saved stage"


async def test_history_authorization_missing_and_real_read_failure(media_authority_db):  # noqa: F811
    db = media_authority_db
    async with db.pool.acquire() as conn:
        role = await conn.fetchval("SELECT current_user")
    assert re.fullmatch(r"admin_http_[0-9a-f]{32}", role)
    async with signed_in(db) as admin, signed_in(db, 1) as ordinary:
        assert (await history(db, ordinary)).status_code == 403
        assert (await call(db, ordinary, "GET", f"/admin/media-deletions/{uuid4()}")).status_code == 403
        missing = await call(db, admin, "GET", f"/admin/media-deletions/{uuid4()}")
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "media_deletion_not_found"
        assert (await call(db, admin, "GET", "/admin/media-deletions/not-a-uuid")).status_code == 422
        assert (await history(db, admin, limit=101)).status_code == 422
        assert (await history(db, admin, cursor="malformed!")).status_code == 422
        await db.owner.execute(f'REVOKE SELECT ON admin_media_deletion_intents FROM "{role}"')
        try:
            denied = await history(db, admin)
            assert denied.status_code == 503 and denied.json()["error"]["code"] == "media_deletion_history_unavailable"
            exact = await call(db, admin, "GET", f"/admin/media-deletions/{uuid4()}")
            assert exact.status_code == 503
        finally:
            await db.owner.execute(f'GRANT SELECT ON admin_media_deletion_intents TO "{role}"')
        assert (await history(db, admin)).status_code == 200
        await db.owner.execute("UPDATE user_profiles SET is_active=FALSE WHERE id=$1", db.users[0])
        assert (await history(db, admin)).status_code == 401


async def test_tied_history_pagination_filters_and_nullable_legacy_stages(media_authority_db):  # noqa: F811
    db = media_authority_db
    identities = [uuid4() for _ in range(105)]
    resources = [uuid4() for _ in identities]
    stamp = await db.owner.fetchval("SELECT clock_timestamp()")
    stages = ("intent_committed", "object_deleted", "failed", "completed")
    async with db.owner.transaction():
        await db.owner.executemany("""INSERT INTO admin_media_deletion_intents
            (id,actor_id,tenant_id,call_id,resource_type,resource_id,idempotency_key,reason,
             resource_snapshot,status,attempt_actor_ids,created_at,updated_at)
            VALUES($1,$2,$3,$4,'recording',$5,$6,'Synthetic history original reason',
                   '{"s3_key":"must-not-leak","transcript":"must-not-leak"}'::jsonb,
                   $7,ARRAY[$2]::uuid[],$8,$8)""",
            [(identity, db.users[0], db.tenants[1], db.media_call_id, resources[index],
              "history:" + identity.hex, stages[index % 4], stamp) for index, identity in enumerate(identities)])
    before = await db.owner.fetch("SELECT * FROM admin_media_deletion_intents WHERE call_id=$1 ORDER BY id", db.media_call_id)
    async with signed_in(db) as admin:
        seen, cursor, first_cursor = [], None, None
        for page in range(3):
            params = {"tenant_id": str(db.tenants[1]), "call_id": str(db.media_call_id), "limit": 37}
            if cursor:
                params["cursor"] = cursor
            response = await history(db, admin, **params)
            assert response.status_code == 200
            body = response.json()
            assert all(set(row) == SAFE_FIELDS for row in body["items"])
            assert all(row["object_deleted_at"] is None and row["completed_at"] is None for row in body["items"])
            assert "must-not-leak" not in response.text
            seen.extend(row["id"] for row in body["items"])
            assert body["has_more"] is (page < 2)
            cursor = body["next_cursor"]
            if page == 0:
                first_cursor = cursor
        assert cursor is None and seen == sorted(map(str, identities), reverse=True)
        mismatch = await history(db, admin, tenant_id=str(db.tenants[1]), call_id=str(db.media_call_id),
                                 status="completed", cursor=first_cursor)
        assert mismatch.status_code == 422
        filtered = await history(db, admin, call_id=str(db.media_call_id), status="failed", resource_type="recording")
        assert filtered.status_code == 200 and len(filtered.json()["items"]) == 26
        assert {row["status"] for row in filtered.json()["items"]} == {"failed"}
        exact = await call(db, admin, "GET", f"/admin/media-deletions/{identities[3]}")
        assert exact.status_code == 200 and exact.json()["status"] == "completed" and exact.json()["completed_at"] is None
    after = await db.owner.fetch("SELECT * FROM admin_media_deletion_intents WHERE call_id=$1 ORDER BY id", db.media_call_id)
    assert before == after
    RUN_EVIDENCE.append({"case": "live_keyset_ties", "rows": 105, "pages": 3,
                         "reads_changed_intents": False, "nullable_legacy_times_preserved": True})
