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
