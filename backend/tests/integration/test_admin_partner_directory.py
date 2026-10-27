"""Normal-auth contract for the existing Next Admin partner selector.

Prepared before the missing GET /admin/partners route is implemented. These
are real HTTP/SQL acceptance assertions, not an expected-404 success test.
Only the inherited explicitly selected disposable database may be used.
"""
import re
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
import pytest_asyncio

from tests.integration.test_admin_real_auth_journey import journey_db  # noqa: F401
from tests.integration.test_admin_users_tenants_crud import (  # noqa: F401
    call, crud_db, signed_in,
)

pytestmark = pytest.mark.integration
RUN_EVIDENCE = []
VISIBLE_FIELDS = {"id", "name", "status", "suspended_at", "tenant_count", "updated_at"}


@pytest_asyncio.fixture
async def partner_list_db(crud_db):  # noqa: F811
    db = crud_db
    db.partner_owner_tenants = [uuid4() for _ in range(4)]
    db.partners = [uuid4() for _ in range(4)]
    db.partner_prefix = "Synthetic Partner " + uuid4().hex
    db.partner_names = [db.partner_prefix + suffix for suffix in (" Alpha", " Alpha", " Beta", " Gamma")]
    db.partner_statuses = ["active", "suspended", "pending_deletion", None]
    async with db.pool.acquire() as conn:
        db.partner_runtime_role = await conn.fetchval("SELECT current_user")
    assert re.fullmatch(r"admin_http_[0-9a-f]{32}", db.partner_runtime_role)
    original_links = await db.owner.fetch(
        "SELECT id,white_label_partner_id FROM tenants WHERE id=ANY($1::uuid[]) ORDER BY id",
        db.tenants,
    )
    assert len(original_links) == 3 and all(row["white_label_partner_id"] is None for row in original_links)
    try:
        for index, (tenant, partner) in enumerate(zip(db.partner_owner_tenants, db.partners)):
            await db.owner.execute(
                "INSERT INTO tenants(id,business_name,status,subscription_status) VALUES($1,$2,'active','active')",
                tenant, db.partner_prefix + " owner " + str(index),
            )
            await db.owner.execute(
                """INSERT INTO white_label_partners(id,tenant_id,company_name,status,contact_email,
                   custom_domain,branding_config,billing_config)
                   VALUES($1,$2,$3,$4,'synthetic-private@example.com','synthetic-private.invalid',
                          '{"private_fixture_marker":true}'::jsonb,'{"private_fixture_marker":true}'::jsonb)""",
                partner, tenant, db.partner_names[index], db.partner_statuses[index],
            )
        # The canonical parent is white_label_partner_id, not tenants.partner_id.
        # Admin A remains outside the linked B/C children.
        await db.owner.execute(
            "UPDATE tenants SET white_label_partner_id=$1 WHERE id=ANY($2::uuid[])",
            db.partners[0], db.tenants[1:],
        )
        db.partner_before = [dict(row) for row in await db.owner.fetch(
            "SELECT * FROM white_label_partners WHERE id=ANY($1::uuid[]) ORDER BY id", db.partners,
        )]
        yield db
        after = [dict(row) for row in await db.owner.fetch(
            "SELECT * FROM white_label_partners WHERE id=ANY($1::uuid[]) ORDER BY id", db.partners,
        )]
        assert after == db.partner_before, "Directory reads must not normalize or mutate stored partner state"
    finally:
        # Exactly this fixture's UUIDs; inherited protected-state/role cleanup
        # runs after these FK-dependent rows have been removed.
        async with db.owner.transaction():
            await db.owner.execute(
                "UPDATE tenants SET white_label_partner_id=NULL "
                "WHERE id=ANY($1::uuid[]) AND white_label_partner_id=ANY($2::uuid[])",
                db.tenants, db.partners,
            )
            await db.owner.execute("DELETE FROM white_label_partners WHERE id=ANY($1::uuid[])", db.partners)
            await db.owner.execute("DELETE FROM tenants WHERE id=ANY($1::uuid[])", db.partner_owner_tenants)


def list_body(response):
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"items", "total", "page", "page_size"}
    assert type(body["total"]) is int and body["total"] >= 0
    for item in body["items"]:
        assert set(item) == VISIBLE_FIELDS
        assert type(item["tenant_count"]) is int and item["tenant_count"] >= 0
        assert item["status"] in {"active", "suspended", "pending_deletion", "unknown"}
    return body


async def test_platform_directory_returns_all_stored_states_and_only_public_fields(partner_list_db):
    db = partner_list_db
    async with signed_in(db) as admin:
        response = await call(db, admin, "GET", "/admin/partners",
                              params={"q": db.partner_prefix, "page": 1, "page_size": 8})
        RUN_EVIDENCE.append({"case": "directory_route_and_shape", "status": response.status_code})
        body = list_body(response)
        assert body["total"] == 4 and body["page"] == 1 and body["page_size"] == 8
        indexed = {item["id"]: item for item in body["items"]}
        assert set(indexed) == {str(partner) for partner in db.partners}
        for index, partner in enumerate(db.partners):
            item = indexed[str(partner)]
            assert item["name"] == db.partner_names[index]
            assert item["status"] == (db.partner_statuses[index] or "unknown")
            assert item["tenant_count"] == (2 if index == 0 else 0)
            assert item["suspended_at"] is None
            assert isinstance(item["updated_at"], str)
        assert "private_fixture_marker" not in response.text and "synthetic-private" not in response.text


