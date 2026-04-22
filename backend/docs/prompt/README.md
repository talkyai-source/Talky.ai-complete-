# backend/docs/prompt — Agent Intelligence Documentation

This folder holds the plans, design notes, and change logs for anything
that touches **how the telephony/Ask-AI agent thinks**: system prompts,
conversation state tracking, slot extraction, spoken-data normalizers,
and model-tuning decisions.

Mirrors `backend/docs/script/` — plans + execution logs live here so the
prompt layer has the same paper trail as the Python helpers under
`backend/app/services/scripts/`.

## Scripts / Helpers

- [spoken_email_normalizer.md](./spoken_email_normalizer.md)
- [call_state_tracker.md](./call_state_tracker.md)
- [prompt_builder.md](./prompt_builder.md)

## References

- [system_prompt_structure.md](./system_prompt_structure.md) — why the telephony prompt is shaped the way it is

## Plans & Execution Logs

- [2026-04-22 — Agent Intelligence & Anti-Hallucination Plan](./2026-04-22-agent-intelligence-plan.md)
- [2026-04-22 — Agent Intelligence Execution Log](./2026-04-22-agent-intelligence-execution.md)
