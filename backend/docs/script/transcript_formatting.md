# `transcript_formatting.py`

**Module:** `app.services.scripts.transcript_formatting`
**Tests:** `backend/tests/unit/test_campaign_transcript_query.py` (formatter section)
**Line budget:** 600 — currently ≈ 50 lines.

## Purpose

Pure view-model helper. No I/O. Translates the stored
`TranscriptTurn.to_dict()` shape (which includes Deepgram-specific fields
like `event_type`, `is_final`, `audio_window_start`, and interim/eager
partials) into the minimal UI contract: `{role, content, timestamp}`.

Kept as its own file so the same trim can be reused by future consumers
(e.g. an export-to-PDF job) without pulling in asyncpg.

## Public API

### `format_transcript_turn(turn: dict) -> dict`

Normalises a single turn. Defaults:
- `role` defaults to `"assistant"` when missing
- `content` is `.strip()`'d
- `timestamp` defaults to `""` when missing

### `format_transcript_turns(turns: Iterable[dict]) -> list[dict]`

Keeps a turn iff:
- `role in {"user", "assistant"}`
- `include_in_plaintext` is truthy (default `True` for older records that
  predate the flag)
- `content` is non-empty after strip

Drops everything else. Also tolerates `None`, an empty iterable, and
non-dict entries in the list.

## Why `include_in_plaintext` matters

Deepgram Flux emits multiple frame types per utterance:

- `eager` — preview transcript, subject to revision
- `update` — revision of a prior eager
- `end_of_turn` — finalised utterance

Only `end_of_turn` frames have `include_in_plaintext: True`. The UI must
never show `eager` / `update` because they appear and disappear as
Deepgram refines its confidence — a user who sees "Hello I am calling
about..." then watches it mutate into "Hi there, is Bob available?" will
(correctly) think the transcript is unreliable.
