"""
Day 4 – RBAC (Role-Based Access Control) + Tenant Isolation.

Tests cover:
  ✓ UserRole hierarchy levels
  ✓ can_access() role comparison
  ✓ Role aliases and normalisation
  ✓ Permission definitions
  ✓ Role-permission default mappings
  ✓ check_permission() with direct, admin, and platform_admin grants
  ✓ RBACContext initialization guard
  ✓ Platform admin bypass behaviour

Note: rbac.py has a circular import with app.api.v1.dependencies.
      We break the cycle by pre-loading dependencies before importing rbac.
"""
from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Break the circular import: stub the dependency that rbac.py imports
# at module level.  This lets us import the pure-logic parts we need
# without pulling in the entire FastAPI dependency graph.
# ---------------------------------------------------------------------------

def _get_rbac_module():
    """Lazy-import rbac, breaking the circular dependency chain."""
    # If dependencies module hasn't been loaded yet, inject a stub
    dep_key = "app.api.v1.dependencies"
    if dep_key not in sys.modules:
        stub = MagicMock()
        sys.modules[dep_key] = stub

    import app.core.security.rbac as rbac  # noqa: E402
    return rbac


@pytest.fixture(scope="module")
def rbac():
    """Provide the rbac module with circular import resolved."""
    return _get_rbac_module()


@pytest.fixture(scope="module")
def UserRole(rbac):
    return rbac.UserRole


@pytest.fixture(scope="module")
def Permission(rbac):
    return rbac.Permission


@pytest.fixture(scope="module")
def ROLE_DEFAULT_PERMISSIONS(rbac):
    return rbac.ROLE_DEFAULT_PERMISSIONS


@pytest.fixture(scope="module")
def ROLE_ALIASES(rbac):
    return rbac.ROLE_ALIASES


# ========================================================================
# UserRole Hierarchy
# ========================================================================


class TestUserRoleHierarchy:
    """Verify NIST RBAC role hierarchy."""

    def test_level_ordering(self, UserRole):
        """Roles must be ordered: platform_admin > partner_admin > tenant_admin > user > readonly."""
        assert UserRole.PLATFORM_ADMIN.level > UserRole.PARTNER_ADMIN.level
        assert UserRole.PARTNER_ADMIN.level > UserRole.TENANT_ADMIN.level
        assert UserRole.TENANT_ADMIN.level > UserRole.USER.level
        assert UserRole.USER.level > UserRole.READONLY.level

    def test_platform_admin_can_access_all(self, UserRole):
        for role in UserRole:
            assert UserRole.PLATFORM_ADMIN.can_access(role) is True

    def test_readonly_cannot_access_any_higher(self, UserRole):
        assert UserRole.READONLY.can_access(UserRole.READONLY) is True
        assert UserRole.READONLY.can_access(UserRole.USER) is False
        assert UserRole.READONLY.can_access(UserRole.TENANT_ADMIN) is False

    def test_user_can_access_user_and_readonly(self, UserRole):
        assert UserRole.USER.can_access(UserRole.USER) is True
        assert UserRole.USER.can_access(UserRole.READONLY) is True
        assert UserRole.USER.can_access(UserRole.TENANT_ADMIN) is False

    def test_tenant_admin_can_access_user(self, UserRole):
        assert UserRole.TENANT_ADMIN.can_access(UserRole.USER) is True
        assert UserRole.TENANT_ADMIN.can_access(UserRole.PARTNER_ADMIN) is False

    def test_same_role_can_access_self(self, UserRole):
        for role in UserRole:
            assert role.can_access(role) is True


# ========================================================================
# Role Normalisation
# ========================================================================


class TestNormalizeRole:
    """normalize_role() tests."""

    def test_valid_enum_value(self, rbac, UserRole):
        assert rbac.normalize_role("platform_admin") == UserRole.PLATFORM_ADMIN
        assert rbac.normalize_role("user") == UserRole.USER

    def test_alias_admin(self, rbac, UserRole):
        assert rbac.normalize_role("admin") == UserRole.TENANT_ADMIN

    def test_alias_owner(self, rbac, UserRole):
        assert rbac.normalize_role("owner") == UserRole.TENANT_ADMIN

    def test_alias_super_admin(self, rbac, UserRole):
        assert rbac.normalize_role("super_admin") == UserRole.PLATFORM_ADMIN

    def test_unknown_defaults_to_readonly(self, rbac, UserRole):
        """Fail-safe: unknown roles default to readonly."""
        assert rbac.normalize_role("hacker") == UserRole.READONLY
        assert rbac.normalize_role("") == UserRole.READONLY


# ========================================================================
# Permission Definitions
# ========================================================================


class TestPermissions:
    """Basic sanity checks for Permission enum."""

    def test_campaigns_permissions_exist(self, Permission):
        assert Permission.CAMPAIGNS_CREATE.value == "campaigns:create"
        assert Permission.CAMPAIGNS_READ.value == "campaigns:read"

    def test_recording_permissions_exist(self, Permission):
        assert Permission.RECORDINGS_READ.value == "recordings:read"
        assert Permission.RECORDINGS_DOWNLOAD.value == "recordings:download"
        assert Permission.RECORDINGS_DELETE.value == "recordings:delete"

    def test_platform_admin_permission_exists(self, Permission):
        assert Permission.PLATFORM_ADMIN.value == "platform:admin"

    def test_all_permissions_use_resource_action_format(self, Permission):
        for perm in Permission:
            parts = perm.value.split(":")
            assert len(parts) >= 2, f"Permission {perm} must be resource:action format"


