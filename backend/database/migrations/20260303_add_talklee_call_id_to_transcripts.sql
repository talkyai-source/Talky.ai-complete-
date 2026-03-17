-- Day 7: Bind transcripts to talklee_call_id for STT integrity reporting.

ALTER TABLE transcripts
    ADD COLUMN IF NOT EXISTS talklee_call_id VARCHAR(20);

CREATE INDEX IF NOT EXISTS idx_transcripts_talklee_id
    ON transcripts(talklee_call_id)
    WHERE talklee_call_id IS NOT NULL;
