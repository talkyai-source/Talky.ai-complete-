"""Persist optional Universal-3.6 Pro settings without changing existing STT.

The explicit AssemblyAI integration request is the provider exception to the
production feature freeze. Existing Flux/Nova rows and tenant RLS stay intact.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0065_assemblyai_settings"
down_revision: Union[str, None] = "0064_dnc_phone_number_default"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UPGRADE_STATEMENTS = (
    "ALTER TABLE public.tenant_ai_configs ADD COLUMN assemblyai_settings JSONB",
    """ALTER TABLE public.tenant_ai_configs
       ADD CONSTRAINT tenant_ai_configs_assemblyai_settings_object
       CHECK (assemblyai_settings IS NULL OR jsonb_typeof(assemblyai_settings) = 'object')""",
    """ALTER TABLE public.tenant_ai_configs
       ADD CONSTRAINT tenant_ai_configs_assemblyai_selection
       CHECK (stt_engine <> 'assemblyai' OR (
           stt_provider = 'assemblyai' AND stt_model = 'universal-3-6-pro'
           AND stt_language = 'en'))""",
)


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    for statement in UPGRADE_STATEMENTS:
        op.execute(statement)


def downgrade() -> None:
    # Do not silently substitute another STT provider or discard saved controls.
    # A rollback to old code must first restore/export these rows explicitly.
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM public.tenant_ai_configs
                   WHERE stt_engine = 'assemblyai' OR assemblyai_settings IS NOT NULL)
        THEN RAISE EXCEPTION 'Export and clear AssemblyAI selections/settings before downgrading 0065';
        END IF;
    END $$""")
    op.execute("ALTER TABLE public.tenant_ai_configs DROP CONSTRAINT tenant_ai_configs_assemblyai_selection")
    op.execute("ALTER TABLE public.tenant_ai_configs DROP CONSTRAINT tenant_ai_configs_assemblyai_settings_object")
    op.execute("ALTER TABLE public.tenant_ai_configs DROP COLUMN assemblyai_settings")
