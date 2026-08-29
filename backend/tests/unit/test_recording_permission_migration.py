"""Drift guards for recording RBAC seeds and their Alembic rollout."""

from __future__ import annotations

import ast
import re
from pathlib import Path


_BACKEND = Path(__file__).resolve().parents[2]
_MIGRATION = _BACKEND / "Alembic" / "versions" / "0024_recording_permissions.py"
_COMPLETE_SCHEMA = _BACKEND / "database" / "complete_schema.sql"
_RBAC = _BACKEND / "app" / "core" / "security" / "rbac.py"

_PERMISSIONS = {
    "recordings:read": "read",
    "recordings:download": "download",
    "recordings:delete": "delete",
}
_GRANTS = {
    ("readonly", "recordings:read"),
    ("user", "recordings:read"),
    ("user", "recordings:download"),
    ("tenant_admin", "recordings:read"),
    ("tenant_admin", "recordings:download"),
    ("tenant_admin", "recordings:delete"),
    ("partner_admin", "recordings:read"),
    ("partner_admin", "recordings:download"),
    ("partner_admin", "recordings:delete"),
    ("platform_admin", "recordings:read"),
    ("platform_admin", "recordings:download"),
    ("platform_admin", "recordings:delete"),
}
_PERMISSION_ROW = re.compile(
    r"\('(?P<name>recordings:(?:read|download|delete))',\s*"
    r"'[^']+',\s*'recordings',\s*'(?P<action>read|download|delete)',\s*TRUE\)"
)
_GRANT_ROW = re.compile(
    r"\('(?P<role>readonly|user|tenant_admin|partner_admin|platform_admin)',\s*"
    r"'(?P<permission>recordings:(?:read|download|delete))'\)"
)


def _permission_rows(source: str) -> dict[str, str]:
    return {
        match.group("name"): match.group("action")
        for match in _PERMISSION_ROW.finditer(source)
    }


def _recording_grants(source: str) -> set[tuple[str, str]]:
    start = source.index("WITH recording_grants(role_name, permission_name) AS")
    end = source.index(
        "ON CONFLICT (role_id, permission_id) DO NOTHING",
        start,
    )
    return {
        (match.group("role"), match.group("permission"))
        for match in _GRANT_ROW.finditer(source[start:end])
    }


def _rbac_recording_contract(source: str) -> tuple[dict[str, str], dict[str, set[str]]]:
    tree = ast.parse(source)
    enum_values: dict[str, str] = {}
    role_defaults: dict[str, set[str]] = {}

    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Permission":
            for member in node.body:
                if (
                    isinstance(member, ast.Assign)
                    and len(member.targets) == 1
                    and isinstance(member.targets[0], ast.Name)
                    and member.targets[0].id.startswith("RECORDINGS_")
                    and isinstance(member.value, ast.Constant)
                    and isinstance(member.value.value, str)
                ):
                    enum_values[member.targets[0].id] = member.value.value

        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "ROLE_DEFAULT_PERMISSIONS"
            and isinstance(node.value, ast.Dict)
        ):
            for role_node, permissions_node in zip(node.value.keys, node.value.values):
                if not (
                    isinstance(role_node, ast.Attribute)
                    and isinstance(role_node.value, ast.Name)
                    and role_node.value.id == "UserRole"
                    and isinstance(permissions_node, ast.Set)
                ):
                    continue
                role_defaults[role_node.attr] = {
                    permission.attr
                    for permission in permissions_node.elts
                    if isinstance(permission, ast.Attribute)
                    and isinstance(permission.value, ast.Name)
                    and permission.value.id == "Permission"
                    and permission.attr.startswith("RECORDINGS_")
                }

    return enum_values, role_defaults


def test_rbac_enum_and_defaults_match_recording_rollout_matrix() -> None:
    enum_values, role_defaults = _rbac_recording_contract(
        _RBAC.read_text(encoding="utf-8")
    )

    assert enum_values == {
        "RECORDINGS_READ": "recordings:read",
        "RECORDINGS_DOWNLOAD": "recordings:download",
        "RECORDINGS_DELETE": "recordings:delete",
    }
    assert role_defaults == {
        "READONLY": {"RECORDINGS_READ"},
        # goals.md §12 validation-matrix roles. AGENT (the human operator) may
        # listen back to a call in-app but must not bulk-export recordings;
        # BILLING_USER never touches call audio at all.
        "AGENT": {"RECORDINGS_READ"},
        "BILLING_USER": set(),
        "USER": {"RECORDINGS_READ", "RECORDINGS_DOWNLOAD"},
        "CAMPAIGN_MANAGER": {"RECORDINGS_READ", "RECORDINGS_DOWNLOAD"},
        "TENANT_ADMIN": {
            "RECORDINGS_READ",
            "RECORDINGS_DOWNLOAD",
            "RECORDINGS_DELETE",
        },
        "PARTNER_ADMIN": {
            "RECORDINGS_READ",
            "RECORDINGS_DOWNLOAD",
            "RECORDINGS_DELETE",
        },
        "PLATFORM_ADMIN": {
            "RECORDINGS_READ",
            "RECORDINGS_DOWNLOAD",
            "RECORDINGS_DELETE",
        },
    }


def test_0024_chains_from_0023_and_is_idempotent() -> None:
    source = _MIGRATION.read_text(encoding="utf-8")

    assert 'revision: str = "0024_recording_permissions"' in source
    assert 'down_revision: str | None = "0023_admin_media_deletion_safety"' in source
    assert len("0024_recording_permissions") <= 32
    assert "ON CONFLICT (name) DO UPDATE" in source
    assert "ON CONFLICT (role_id, permission_id) DO NOTHING" in source


def test_0024_seeds_exact_recording_permissions_and_role_matrix() -> None:
    source = _MIGRATION.read_text(encoding="utf-8")

    assert _permission_rows(source) == _PERMISSIONS
    assert _recording_grants(source) == _GRANTS


def test_complete_schema_matches_0024_recording_seed_and_grants() -> None:
    migration = _MIGRATION.read_text(encoding="utf-8")
    complete_schema = _COMPLETE_SCHEMA.read_text(encoding="utf-8")

    assert _permission_rows(complete_schema) == _permission_rows(migration) == _PERMISSIONS
    assert _recording_grants(complete_schema) == _recording_grants(migration) == _GRANTS
    assert "ON CONFLICT (name) DO NOTHING" in complete_schema
    assert "ON CONFLICT (role_id, permission_id) DO NOTHING" in complete_schema
