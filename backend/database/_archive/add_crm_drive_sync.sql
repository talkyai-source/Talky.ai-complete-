-- =============================================================================
-- Migration: Add CRM & Drive Sync Columns
-- Day 30: CRM & Drive Integration
-- =============================================================================

-- Leads table: CRM contact reference
ALTER TABLE leads 
ADD COLUMN IF NOT EXISTS crm_contact_id TEXT;

-- Add index for CRM contact lookups
CREATE INDEX IF NOT EXISTS idx_leads_crm_contact_id 
ON leads(crm_contact_id) WHERE crm_contact_id IS NOT NULL;

-- Calls table: CRM engagement references
ALTER TABLE calls
ADD COLUMN IF NOT EXISTS crm_call_id TEXT,
ADD COLUMN IF NOT EXISTS crm_note_id TEXT,
ADD COLUMN IF NOT EXISTS crm_synced_at TIMESTAMPTZ;

-- Add indexes for CRM lookups
CREATE INDEX IF NOT EXISTS idx_calls_crm_call_id 
ON calls(crm_call_id) WHERE crm_call_id IS NOT NULL;

-- Recordings table: Drive file reference
ALTER TABLE recordings
ADD COLUMN IF NOT EXISTS drive_file_id TEXT,
ADD COLUMN IF NOT EXISTS drive_web_link TEXT;

-- Add index for Drive file lookups
CREATE INDEX IF NOT EXISTS idx_recordings_drive_file_id 
ON recordings(drive_file_id) WHERE drive_file_id IS NOT NULL;

-- Transcripts table: Drive file reference
ALTER TABLE transcripts
ADD COLUMN IF NOT EXISTS drive_file_id TEXT,
ADD COLUMN IF NOT EXISTS drive_web_link TEXT;

-- Add index for Drive file lookups
CREATE INDEX IF NOT EXISTS idx_transcripts_drive_file_id 
ON transcripts(drive_file_id) WHERE drive_file_id IS NOT NULL;

-- Tenant settings: Drive folder preference
-- First check if tenant_settings table exists
DO $$
BEGIN
    IF EXISTS (
        SELECT FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_name = 'tenant_settings'
    ) THEN
        ALTER TABLE tenant_settings
        ADD COLUMN IF NOT EXISTS drive_root_folder_id TEXT;
    ELSE
        -- Create tenant_settings if it doesn't exist
        CREATE TABLE IF NOT EXISTS tenant_settings (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            auto_actions_enabled BOOLEAN DEFAULT FALSE,
            drive_root_folder_id TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(tenant_id)
        );
        
        -- Add index
        CREATE INDEX IF NOT EXISTS idx_tenant_settings_tenant_id 
        ON tenant_settings(tenant_id);
        
        -- Enable RLS
        ALTER TABLE tenant_settings ENABLE ROW LEVEL SECURITY;
        
        -- RLS policies
        CREATE POLICY "Users can view their tenant settings" ON tenant_settings
            FOR SELECT USING (
                tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
            );
        
        CREATE POLICY "Users can update their tenant settings" ON tenant_settings
            FOR UPDATE USING (
                tenant_id = (SELECT tenant_id FROM user_profiles WHERE id = auth.uid())
            );
        
        CREATE POLICY "Service role can manage all settings" ON tenant_settings
            FOR ALL USING (auth.role() = 'service_role');
    END IF;
END $$;

-- =============================================================================
-- Table Comments
-- =============================================================================
COMMENT ON COLUMN leads.crm_contact_id IS 'External CRM contact ID (e.g., HubSpot contact ID)';
COMMENT ON COLUMN calls.crm_call_id IS 'CRM call engagement ID';
COMMENT ON COLUMN calls.crm_note_id IS 'CRM note ID with Drive links';
COMMENT ON COLUMN calls.crm_synced_at IS 'When call was synced to CRM';
COMMENT ON COLUMN recordings.drive_file_id IS 'Google Drive file ID for recording';
COMMENT ON COLUMN recordings.drive_web_link IS 'Google Drive web link for recording';
COMMENT ON COLUMN transcripts.drive_file_id IS 'Google Drive file ID for transcript';
COMMENT ON COLUMN transcripts.drive_web_link IS 'Google Drive web link for transcript';

-- =============================================================================
-- Success Notification
-- =============================================================================
DO $$
BEGIN
    RAISE NOTICE '============================================================================';
    RAISE NOTICE 'Day 30 Migration: CRM & Drive Sync Complete!';
    RAISE NOTICE '';
    RAISE NOTICE 'New columns added:';
    RAISE NOTICE '  - leads.crm_contact_id';
    RAISE NOTICE '  - calls.crm_call_id, crm_note_id, crm_synced_at';
    RAISE NOTICE '  - recordings.drive_file_id, drive_web_link';
    RAISE NOTICE '  - transcripts.drive_file_id, drive_web_link';
    RAISE NOTICE '  - tenant_settings.drive_root_folder_id';
    RAISE NOTICE '============================================================================';
END $$;
