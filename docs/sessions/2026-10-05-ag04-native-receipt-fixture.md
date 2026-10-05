# AG04 native replay receipt boundary — 2026-10-05

Source repair: `6617d072f3067e609ac482afd6b4f327c7e44eeb`, isolated from `91e37616`. Only the native qualification runner, JSON corpus and its behavioral test changed. No application, model, prompt, threshold or database change.

The new native DNC speech gate correctly requires an acknowledged persistence result before allowing the original response. The older replay fixture supplied no such port: its synthetic call had no registered tenant/phone lifecycle session, so the real helper could not acknowledge a write. The full-suite failure was therefore an obsolete successful-fixture setup, not permission to relax the speech gate or its expected shutdown/submission checks. The [root baseline excerpt](artifacts/ag04-native-receipts/root-baseline-failure.txt) retains that failure. Two isolated baseline attempts hit existing two-second runner waits instead; they remain separately recorded as timing failures in `before.txt` and `before-repeat.txt`. No deadlines were raised.

The runner now intercepts only the existing `purge_opt_out_before_farewell` persistence port for its own call. Positive DNC scenarios explicitly declare `true`; failed and unknown variants declare `false` and `null`. Undeclared acknowledgement remains unknown. The actual provider parsers, bridge task/receipt gates and repair serializers still execute. Each receipt records `synthetic persistence port` and zero database writes; durable `dnc_effect_count` remains unknown. New variants retain the unsafe raw candidate, require zero original speech/control submission, capture one bounded repair request and require no premature termination. No repair response is invented. OpenAI's repair metadata and xAI's absence of unsupported metadata remain visible in the captured wire request.

## Verification and limits

- **112 passed, zero skips** across native/traditional qualification, qualification gates and native DNC speech tests, in 13.14 seconds. Existing datetime deprecation warnings remain.
- CI-rule Ruff `F` and `git diff --check` passed.
- The combined evaluator ran at the committed clean source: **174 declared/observed rows, 1,160 control checks, zero control failures, zero evidence errors, zero attempted network access**.
- Exit **1** is expected and retained: the original captured Groq semantic failure remains failed; the other 173 semantic findings remain unreviewed. Production approval is false and live/human gates are not run.
- Matrix coverage remains **37/50**, with 13 unmapped conditions. Failed/unknown DNC receipt variants reuse existing canonical conditions; they are not new human scenarios or profile qualification.
- The follow-up used the isolated exact-pin overlay: `urllib3==2.8.0`, `PyJWT==2.15.1`, followed by the existing test-only Lua target. All 62 declared active production requirements were checked separately against their marker/specifier constraints. The original shared virtual environment was not modified.

Exact commands are in [verification.json](artifacts/ag04-native-receipts/verification.json). [Replay summary](artifacts/ag04-native-receipts/replay-summary.json) records provenance and hashes; [full replay](artifacts/ag04-native-receipts/replay.json.gz) preserves the exact uncompressed JSON bytes. This is synthetic control evidence, not actual durable DNC, human hearing, live provider, carrier or acoustic validation. One initial command used a nonexistent evaluator-test filename and ran no tests; that error is retained separately, not counted as a test failure or success.
