"""Shared recording read boundaries and current tenant compliance holds (F12-06)."""
from __future__ import annotations

import os
from typing import Any


class RecordingPathOutsideRoot(ValueError):
    """A recording key resolves outside the configured storage boundary."""


def validated_recording_path(raw_path: str) -> str:
    """Resolve a local object key only inside the configured recording root."""

    # abspath/commonpath alone is vulnerable to an in-root symlink or Windows
    # junction whose target is outside the storage directory. Resolve both the
    # configured root and candidate through the filesystem before comparing.
    root = os.path.realpath(
        os.path.abspath(os.getenv("LOCAL_RECORDINGS_DIR", "./recordings"))
    )
    path = os.path.realpath(os.path.abspath(raw_path))
    try:
        inside = os.path.commonpath((root, path)) == root
    except ValueError:
        inside = False
    if not inside:
        raise RecordingPathOutsideRoot("Recording path is outside storage root")
    return path


ACTIVE_COMPLIANCE_HOLD_SQL = """
    EXISTS (
        SELECT 1
        FROM suspension_events se
        WHERE se.suspension_type = 'COMPLIANCE'
          AND se.is_active = TRUE
          AND se.restored_at IS NULL
          AND (se.suspended_until IS NULL OR se.suspended_until > NOW())
          AND (
              (se.target_type = 'tenant'
               AND se.target_id = {tenant_expression})
              OR (
                  se.target_type = 'partner'
                  AND EXISTS (
                      SELECT 1
                      FROM tenants held_tenant
                      WHERE held_tenant.id = {tenant_expression}
                        AND held_tenant.white_label_partner_id = se.target_id
                  )
              )
          )
    )
"""


async def has_active_compliance_hold(conn: Any, tenant_id: Any) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT " + ACTIVE_COMPLIANCE_HOLD_SQL.format(tenant_expression="$1"),
            tenant_id,
        )
    )