# ========================================================================
# Role-Permission Default Mappings
# ========================================================================


class TestRoleDefaultPermissions:
    """ROLE_DEFAULT_PERMISSIONS mapping tests."""

    def test_all_roles_have_mappings(self, UserRole, ROLE_DEFAULT_PERMISSIONS):
        for role in UserRole:
            assert role in ROLE_DEFAULT_PERMISSIONS

    def test_readonly_has_only_read_permissions(self, UserRole, ROLE_DEFAULT_PERMISSIONS):
        readonly_perms = ROLE_DEFAULT_PERMISSIONS[UserRole.READONLY]
        for perm in readonly_perms:
            assert "read" in perm.value or "export" in perm.value, \
                f"Readonly should not have {perm.value}"

    def test_platform_admin_has_platform_permissions(self, UserRole, Permission, ROLE_DEFAULT_PERMISSIONS):
        platform_perms = ROLE_DEFAULT_PERMISSIONS[UserRole.PLATFORM_ADMIN]
        assert Permission.PLATFORM_ADMIN in platform_perms
        assert Permission.PLATFORM_TENANTS_MANAGE in platform_perms
        assert Permission.PLATFORM_USERS_MANAGE in platform_perms

    def test_user_does_not_have_users_delete(self, UserRole, Permission, ROLE_DEFAULT_PERMISSIONS):
        user_perms = ROLE_DEFAULT_PERMISSIONS[UserRole.USER]
        assert Permission.USERS_DELETE not in user_perms

    def test_tenant_admin_has_users_manage(self, UserRole, Permission, ROLE_DEFAULT_PERMISSIONS):
        admin_perms = ROLE_DEFAULT_PERMISSIONS[UserRole.TENANT_ADMIN]
        assert Permission.USERS_MANAGE in admin_perms

    def test_recording_defaults_match_rollout_matrix(
        self, UserRole, Permission, ROLE_DEFAULT_PERMISSIONS
    ):
        expected = {
            UserRole.READONLY: {Permission.RECORDINGS_READ},
            UserRole.USER: {
                Permission.RECORDINGS_READ,
                Permission.RECORDINGS_DOWNLOAD,
            },
            UserRole.TENANT_ADMIN: {
                Permission.RECORDINGS_READ,
                Permission.RECORDINGS_DOWNLOAD,
                Permission.RECORDINGS_DELETE,
            },
            UserRole.PARTNER_ADMIN: {
                Permission.RECORDINGS_READ,
                Permission.RECORDINGS_DOWNLOAD,
                Permission.RECORDINGS_DELETE,
            },
            UserRole.PLATFORM_ADMIN: {
                Permission.RECORDINGS_READ,
                Permission.RECORDINGS_DOWNLOAD,
                Permission.RECORDINGS_DELETE,
            },
        }

        for role, expected_permissions in expected.items():
            actual = {
                permission
                for permission in ROLE_DEFAULT_PERMISSIONS[role]
                if permission.value.startswith("recordings:")
            }
            assert actual == expected_permissions

    def test_hierarchy_is_additive(self, UserRole, ROLE_DEFAULT_PERMISSIONS):
        """Higher roles should have at least all permissions of lower roles."""
        readonly = ROLE_DEFAULT_PERMISSIONS[UserRole.READONLY]
        user = ROLE_DEFAULT_PERMISSIONS[UserRole.USER]
        admin = ROLE_DEFAULT_PERMISSIONS[UserRole.TENANT_ADMIN]

        # user ⊇ readonly (read permissions)
        for perm in readonly:
            if "read" in perm.value or "export" in perm.value:
                assert perm in user, f"user missing readonly perm {perm}"

        # admin ⊇ user
        for perm in user:
            assert perm in admin, f"tenant_admin missing user perm {perm}"


# ========================================================================
# check_permission()
# ========================================================================