async def test_directory_stable_pages_include_equal_names_and_truthful_empty_end(partner_list_db):
    db = partner_list_db
    expected = sorted(zip(db.partner_names, map(str, db.partners)))
    async with signed_in(db) as admin:
        pages = [list_body(await call(db, admin, "GET", "/admin/partners",
                    params={"q": db.partner_prefix, "page": page, "page_size": 2}))
                 for page in (1, 2, 3)]
        assert [(item["name"], item["id"]) for page in pages[:2] for item in page["items"]] == expected
        assert all(page["total"] == 4 and page["page_size"] == 2 for page in pages)
        assert [page["page"] for page in pages] == [1, 2, 3]
        assert pages[2]["items"] == []


async def test_directory_search_id_casefold_and_status_match_displayed_state(partner_list_db):
    db = partner_list_db
    async with signed_in(db) as admin:
        casefold = list_body(await call(db, admin, "GET", "/admin/partners",
            params={"q": db.partner_prefix.lower()}))
        assert casefold["total"] == 4
        exact = list_body(await call(db, admin, "GET", "/admin/partners", params={"q": str(db.partners[2])}))
        assert exact["total"] == 1 and exact["items"][0]["id"] == str(db.partners[2])
        for index, state in enumerate(("active", "suspended", "pending_deletion", "unknown")):
            filtered = list_body(await call(db, admin, "GET", "/admin/partners",
                params={"q": db.partner_prefix, "status": state}))
            assert filtered["total"] == 1 and filtered["items"][0]["id"] == str(db.partners[index])
        empty = list_body(await call(db, admin, "GET", "/admin/partners", params={"q": uuid4().hex}))
        assert empty["total"] == 0 and empty["items"] == []


@pytest.mark.parametrize("params", [
    {"page": 0}, {"page_size": 0}, {"page_size": 101}, {"status": "invented"}, {"q": "x" * 256},
], ids=["page-zero", "size-zero", "size-over-cap", "unknown-status", "search-over-cap"])
async def test_directory_rejects_unbounded_or_invalid_queries(partner_list_db, params):
    db = partner_list_db
    async with signed_in(db) as admin:
        assert (await call(db, admin, "GET", "/admin/partners", params=params)).status_code == 422


@pytest.mark.parametrize("role", ["user", "tenant_admin", "partner_admin"])
async def test_directory_denies_current_non_platform_roles(partner_list_db, role):
    db = partner_list_db
    role_id = await db.owner.fetchval("SELECT id FROM roles WHERE name=$1", role)
    assert role_id is not None, "Canonical migrated role required"
    async with db.owner.transaction():
        await db.owner.execute("UPDATE user_profiles SET role=$2 WHERE id=$1", db.users[1], role)
        await db.owner.execute("UPDATE tenant_users SET role_id=$2 WHERE user_id=$1", db.users[1], role_id)
    async with signed_in(db, 1) as member:
        me = await call(db, member, "GET", "/auth/me")
        assert me.json()["role"] == role
        assert (await call(db, member, "GET", "/admin/partners")).status_code == 403


async def test_old_admin_jwt_does_not_keep_directory_authority_after_current_demotion(partner_list_db):
    db = partner_list_db
    async with signed_in(db) as admin:
        await db.owner.execute("UPDATE user_profiles SET role='tenant_admin' WHERE id=$1", db.users[0])
        response = await call(db, admin, "GET", "/admin/partners")
        assert response.status_code == 403


async def test_directory_select_failure_is_503_not_empty_success(partner_list_db, monkeypatch):
    db = partner_list_db
    from app.api.v1.endpoints.admin import partners

    original_acquire = partners.acquire_with_tenant
    admitted = False

    @asynccontextmanager
    async def deny_directory_read(*args, **kwargs):
        nonlocal admitted
        # Delay only the fixture's real permission fault until canonical auth
        # dependencies have completed. The endpoint retains its real SQL/scope.
        async with original_acquire(*args, **kwargs) as conn:
            await db.owner.execute(f'REVOKE SELECT ON white_label_partners FROM "{db.partner_runtime_role}"')
            admitted = True
            yield conn

    async with signed_in(db) as admin:
        monkeypatch.setattr(partners, "acquire_with_tenant", deny_directory_read)
        try:
            response = await call(db, admin, "GET", "/admin/partners", params={"q": db.partner_prefix})
            RUN_EVIDENCE.append({"case": "real_select_acl_failure", "status": response.status_code,
                                 "directory_admitted_after_normal_auth": admitted})
            assert admitted
            assert response.status_code == 503 and "items" not in response.json()
            assert response.json()["error"]["code"] == "partner_directory_unavailable"
        finally:
            await db.owner.execute(f'GRANT SELECT ON white_label_partners TO "{db.partner_runtime_role}"')
            monkeypatch.setattr(partners, "acquire_with_tenant", original_acquire)
        recovered = list_body(await call(db, admin, "GET", "/admin/partners", params={"q": db.partner_prefix}))
        assert recovered["total"] == 4
