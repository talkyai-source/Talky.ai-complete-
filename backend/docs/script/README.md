# backend/app/services/scripts — Documentation Index

Every Python module under `backend/app/services/scripts/` has a matching
one-pager in this directory.

**Invariants for every script file:**
- Max **600 lines** per file (including imports, comments, blank lines).
  When a file is about to cross 600, split it — do not grow it.
- Pure and focused. No hidden I/O in "formatter" modules.
- Unit-tested under `backend/tests/unit/test_<module>.py`.

## Scripts

- [`call_transcript_persister.md`](./call_transcript_persister.md) — bind voice session to dialer's `calls` row; persist transcript on hangup.
- [`campaign_transcript_query.md`](./campaign_transcript_query.md) — paginated `calls + transcripts` read for one campaign.
- [`transcript_formatting.md`](./transcript_formatting.md) — pure view-model helper that drops partial STT frames.

## Plans & execution logs

- [2026-04-22 Call Transcripts Plan](./2026-04-22-call-transcripts-plan.md)
- [2026-04-22 Call Transcripts Execution Log](./2026-04-22-call-transcripts-execution.md)