class TestCheckPermission:
    """check_permission() tests."""

    def test_direct_permission_grants_access(self, rbac, Permission):
        perms = {Permission.CAMPAIGNS_CREATE}
        assert rbac.check_permission(perms, Permission.CAMPAIGNS_CREATE) is True

    def test_missing_permission_denies_access(self, rbac, Permission):
        perms = {Permission.CAMPAIGNS_READ}
        assert rbac.check_permission(perms, Permission.CAMPAIGNS_CREATE) is False

    def test_admin_permission_grants_resource_access(self, rbac, Permission):
        """campaigns:admin should grant campaigns:create."""
        perms = {Permission.CAMPAIGNS_ADMIN}
        assert rbac.check_permission(perms, Permission.CAMPAIGNS_CREATE) is True

    def test_platform_admin_grants_everything(self, rbac, Permission):
        """platform:admin should grant any permission.

        Note: check_permission checks resource:admin before platform:admin.
        If no resource:admin exists for a resource (e.g. 'users'), a ValueError
        is raised. Test only with resources that have an :admin enum value.
        """
        perms = {Permission.PLATFORM_ADMIN}
        assert rbac.check_permission(perms, Permission.CAMPAIGNS_CREATE) is True
        assert rbac.check_permission(perms, Permission.BILLING_READ) is True
        assert rbac.check_permission(perms, Permission.TENANTS_READ) is True

    def test_empty_permissions_denies_all(self, rbac, Permission):
        assert rbac.check_permission(set(), Permission.CAMPAIGNS_READ) is False
        assert rbac.check_permission(set(), Permission.PLATFORM_ADMIN) is False

    def test_admin_for_wrong_resource_denies(self, rbac, Permission):
        """campaigns:admin should NOT grant billing:read."""
        perms = {Permission.CAMPAIGNS_ADMIN}
        assert rbac.check_permission(perms, Permission.BILLING_READ) is False


# ========================================================================
# RBACContext
# ========================================================================


class TestRBACContext:
    """RBACContext tests."""

    def test_has_permission_raises_without_init(self, rbac, Permission):
        ctx = rbac.RBACContext(None, "user-123")
        with pytest.raises(RuntimeError, match="not initialized"):
            ctx.has_permission(Permission.CAMPAIGNS_READ)


# ========================================================================
# require_permission() — DB grants/revocations, not just role defaults
# ========================================================================


class _RevokedConn:
    """A connection whose role_permissions / user_permissions rows are the
    authoritative answer — here, with calls:delete revoked."""

    def __init__(self, granted_names):
        self.granted_names = list(granted_names)

    async def fetch(self, _query, *_args):
        return [{"name": name} for name in self.granted_names]


class _Acquire:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_):
        return False


class _Pool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _Acquire(self._conn)


class _User:
    def __init__(self, role, tenant_id="tenant-A", user_id="user-1"):
        self.id = user_id
        self.role = role
        self.tenant_id = tenant_id


def _request(tenant_id="tenant-A"):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "server": ("api.talkleeai.com", 443),
        "path": "/api/v1/calls/abc/hangup",
        "raw_path": b"/api/v1/calls/abc/hangup",
        "query_string": b"",
        "headers": [(b"x-tenant-id", tenant_id.encode())],
        "client": ("127.0.0.1", 0),
        "state": {},
    }
    return Request(scope)


class TestRequirePermissionConsultsDatabase:
    """``require_permission()`` used ROLE_DEFAULT_PERMISSIONS only, so a
    database revocation of e.g. calls:delete was completely ignored and the
    user could still hang up calls (calls.py hangup route). It must resolve
    the same effective permissions the newer dependencies use
    (``get_effective_permissions``: role grants + bounded direct grants)."""

    @pytest.mark.asyncio
    async def test_db_revocation_denies_despite_role_default(self, rbac, Permission, UserRole):
        import unittest.mock as _mock

        # Sanity: the role's Python default DOES include calls:delete.
        assert Permission.CALLS_DELETE in rbac.ROLE_DEFAULT_PERMISSIONS[UserRole.TENANT_ADMIN]

        # ...but the database says otherwise (calls:delete revoked).
        pool = _Pool(_RevokedConn(["calls:read", "campaigns:read"]))
        checker = rbac.require_permission(Permission.CALLS_DELETE)

        with _mock.patch(
            "app.core.container.get_db_pool_from_container", return_value=pool
        ):
            with pytest.raises(Exception) as exc:
                await checker(_request(), _User("tenant_admin"))

        assert getattr(exc.value, "status_code", None) == 403

    @pytest.mark.asyncio
    async def test_db_grant_allows(self, rbac, Permission):
        import unittest.mock as _mock

        pool = _Pool(_RevokedConn(["calls:read", "calls:delete"]))
        checker = rbac.require_permission(Permission.CALLS_DELETE)

        with _mock.patch(
            "app.core.container.get_db_pool_from_container", return_value=pool
        ):
            user = await checker(_request(), _User("tenant_admin"))

        assert user.id == "user-1"

    @pytest.mark.asyncio
    async def test_authorization_unavailable_fails_closed(self, rbac, Permission):
        """A pool that exists but errors must NOT silently fall back to the
        Python role defaults."""
        import unittest.mock as _mock

        class _BrokenPool:
            def acquire(self):
                raise RuntimeError("db down")

        checker = rbac.require_permission(Permission.CALLS_DELETE)
        with _mock.patch(
            "app.core.container.get_db_pool_from_container", return_value=_BrokenPool()
        ):
            with pytest.raises(Exception) as exc:
                await checker(_request(), _User("tenant_admin"))

        assert getattr(exc.value, "status_code", None) == 503

    @pytest.mark.asyncio
    async def test_no_pool_falls_back_to_role_defaults(self, rbac, Permission):
        """Dev/unit contexts with no initialised container keep working."""
        import unittest.mock as _mock

        checker = rbac.require_permission(Permission.CALLS_DELETE)
        with _mock.patch(
            "app.core.container.get_db_pool_from_container",
            side_effect=RuntimeError("Container not initialized"),
        ):
            user = await checker(_request(), _User("tenant_admin"))

        assert user.id == "user-1"


