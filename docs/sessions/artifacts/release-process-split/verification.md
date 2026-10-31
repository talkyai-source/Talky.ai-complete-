# Release API split corrections

Two reproduced merge-candidate defects are repaired:

- The nginx map now sends `/api/v1/healthz/ready` and `/api/v1/healthz/deep` to the call process. Dashboard capacity/drain defaults cannot stand in for call-process readiness.
- Manual telephony start and authenticated internal origination refuse a disabled process role with HTTP 503 before database, intent, or provider work. Origination authentication still runs first. Default/enabled call-process behavior and existing ownership checks remain unchanged.

Initial actual-entrypoint and nginx-map controls: **6 failed, 42 passed**, zero network attempts. Final eight-module regression: **175 passed, zero skipped, 51 warnings, 11.18 seconds**, zero network attempts. It includes existing start ownership, internal authentication, durable origination, campaign boundaries, systemd timer retirement, ten-second loop, and deployment migration guards.

Tests use the exact patched dependency overlay described in the adjacent `release-langgraph-security/commands.json`. Actual application entrypoints use synthetic storage/adapter seams. Nginx tests evaluate the checked-in map, not a running nginx server. No database, provider, call, or production operation was performed.

Full-file Ruff F finds the same five pre-existing unused import/local diagnostics in `telephony_bridge.py` as HEAD; `lint.json` records the exact comparison. Both affected test files pass Ruff F. Diff check is clean. Root performed a bounded read review of the minimal source/tests.

`manifest.json` records the exact final command and canonical LF source hashes. `initial.txt` and `final.txt` retain results. Nginx installation/reload remains a separate explicit activation step; `deploy_to_server.sh` does not install this site. Retired timer disable-before-daemon-reload and the ten-second service loop were preserved.
