"""A database built from database/complete_schema.sql grants exactly the
application's role matrix (app/core/security/rbac.py ROLE_DEFAULT_PERMISSIONS),
the same source scripts/seed_rbac_standalone.py seeds production from.

The fresh path once handed ``platform:admin`` to tenant and partner admins and
user-management and billing writes to plain users (QA F2-06). Real SQL only:
the test builds a disposable ``*_test`` database on a localhost server.
"""
import os
import re
import uuid
from pathlib import Path
from urllib.parse import urlparse

import asyncpg
import pytest

from app.core.security.rbac import ROLE_DEFAULT_PERMISSIONS, UserRole

BACKEND = Path(__file__).resolve().parents[2]


async def _fresh_schema_grants():
    admin_dsn = os.getenv("TALKY_SCHEMA_TEST_ADMIN_DSN", "").strip()
    if not admin_dsn:
        pytest.skip("explicit TALKY_SCHEMA_TEST_ADMIN_DSN (localhost admin DSN) is required")
    parsed = urlparse(admin_dsn)
    if parsed.hostname not in {"localhost", "127.0.0.1"}:
        pytest.fail("Only a localhost server is allowed")
    name = f"fresh_grants_{uuid.uuid4().hex}_test"
    assert re.fullmatch(r"fresh_grants_[0-9a-f]{32}_test", name)
    admin = await asyncpg.connect(admin_dsn)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        conn = await asyncpg.connect(parsed._replace(path="/" + name).geturl())
        try:
            await conn.execute((BACKEND / "database/complete_schema.sql").read_text(encoding="utf-8"))
            roles = {r["name"] for r in await conn.fetch("SELECT name FROM roles")}
            permissions = {r["name"] for r in await conn.fetch("SELECT name FROM permissions")}
            rows = await conn.fetch(
                """SELECT r.name AS role, p.name AS permission
                     FROM role_permissions rp
                     JOIN roles r ON r.id = rp.role_id
                     JOIN permissions p ON p.id = rp.permission_id"""
            )
        finally:
            await conn.close()
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        await admin.close()
    grants: dict[str, set[str]] = {}
    for row in rows:
        grants.setdefault(row["role"], set()).add(row["permission"])
    return roles, permissions, grants


async def test_fresh_schema_grants_platform_rights_to_platform_admin_only():
    _, _, grants = await _fresh_schema_grants()
    holders = {role for role, names in grants.items() if any(n.startswith("platform") for n in names)}
    assert holders == {"platform_admin"}


async def test_fresh_schema_role_grants_equal_the_application_matrix():
    roles, permissions, grants = await _fresh_schema_grants()
    assert {role.value for role in UserRole} <= roles
    for role, wanted in ROLE_DEFAULT_PERMISSIONS.items():
        expected = {p.value for p in wanted} & permissions
        assert grants.get(role.value, set()) == expected, role.value
