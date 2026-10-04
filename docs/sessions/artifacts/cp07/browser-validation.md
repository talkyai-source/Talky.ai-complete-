# CP07 local Chromium notification validation

The checked application modules are the real notification store, client hooks, NotificationCenter, event query, and QualifiedLeadAlerts. The harness supplies synthetic verified tenant/user pairs and a synthetic event API. It does **not** mount AuthProvider, use real login/cookies, call the backend, or send external notifications. The real authentication boundary has separate component-test evidence. This is browser isolation evidence, not deployed end-to-end or external-delivery acceptance.

The local fixture ran on `http://127.0.0.1:3197/` in two isolated Chrome tabs. The fixture blocks `window.fetch` and counts attempts; actual event polling runs against the supplied synthetic API adapter. Only local GET page/bundle requests are permitted by the recorded scenario. The `never-send.example.invalid` address is deliberate inert test data. All account IDs, phone numbers, alerts and responses are synthetic.

## Reproduction

From the repository root after installing the locked Talk-Leee dependencies, copy the saved `browser-harness/` source files into `tmp/cp07-browser/`. The saved source is the exact executed harness; the generated JavaScript bundle is intentionally not checked in.

```powershell
New-Item -ItemType Directory -Force tmp/cp07-browser
Copy-Item -Path docs/sessions/artifacts/cp07/browser-harness/* -Destination tmp/cp07-browser
node tmp/cp07-browser/build.mjs
node tmp/cp07-browser/server.mjs
```

Keep that loopback-only server in its own terminal. In another terminal, using the Playwright CLI:

```powershell
npx --yes --package @playwright/cli playwright-cli -s=cp07-audit open http://127.0.0.1:3197 --browser chrome
npx --yes --package @playwright/cli playwright-cli -s=cp07-audit tab-new http://127.0.0.1:3197
npx --yes --package @playwright/cli playwright-cli -s=cp07-audit --raw run-code --filename tmp/cp07-browser/browser-run.js > docs/sessions/artifacts/cp07/browser-validation-run.txt
python tmp/cp07-browser/write-browser-evidence.py
npx --yes --package @playwright/cli playwright-cli -s=cp07-audit close
```

Stop only the local server process started for this fixture. The session must contain exactly two tabs at this origin. The scenario clears only this isolated fixture origin's localStorage. It uses the real rendered controls and actual browser storage events; one control temporarily defers delivery of storage events to reproduce stale-tab clearing. It never changes the application store's private internals.

## Recorded coverage

The final scenario checks:

1. A local action and an actual QualifiedLeadAlerts observation create A-only history; the second tab hydrates it without recreating toasts.
2. Missing and foreign tenant/user stamps in event responses cannot create alerts in the captured account.
3. Switching both tabs to B rejects both a captured A action and an already-started A event response released after the switch.
4. Old unscoped history and legacy webhook-enabled settings are discarded/normalized. No fetch attempt is made.
5. B refresh retains only B history.
6. Tab B holds old storage events/settings, while the other tab disables persistence. Clearing in the stale tab preserves the new privacy setting, clears history/toasts in both tabs, and does not recreate the deleted B history key.
7. Rebinding A restores only A history. Logout masks live history, toasts and scope in both tabs.

The final `browser-validation.json` records the actual assertions, states, local request list, and fetch-attempt counts. **Passed on source commit `40232996372305b8ab100bc8fc57a3957296f4e4`: 12 recorded states, zero fetch attempts in either final tab and all captured states, eight local GET page/bundle requests.** Bundled application files had no uncommitted changes. Chrome version was 154.0.8037.93, running headlessly. Screenshots show the actual components with minimal harness CSS; they are not full-dashboard visual acceptance. Earlier individual `browser-state-*.json` files are exploratory observations; the single final scenario is the authoritative replay against the hashed final bundle. The displayed `eventReads` diagnostic can lag when no React state changes; the runner waits on the actual adapter counter and query completion directly for both rejected-owner controls.

Authoritative screenshots: `browser-account-a.png`, `browser-account-b.png`, `browser-clear-after-delayed-storage.png`, and `browser-logout.png`. They were visually inspected. The captured before-fix clear asymmetry is preserved separately in `browser-clear-race-before-fix.json` and `.txt`; it is not a failure of the final replay. The minimal fix uses an account-scoped random clear marker, rereads current privacy, and clears live rows/toasts. It does not broadcast alert content. Cross-tab clear still requires writable browser storage; this replay does not claim otherwise.

Saved per-account history is deliberately retained for the same verified identity when persistence is enabled. This checks application-level presentation and asynchronous-result isolation. It does not assert encryption or erasure of persisted browser data on logout, nor protection from a malicious same-origin script/browser owner. Closed-browser delivery, recipient selection, durable outbound retries, receiver acceptance and production operations remain outside this proof. External notification automation is still blocked / not done.

Source/bundle provenance is in `browser-validation.json`; browser/server cleanup is in `browser-cleanup.json`. The owned `cp07-audit` Chrome session and server PID16560 were stopped and loopback port3197 had no listener. The temporary generated bundle is not part of the saved source. Automatic approval review rejected recursive deletion of the ignored `tmp/cp07-browser` directory as blocked by policy, so those inactive files were retained; the saved harness source was independently compared byte-for-byte with the temporary originals. Dates in browser screenshots use local Asia/Karachi time, while machine timestamps use UTC.