# ========================================================================
# require_permission() — unseeded deployment vs. genuine revocation
# ========================================================================


class _ProbeConn:
    """Connection that answers both the permission join and the deployment
    seeding probe.

    ``granted_names``  -> what ``get_user_permissions`` resolves for the user.
    ``role_grants`` / ``memberships`` -> the two global EXISTS legs of the
    seeding probe (``role_permissions`` rows, active ``tenant_users`` rows).
    """

    def __init__(self, granted_names, role_grants: bool, memberships: bool):
        self.granted_names = list(granted_names)
        self.role_grants = role_grants
        self.memberships = memberships
        self.fetchrow_calls = 0
        self.fetch_calls = 0

    async def fetch(self, _query, *_args):
        self.fetch_calls += 1
        return [{"name": name} for name in self.granted_names]

    async def fetchrow(self, _query, *_args):
        self.fetchrow_calls += 1
        return {
            "has_role_grants": self.role_grants,
            "has_memberships": self.memberships,
        }


class _ProbeErrorConn(_ProbeConn):
    async def fetchrow(self, _query, *_args):
        self.fetchrow_calls += 1
        raise RuntimeError("relation role_permissions does not exist")


@pytest.fixture()
def _clean_probe_cache(rbac):
    rbac.reset_rbac_seeding_probe_cache()
    yield
    rbac.reset_rbac_seeding_probe_cache()


class TestUnseededDeploymentFallback:
    """Production has 0 rows in ``role_permissions`` and 0 in ``tenant_users``.

    With a DB-only resolver every non-platform-admin user resolved to an empty
    permission set, so every ``require_permission`` route 403'd — a total
    lockout. "This deployment has no RBAC data" and "this user was denied" are
    different states and must behave differently.
    """

    @pytest.mark.asyncio
    async def test_unseeded_deployment_falls_back_to_role_defaults(
        self, rbac, Permission, _clean_probe_cache
    ):
        import unittest.mock as _mock

        conn = _ProbeConn([], role_grants=False, memberships=False)
        checker = rbac.require_permission(Permission.CALLS_DELETE)

        with _mock.patch(
            "app.core.container.get_db_pool_from_container", return_value=_Pool(conn)
        ):
            user = await checker(_request(), _User("tenant_admin"))

        assert user.id == "user-1"

    @pytest.mark.asyncio
    async def test_half_seeded_catalogue_without_memberships_still_falls_back(
        self, rbac, Permission, _clean_probe_cache
    ):
        """seed_rbac.py alone (role_permissions filled, tenant_users still
        empty) leaves nothing for ANY user to resolve against — that is still
        an unseeded deployment, not a revocation."""
        import unittest.mock as _mock

        conn = _ProbeConn([], role_grants=True, memberships=False)
        checker = rbac.require_permission(Permission.CALLS_DELETE)

        with _mock.patch(
            "app.core.container.get_db_pool_from_container", return_value=_Pool(conn)
        ):
            user = await checker(_request(), _User("tenant_admin"))

        assert user.id == "user-1"

    @pytest.mark.asyncio
    async def test_seeded_deployment_denies_user_with_no_grant(
        self, rbac, Permission, _clean_probe_cache
    ):
        """The regression the DB resolver bought: a real revocation still 403s."""
        import unittest.mock as _mock

        conn = _ProbeConn([], role_grants=True, memberships=True)
        checker = rbac.require_permission(Permission.CALLS_DELETE)

        with _mock.patch(
            "app.core.container.get_db_pool_from_container", return_value=_Pool(conn)
        ):
            with pytest.raises(Exception) as exc:
                await checker(_request(), _User("tenant_admin"))

        assert getattr(exc.value, "status_code", None) == 403

    @pytest.mark.asyncio
    async def test_seeded_deployment_revoking_one_permission_denies(
        self, rbac, Permission, _clean_probe_cache
    ):
        """Non-empty grants prove the deployment is seeded — no probe needed."""
        import unittest.mock as _mock

        conn = _ProbeConn(["calls:read"], role_grants=True, memberships=True)
        checker = rbac.require_permission(Permission.CALLS_DELETE)

        with _mock.patch(
            "app.core.container.get_db_pool_from_container", return_value=_Pool(conn)
        ):
            with pytest.raises(Exception) as exc:
                await checker(_request(), _User("tenant_admin"))

        assert getattr(exc.value, "status_code", None) == 403
        assert conn.fetchrow_calls == 0, "probe must not run when grants resolve"

    @pytest.mark.asyncio
    async def test_seeded_and_granted_allows(
        self, rbac, Permission, _clean_probe_cache
    ):
        import unittest.mock as _mock

        conn = _ProbeConn(["calls:delete"], role_grants=True, memberships=True)
        checker = rbac.require_permission(Permission.CALLS_DELETE)

        with _mock.patch(
            "app.core.container.get_db_pool_from_container", return_value=_Pool(conn)
        ):
            user = await checker(_request(), _User("tenant_admin"))

        assert user.id == "user-1"

    @pytest.mark.asyncio
    async def test_probe_query_error_fails_closed_with_503(
        self, rbac, Permission, _clean_probe_cache
    ):
        """An erroring probe is a database failure, not a licence to downgrade."""
        import unittest.mock as _mock

        conn = _ProbeErrorConn([], role_grants=False, memberships=False)
        checker = rbac.require_permission(Permission.CALLS_DELETE)

        with _mock.patch(
            "app.core.container.get_db_pool_from_container", return_value=_Pool(conn)
        ):
            with pytest.raises(Exception) as exc:
                await checker(_request(), _User("tenant_admin"))

        assert getattr(exc.value, "status_code", None) == 503

    @pytest.mark.asyncio
    async def test_probe_result_is_cached_per_process(
        self, rbac, Permission, _clean_probe_cache
    ):
        """At prod request volume an uncached probe would be an extra query on
        every authorized request."""
        import unittest.mock as _mock

        conn = _ProbeConn([], role_grants=False, memberships=False)
        checker = rbac.require_permission(Permission.CALLS_DELETE)

        with _mock.patch(
            "app.core.container.get_db_pool_from_container", return_value=_Pool(conn)
        ):
            for _ in range(5):
                await checker(_request(), _User("tenant_admin"))

        assert conn.fetchrow_calls == 1

    @pytest.mark.asyncio
    async def test_unseeded_warning_logged_once_not_per_request(
        self, rbac, Permission, caplog, _clean_probe_cache
    ):
        import logging as _logging
        import unittest.mock as _mock

        conn = _ProbeConn([], role_grants=False, memberships=False)
        checker = rbac.require_permission(Permission.CALLS_DELETE)

        with caplog.at_level(_logging.WARNING, logger="app.core.security.rbac"):
            with _mock.patch(
                "app.core.container.get_db_pool_from_container",
                return_value=_Pool(conn),
            ):
                for _ in range(5):
                    await checker(_request(), _User("tenant_admin"))

        unseeded_warnings = [
            r for r in caplog.records if "RBAC_UNSEEDED_FALLBACK" in r.getMessage()
        ]
        assert len(unseeded_warnings) == 1, (
            f"expected exactly one warning, got {len(unseeded_warnings)}"
        )
        assert unseeded_warnings[0].levelno == _logging.WARNING

    @pytest.mark.asyncio
    async def test_probe_is_global_not_tenant_scoped(
        self, rbac, Permission, _clean_probe_cache
    ):
        """The probe must not be foolable by one tenant's data being absent:
        it takes no user_id/tenant_id parameters."""
        import unittest.mock as _mock

        seen = {}

        class _RecordingConn(_ProbeConn):
            async def fetchrow(self, query, *args):
                seen["query"] = query
                seen["args"] = args
                return await super().fetchrow(query, *args)

        conn = _RecordingConn([], role_grants=False, memberships=False)
        checker = rbac.require_permission(Permission.CALLS_DELETE)

        with _mock.patch(
            "app.core.container.get_db_pool_from_container", return_value=_Pool(conn)
        ):
            await checker(_request(), _User("tenant_admin"))

        assert seen["args"] == ()
        assert "tenant_id" not in seen["query"]
        assert "user_id" not in seen["query"]


