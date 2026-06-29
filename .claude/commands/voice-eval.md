---
description: Run a voice/LLM eval harness on the server against an isolated /tmp overlay of the live backend (never touches prod).
argument-hint: "<path to harness .py> [un-deployed files to overlay on top]"
allowed-tools: Bash, Write, Read
---
Run a behaviour/eval harness on the Hetzner server in an isolated overlay of the
live backend — this never touches production. Server access is in the
`server-ssh-access` memory.

The overlay pattern (used all through the 2026-06 LLM-stability work):

1. Refresh the overlay from the LIVE deployed code so the test reflects prod:
   `rsync -a --exclude venv --exclude __pycache__ --exclude .git /opt/talky/backend/ /tmp/talky-verify/`.
   If testing UN-deployed local changes, `scp` those specific changed files on top of the overlay after the rsync.
2. `scp` the harness (the path in $ARGUMENTS) to `/tmp/talky-verify/`.
3. Run it DETACHED so an SSH drop can't kill it:
   `cd /tmp/talky-verify && nohup env PYTHONPATH=/tmp/talky-verify /opt/talky/backend/venv/bin/python <harness> > /tmp/talky-verify/eval.log 2>&1 & echo "pid $!"`.
4. Poll: `kill -0 <pid>` for RUNNING/DONE, then read `/tmp/talky-verify/eval.log`, filtering out the noisy `High .*TTFT|rate limit|cold cache|cold start` lines.
5. Summarise results.

Notes: the venv has deepgram/groq/google-genai installed; the harness should
`load_env("/opt/talky/backend/.env")` for API keys + `DATABASE_URL`. Harnesses
typically build the REAL campaign prompt via `compose_prompt` + the live DB
(campaign `b6a61ac6` = Dojo) and may use an LLM caller + LLM judge for multi-turn.
Models in the menu: llama-3.3-70b, llama-3.1-8b, qwen/qwen3.6-27b,
gemini-3.1-flash-lite-preview, gemini-2.5-flash. Best test temp ≈ 0.5.
