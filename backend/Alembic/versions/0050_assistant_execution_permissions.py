"""Separate communication execution from connector configuration permissions."""
from alembic import op

revision = "0050_assistant_execution_perms"
down_revision = "0049_lead_detail_evidence"
branch_labels = None
depends_on = None

def upgrade():
    op.execute("""
        INSERT INTO permissions (name, description, resource, action, is_system)
        VALUES
            ('email:send', 'Send email through tenant integrations', 'email', 'send', true),
            ('sms:send', 'Send SMS messages', 'sms', 'send', true),
            ('calendar:read', 'Read calendar availability', 'calendar', 'read', true),
            ('calendar:manage', 'Create, update, cancel calendar events', 'calendar', 'manage', true),
            ('reminders:manage', 'Schedule communications reminders', 'reminders', 'manage', true),
            ('support:report', 'Submit a report to configured support', 'support', 'report', true)
        ON CONFLICT (name) DO UPDATE SET description=EXCLUDED.description;
    """)
    op.execute("""
        INSERT INTO role_permissions (role_id, permission_id)
        SELECT r.id, p.id FROM roles r CROSS JOIN permissions p
        WHERE r.name IN ('tenant_admin','partner_admin','platform_admin')
          AND p.name IN ('email:send','sms:send','calendar:read','calendar:manage','reminders:manage','support:report')
        ON CONFLICT DO NOTHING;
    """)

def downgrade():
    # Keep explicit grants and audit history during rollback.
    pass