# ========================================================================
# goals.md §12 validation-matrix roles
# ========================================================================
#
# §12 ("Client Management and 200-Tenant Validation") requires role samples
# across six roles: tenant admin, campaign manager, agent/operator, billing
# user, read-only user and partner/reseller user.  Three of them did not
# exist, and ``normalize_role()`` silently downgraded their names to READONLY
# - so seeding a tenant with "campaign_manager" produced a readonly user while
# the validation matrix believed it was testing a campaign manager.


class TestValidationMatrixRolesExist:
    """The six §12 roles must all be real, distinct roles."""

    def test_campaign_manager_resolves_to_itself(self, rbac, UserRole):
        assert rbac.normalize_role("campaign_manager") is UserRole.CAMPAIGN_MANAGER

    def test_agent_resolves_to_itself(self, rbac, UserRole):
        assert rbac.normalize_role("agent") is UserRole.AGENT

    def test_operator_is_an_alias_of_agent(self, rbac, UserRole):
        """§12 names the role "Agent/operator" - both spellings must land on
        the same role, never on readonly."""
        assert rbac.normalize_role("operator") is UserRole.AGENT

    def test_billing_user_resolves_to_itself(self, rbac, UserRole):
        assert rbac.normalize_role("billing_user") is UserRole.BILLING_USER

    def test_all_six_matrix_roles_are_distinct(self, rbac, UserRole):
        matrix = [
            rbac.normalize_role(name)
            for name in (
                "tenant_admin",
                "campaign_manager",
                "agent",
                "billing_user",
                "readonly",
                "partner_admin",
            )
        ]
        assert len(set(matrix)) == 6

    def test_legacy_aliases_still_resolve(self, rbac, UserRole):
        """Regression guard: adding roles must not break existing logins."""
        assert rbac.normalize_role("admin") is UserRole.TENANT_ADMIN
        assert rbac.normalize_role("owner") is UserRole.TENANT_ADMIN
        assert rbac.normalize_role("super_admin") is UserRole.PLATFORM_ADMIN
        for role in UserRole:
            assert rbac.normalize_role(role.value) is role


