"""granular recording access permissions

Revision ID: 0024_recording_permissions
Revises: 0023_admin_media_deletion_safety
Create Date: 2026-08-26 00:00:00.000000

Playback, download, and irreversible deletion are separate grants so operators
can revoke export or erasure without hiding tenant-owned recording metadata.
The seed is additive and idempotent for installations that already received
equivalent rows through a bootstrap or manual rollout.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

revision: str = "0024_recording_permissions"
down_revision: str | None = "0023_admin_media_deletion_safety"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        text(
            """
            INSERT INTO permissions (
                name, description, resource, action, is_system
            )
            VALUES
                ('recordings:read', 'View and play call recordings',
                    'recordings', 'read', TRUE),
                ('recordings:download', 'Download call recordings',
                    'recordings', 'download', TRUE),
                ('recordings:delete', 'Permanently delete call recordings',
                    'recordings', 'delete', TRUE)
            ON CONFLICT (name) DO UPDATE
            SET description = EXCLUDED.description,
                resource = EXCLUDED.resource,
                action = EXCLUDED.action,
                is_system = TRUE
            """
        )
    )
    op.execute(
        text(
            """
            WITH recording_grants(role_name, permission_name) AS (
                VALUES
                    ('readonly', 'recordings:read'),
                    ('user', 'recordings:read'),
                    ('user', 'recordings:download'),
                    ('tenant_admin', 'recordings:read'),
                    ('tenant_admin', 'recordings:download'),
                    ('tenant_admin', 'recordings:delete'),
                    ('partner_admin', 'recordings:read'),
                    ('partner_admin', 'recordings:download'),
                    ('partner_admin', 'recordings:delete'),
                    ('platform_admin', 'recordings:read'),
                    ('platform_admin', 'recordings:download'),
                    ('platform_admin', 'recordings:delete')
            )
            INSERT INTO role_permissions (role_id, permission_id)
            SELECT r.id, p.id
            FROM recording_grants g
            JOIN roles r ON r.name = g.role_name
            JOIN permissions p ON p.name = g.permission_name
            ON CONFLICT (role_id, permission_id) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    # Deliberately retain these permission definitions and grants. The fresh
    # schema owns them too, and deleting a permission would cascade bounded
    # user_permissions grants created after this revision was applied.
    pass
