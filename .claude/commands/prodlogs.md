---
description: Triage production logs on the Hetzner server (errors/tracebacks across the Python services).
argument-hint: "[grep term — defaults to error|traceback|exception]"
allowed-tools: Bash
---
Fast production incident triage. Server access + sudo password are in the
`server-ssh-access` memory.

KEY FACT: **telephony runs in `talky-api`** (STT/LLM/TTS pipeline + Deepgram Flux);
`talky-voice-worker` is the browser/ask-ai path. So "calls aren't responding" =
look at `talky-api` first.

For each of `talky-api`, `talky-voice-worker`, `talky-dialer-worker` over the last
15 minutes, run (sudo pw from memory):
`journalctl -u <svc> --since "15 minutes ago" --no-pager | grep -iE "<$ARGUMENTS or default>"`.

Targeted checks worth adding:
- **Silent calls / no STT:** grep `Flux connection error|HTTP 400|STT stream error`
  (root cause of the 2026-06-29 outage = Deepgram Flux rejecting `numerals=true`).
- **LLM issues:** grep `Zero text chunks|circuit|LLM .*timeout|stream error`.
- **Call setup:** grep `telephony_prompt_composed` to confirm calls are even starting.

Summarise what's failing, the likely root cause, and whether it's our code or a
provider-side issue (Deepgram/Groq/Gemini). Prefer a config/env fix or rollback to
`4a36b841`-style targets over a risky hot patch when prod is down.