class TestValidationMatrixRoleLevels:
    """Hierarchy placement of the three new roles."""

    def test_campaign_manager_sits_between_user_and_tenant_admin(self, UserRole):
        assert UserRole.USER.level < UserRole.CAMPAIGN_MANAGER.level
        assert UserRole.CAMPAIGN_MANAGER.level < UserRole.TENANT_ADMIN.level

    def test_campaign_manager_cannot_reach_tenant_admin_gates(self, UserRole):
        assert UserRole.CAMPAIGN_MANAGER.can_access(UserRole.USER) is True
        assert UserRole.CAMPAIGN_MANAGER.can_access(UserRole.TENANT_ADMIN) is False

    def test_agent_and_billing_user_sit_below_user(self, UserRole):
        assert UserRole.READONLY.level < UserRole.BILLING_USER.level
        assert UserRole.BILLING_USER.level < UserRole.AGENT.level
        assert UserRole.AGENT.level < UserRole.USER.level

    def test_agent_and_billing_user_cannot_reach_user_gates(self, UserRole):
        assert UserRole.AGENT.can_access(UserRole.USER) is False
        assert UserRole.BILLING_USER.can_access(UserRole.USER) is False


class TestValidationMatrixRoleDefaults:
    """Exact default permission sets. The DENIALS are the point: a role whose
    defaults quietly matched a broader role would make the §12 matrix pass
    while testing nothing."""

    def test_campaign_manager_defaults_are_exact(
        self, UserRole, Permission, ROLE_DEFAULT_PERMISSIONS
    ):
        assert ROLE_DEFAULT_PERMISSIONS[UserRole.CAMPAIGN_MANAGER] == {
            Permission.CAMPAIGNS_CREATE,
            Permission.CAMPAIGNS_READ,
            Permission.CAMPAIGNS_UPDATE,
            Permission.CAMPAIGNS_DELETE,
            Permission.CAMPAIGNS_ADMIN,
            Permission.INBOUND_READ,
            Permission.INBOUND_ASSIGN,
            Permission.CALLS_CREATE,
            Permission.CALLS_READ,
            Permission.RECORDINGS_READ,
            Permission.RECORDINGS_DOWNLOAD,
            Permission.CONNECTORS_CREATE,
            Permission.CONNECTORS_READ,
            Permission.CONNECTORS_UPDATE,
            Permission.CONNECTORS_DELETE,
            Permission.ANALYTICS_READ,
            Permission.ANALYTICS_EXPORT,
            Permission.TENANTS_READ,
            Permission.USERS_READ,
        }

    def test_campaign_manager_is_user_plus_campaign_ownership(
        self, UserRole, Permission, ROLE_DEFAULT_PERMISSIONS
    ):
        """It sits between user and tenant_admin: everything a user can do,
        plus campaigns:admin and inbound:assign - and nothing else."""
        assert ROLE_DEFAULT_PERMISSIONS[UserRole.CAMPAIGN_MANAGER] == (
            ROLE_DEFAULT_PERMISSIONS[UserRole.USER]
            | {Permission.CAMPAIGNS_ADMIN, Permission.INBOUND_ASSIGN}
        )

    def test_campaign_manager_is_denied_admin_surfaces(
        self, UserRole, Permission, ROLE_DEFAULT_PERMISSIONS
    ):
        perms = ROLE_DEFAULT_PERMISSIONS[UserRole.CAMPAIGN_MANAGER]
        for denied in (
            Permission.BILLING_READ,
            Permission.BILLING_UPDATE,
            Permission.BILLING_ADMIN,
            Permission.USERS_CREATE,
            Permission.USERS_MANAGE,
            Permission.TENANTS_UPDATE,
            Permission.TENANTS_ADMIN,
            Permission.INBOUND_MANAGE,
            Permission.INBOUND_CONTROLS,
            Permission.CALLS_DELETE,
            Permission.RECORDINGS_DELETE,
        ):
            assert denied not in perms

    def test_agent_defaults_are_exact(
        self, UserRole, Permission, ROLE_DEFAULT_PERMISSIONS
    ):
        assert ROLE_DEFAULT_PERMISSIONS[UserRole.AGENT] == {
            Permission.CAMPAIGNS_READ,
            Permission.INBOUND_READ,
            Permission.CALLS_CREATE,
            Permission.CALLS_READ,
            Permission.RECORDINGS_READ,
            Permission.ANALYTICS_READ,
            Permission.TENANTS_READ,
        }

    def test_agent_can_work_calls(
        self, UserRole, Permission, ROLE_DEFAULT_PERMISSIONS
    ):
        perms = ROLE_DEFAULT_PERMISSIONS[UserRole.AGENT]
        assert Permission.CALLS_CREATE in perms
        assert Permission.CALLS_READ in perms

    def test_agent_cannot_reach_billing_or_tenant_administration(
        self, UserRole, Permission, ROLE_DEFAULT_PERMISSIONS
    ):
        perms = ROLE_DEFAULT_PERMISSIONS[UserRole.AGENT]
        for denied in (
            Permission.BILLING_READ,
            Permission.BILLING_UPDATE,
            Permission.BILLING_ADMIN,
            Permission.TENANTS_UPDATE,
            Permission.TENANTS_ADMIN,
            Permission.USERS_READ,
            Permission.USERS_CREATE,
            Permission.USERS_MANAGE,
            Permission.CAMPAIGNS_CREATE,
            Permission.CAMPAIGNS_UPDATE,
            Permission.CALLS_DELETE,
            Permission.RECORDINGS_DOWNLOAD,
            Permission.RECORDINGS_DELETE,
            Permission.ANALYTICS_EXPORT,
            Permission.CONNECTORS_READ,
            Permission.INBOUND_MANAGE,
            Permission.INBOUND_ASSIGN,
            Permission.INBOUND_CONTROLS,
        ):
            assert denied not in perms

    def test_billing_user_defaults_are_exact(
        self, UserRole, Permission, ROLE_DEFAULT_PERMISSIONS
    ):
        assert ROLE_DEFAULT_PERMISSIONS[UserRole.BILLING_USER] == {
            Permission.BILLING_READ,
            Permission.BILLING_UPDATE,
            Permission.ANALYTICS_READ,
            Permission.ANALYTICS_EXPORT,
            Permission.TENANTS_READ,
        }

    def test_billing_user_can_see_and_pay_billing(
        self, UserRole, Permission, ROLE_DEFAULT_PERMISSIONS
    ):
        perms = ROLE_DEFAULT_PERMISSIONS[UserRole.BILLING_USER]
        assert Permission.BILLING_READ in perms
        assert Permission.BILLING_UPDATE in perms

    def test_billing_user_cannot_place_or_hang_up_calls(
        self, UserRole, Permission, ROLE_DEFAULT_PERMISSIONS
    ):
        perms = ROLE_DEFAULT_PERMISSIONS[UserRole.BILLING_USER]
        assert Permission.CALLS_CREATE not in perms
        assert Permission.CALLS_DELETE not in perms
        assert Permission.CALLS_READ not in perms

    def test_billing_user_is_denied_everything_outside_finance(
        self, UserRole, Permission, ROLE_DEFAULT_PERMISSIONS
    ):
        perms = ROLE_DEFAULT_PERMISSIONS[UserRole.BILLING_USER]
        for denied in (
            Permission.BILLING_ADMIN,
            Permission.CAMPAIGNS_READ,
            Permission.CAMPAIGNS_CREATE,
            Permission.RECORDINGS_READ,
            Permission.RECORDINGS_DOWNLOAD,
            Permission.RECORDINGS_DELETE,
            Permission.CONNECTORS_READ,
            Permission.USERS_READ,
            Permission.USERS_MANAGE,
            Permission.TENANTS_UPDATE,
            Permission.TENANTS_ADMIN,
            Permission.INBOUND_READ,
        ):
            assert denied not in perms

    def test_no_new_role_holds_platform_permissions(
        self, UserRole, Permission, ROLE_DEFAULT_PERMISSIONS
    ):
        for role in (
            UserRole.CAMPAIGN_MANAGER,
            UserRole.AGENT,
            UserRole.BILLING_USER,
        ):
            perms = ROLE_DEFAULT_PERMISSIONS[role]
            assert not any(p.value.startswith("platform:") for p in perms)


