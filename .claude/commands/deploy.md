---
description: Test, commit (Uzair, no co-author), push to main, deploy to the Hetzner server, and verify.
argument-hint: "[optional commit-message summary]"
allowed-tools: Bash, Read, Edit
---
Deploy the current working-tree changes to production.

Server access (IP, user `admins`, SSH key path, sudo password) lives in the
`server-ssh-access` memory — read it from there at runtime. NEVER hardcode the
sudo password anywhere. TELEPHONY runs in `talky-api` (the voice-worker is
browser/ask-ai), so a backend prompt/LLM/STT change must restart `talky-api`.

Do these steps in order; STOP and report if any step fails:

1. Show `git status --short` and a one-line diff summary so the user sees exactly what's shipping.
2. **Test before shipping:** run the affected backend unit tests (`cd backend && python -m pytest <relevant files> -q`). If anything under `Talk-Leee/` changed, also `cd Talk-Leee && npx tsc -p tsconfig.json --noEmit`. Abort on any failure.
3. **Commit** as the SOLE author `UzairDevelops <Uzairdevelops@gmail.com>` with NO co-author / Claude trailer (standing rule). Use `--author="UzairDevelops <Uzairdevelops@gmail.com>"`. Fold the summary in $ARGUMENTS into the message if given.
4. `git push origin main` (this also auto-deploys the Vercel frontend).
5. On the server: `cd /opt/talky && git pull --ff-only` and confirm the new HEAD.
6. Restart the Python services (sudo pw from memory, non-interactive `sudo -S`):
   `systemctl restart talky-api talky-voice-worker talky-dialer-worker talky-reminder-worker`.
   Do NOT restart the C++ `talky-voice-gateway` or `asterisk` unless `services/voice-gateway-cpp` changed.
7. **Verify:** services `is-active`; no `error|traceback|exception` in the last ~90s of `journalctl -u talky-api`; and `curl -s localhost:8000/api/v1/ai-options/providers` returns the expected model menu.
8. Update the prod HEAD + rollback target lines in the `server-ssh-access` memory.

Report: new prod HEAD, rollback target, and the verification results.
