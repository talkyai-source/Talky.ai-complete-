# backend/docs/stability — Voice Pipeline Resilience

This folder documents work whose sole job is to keep an active call
alive. Nothing here adds features — everything here makes existing
features survive transient provider failures, network blips, and
upstream stalls without the caller hearing dead air.

Companion to `backend/docs/prompt/` and `backend/docs/script/`. Same
paper trail: one reference doc per feature + plan + execution log.

## Features

- [google_tts_connection_hardening.md](./google_tts_connection_hardening.md) — streaming TTS must not break until hangup; auto-fallback to REST when the bidi stream dies

## Plans & Execution Logs

- [2026-04-22 — Google TTS Connection Hardening Plan](../superpowers/plans/2026-04-22-google-tts-connection-hardening.md)
- [2026-04-22 — Google TTS Connection Hardening Execution Log](./2026-04-22-google-tts-connection-hardening-execution.md)