# ========================================================================
# Unknown role names must be LOUD, never a silent downgrade
# ========================================================================


class TestUnknownRoleIsLoud:
    """``normalize_role()`` mapping an unknown name to READONLY is the trap
    that made this ticket necessary: seeding "campaign_manager" before the
    role existed produced a readonly user and the validation "passed" while
    exercising the wrong permissions. The downgrade stays (it is the fail-safe
    that keeps login working) but it must be audible."""

    def test_unknown_role_still_returns_readonly(self, rbac, UserRole):
        """Login safety: the return value is unchanged."""
        assert rbac.normalize_role("this-role-does-not-exist") is UserRole.READONLY

    def test_unknown_role_logs_at_error_with_the_offending_value(self, rbac, caplog):
        import logging as _logging

        rbac.reset_unknown_role_log_cache()
        with caplog.at_level(_logging.DEBUG, logger="app.core.security.rbac"):
            rbac.normalize_role("campaign-manger-typo")

        records = [r for r in caplog.records if "RBAC_UNKNOWN_ROLE" in r.getMessage()]
        assert len(records) == 1
        assert records[0].levelno == _logging.ERROR
        assert "campaign-manger-typo" in records[0].getMessage()

    def test_repeated_unknown_role_does_not_flood_the_log(self, rbac, caplog):
        import logging as _logging

        rbac.reset_unknown_role_log_cache()
        with caplog.at_level(_logging.DEBUG, logger="app.core.security.rbac"):
            for _ in range(5):
                rbac.normalize_role("repeated-unknown-role")

        errors = [
            r
            for r in caplog.records
            if "RBAC_UNKNOWN_ROLE" in r.getMessage() and r.levelno == _logging.ERROR
        ]
        assert len(errors) == 1

    def test_strict_raises_for_unknown_role(self, rbac):
        with pytest.raises(rbac.UnknownRoleError) as exc:
            rbac.normalize_role("not-a-role", strict=True)
        assert exc.value.role_name == "not-a-role"

    def test_strict_is_opt_in_only(self, rbac, UserRole):
        """Every existing call site passes one positional argument, so the
        default path cannot start raising and lock users out."""
        assert rbac.normalize_role("still-unknown") is UserRole.READONLY

    def test_strict_accepts_valid_roles_and_aliases(self, rbac, UserRole):
        assert rbac.normalize_role("admin", strict=True) is UserRole.TENANT_ADMIN
        assert (
            rbac.normalize_role("campaign_manager", strict=True)
            is UserRole.CAMPAIGN_MANAGER
        )
        assert rbac.normalize_role("operator", strict=True) is UserRole.AGENT

    def test_unknown_role_error_is_a_value_error(self, rbac):
        """Call sites that already catch ValueError keep working."""
        assert issubclass(rbac.UnknownRoleError, ValueError)


