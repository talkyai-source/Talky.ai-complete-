ALTER TABLE assistant_conversations
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

ALTER TABLE assistant_conversations
    ALTER COLUMN updated_at SET DEFAULT NOW();

UPDATE assistant_conversations
SET updated_at = COALESCE(updated_at, last_message_at, started_at, created_at, NOW())
WHERE updated_at IS NULL;

ALTER TABLE assistant_conversations
    ALTER COLUMN updated_at SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_proc
        WHERE proname = 'update_updated_at_column'
    ) THEN
        CREATE FUNCTION update_updated_at_column()
        RETURNS TRIGGER
        LANGUAGE plpgsql
        AS $fn$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $fn$;
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_trigger
        WHERE tgname = 'update_assistant_conversations_updated_at'
          AND tgrelid = 'assistant_conversations'::regclass
    ) THEN
        CREATE TRIGGER update_assistant_conversations_updated_at
        BEFORE UPDATE ON assistant_conversations
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at_column();
    END IF;
END $$;