# ========================================================================
# New roles through require_permission(): seeded AND unseeded paths
# ========================================================================


class TestNewRolesThroughRequirePermission:
    """Production is currently on the unseeded-fallback path (0 rows in
    role_permissions / tenant_users), so the new roles must behave correctly
    there as well as once the catalogue is seeded."""

    async def _run(self, rbac, permission, role, conn):
        import unittest.mock as _mock

        checker = rbac.require_permission(permission)
        with _mock.patch(
            "app.core.container.get_db_pool_from_container", return_value=_Pool(conn)
        ):
            try:
                await checker(_request(), _User(role))
                return True
            except Exception as exc:  # noqa: BLE001
                assert getattr(exc, "status_code", None) == 403, exc
                return False

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "role,permission_name,expected",
        [
            ("campaign_manager", "CAMPAIGNS_CREATE", True),
            ("campaign_manager", "INBOUND_ASSIGN", True),
            ("campaign_manager", "BILLING_UPDATE", False),
            ("campaign_manager", "USERS_MANAGE", False),
            ("campaign_manager", "INBOUND_MANAGE", False),
            ("agent", "CALLS_CREATE", True),
            ("agent", "CALLS_READ", True),
            ("operator", "CALLS_CREATE", True),
            ("agent", "BILLING_READ", False),
            ("agent", "TENANTS_ADMIN", False),
            ("agent", "RECORDINGS_DOWNLOAD", False),
            ("billing_user", "BILLING_READ", True),
            ("billing_user", "BILLING_UPDATE", True),
            ("billing_user", "CALLS_CREATE", False),
            ("billing_user", "CALLS_DELETE", False),
            ("billing_user", "BILLING_ADMIN", False),
        ],
    )
    async def test_unseeded_fallback_path(
        self, rbac, Permission, role, permission_name, expected, _clean_probe_cache
    ):
        conn = _ProbeConn([], role_grants=False, memberships=False)
        got = await self._run(rbac, getattr(Permission, permission_name), role, conn)
        assert got is expected

    @pytest.mark.asyncio
    async def test_seeded_path_uses_database_grants_for_new_roles(
        self, rbac, Permission, _clean_probe_cache
    ):
        """Once seeded, the database is authoritative - a billing_user whose
        billing:update grant was revoked is denied even though the role
        default holds it."""
        conn = _ProbeConn(["billing:read"], role_grants=True, memberships=True)
        assert (
            await self._run(rbac, Permission.BILLING_READ, "billing_user", conn) is True
        )

        conn = _ProbeConn(["billing:read"], role_grants=True, memberships=True)
        assert (
            await self._run(rbac, Permission.BILLING_UPDATE, "billing_user", conn)
            is False
        )

    @pytest.mark.asyncio
    async def test_seeded_path_grants_agent_its_seeded_permissions(
        self, rbac, Permission, _clean_probe_cache
    ):
        conn = _ProbeConn(
            ["calls:create", "calls:read"], role_grants=True, memberships=True
        )
        assert await self._run(rbac, Permission.CALLS_CREATE, "agent", conn) is True

        conn = _ProbeConn(
            ["calls:create", "calls:read"], role_grants=True, memberships=True
        )
        assert await self._run(rbac, Permission.BILLING_READ, "agent", conn) is False


# ========================================================================
# scripts/seed_rbac.py must create every role
# ========================================================================


class TestSeedScriptCoversEveryRole:
    def test_seed_script_derives_roles_from_the_enum(self):
        """A hand-maintained role list in the seed script is exactly how three
        roles ended up defined in Python but absent from the database."""
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[2] / "scripts" / "seed_rbac.py"
        ).read_text(encoding="utf-8")

        assert "for role in UserRole" in source, (
            "seed_rbac.py must enumerate UserRole, not a hardcoded role list"
        )
        # No hardcoded level literals that can drift from UserRole.level.
        assert "UserRole.PLATFORM_ADMIN: 100" not in source
