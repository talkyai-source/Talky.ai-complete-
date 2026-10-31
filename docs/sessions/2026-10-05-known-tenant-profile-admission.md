# Known-tenant profile availability at call admission

Source candidate: `a4517678b91d1a6745606756313a8c9d93326bae`, isolated from MAIN at `33360ddf42c1089e78f9ee9918ba0d770f4347c7`. No provider, model, default, prompt, schema or lifecycle changes were made. The application change consists of three strict-resolution flags and documentation corrections.

## Reproduced mismatch

An offline probe exercised actual tenant resolvers, `prepare_prewarmed_session`, and the Twilio/Vonage session builders. Only the database lookup, DID route, provider factory and warmup ports were synthetic; socket connections and DNS were prohibited.

For a known tenant whose saved profile selected native Realtime with voice `ash`, an unavailable/unwired AI lookup made outbound prewarm return ready while supplying the factory a process-default cascaded profile and `marin`. A failed/unwired tuning lookup similarly changed the saved cascaded end-of-turn timeout from 1,800 ms to the 500 ms default while returning ready. The strict warmup gate could not detect this: it warmed the fallback configuration it received.

After a DID had resolved to a tenant, both Twilio and Vonage builders also returned a default cascaded profile when the AI lookup failed. Their tuning lookup already raised `TenantAIConfigUnavailable`, making the AI and tuning failure behavior inconsistent.

This is a configuration-admission defect, not a semantic model failure or evidence that real provider sessions/calls were made during the probe. The normal outbound endpoint already rejected a failed prewarm with 503; the problem was that lookup failure was hidden and prewarm reported success.

## Bounded repair

- Outbound prewarm requests `require_available=bool(known_tenant)` for both existing resolvers, before building the session configuration or creating a provider session.
- The DID helper requests the same strict AI resolution once a tenant is known. The call is outside the route-resolution exception handler, so a profile failure is not recast as tenant-less success.
- Existing strict behavior raises for unavailable/unwired lookup. A successful lookup returning no saved row remains compatible with defaults. Genuinely tenant-less paths retain their existing default behavior.
- Resolver documentation now describes strict and explicit soft modes accurately. Two old test doubles were updated for the existing keyword contract; their behavior assertions were retained.

The existing prewarm error result reaches the existing outbound 503 gate. No provider factory or origination is invoked for these unavailable known-tenant cases. The repair does not add retry, failover or a second configuration service.

## Evidence

The initial actual-method regression yielded **8 failures and 14 passes**: four outbound lookup-unavailable cases and four known-DID AI cases failed to reject; saved/no-row/tenant-less and existing strict tuning controls passed. Final focused coverage is **26 passed**, including four actual outbound endpoint 503/no-provider/no-origination controls. The final ten-module regression is **308 passed, zero skips**, with 11 existing deprecation warnings.

The same offline probe after the repair rejects AI/tuning exceptions and unwired lookups for a known tenant, while saved profiles, successful no-row defaults, and the existing positive builders remain available. Exact before and after JSON, original probe, commands, source hashes and logs are retained in [artifacts/tenant-config-admission](artifacts/tenant-config-admission), especially [verification.json](artifacts/tenant-config-admission/verification.json).

Intermediate test authoring failures are preserved separately: the first endpoint fixture lacked its second campaign row and reached an unrelated 404 after the bad fallback; a network-ban fixture initially outlived the Windows event-loop teardown; and an existing opening-mode test double lacked the new call-site keyword. None is counted as an additional product defect. The network-ban lifetime and fixture contracts were corrected before the final run.

Scoped CI Ruff `F` with the repository's existing `F401,F841` exclusions and `git diff --check` passed. A stricter unfiltered `F` invocation identified the already-existing unused `os` import in the documentation-only `voice_tuning.py` change; that unrelated debt was not changed. Independent read-only review by `llm_audit` found no material defect; the reviewer did not rerun tests.

## Limits and separate follow-up

No actual provider, database, telephone or customer action was performed. Tests use the existing dependency overlay with the pinned production versions, followed by the existing test-only Lua target; the shared virtual environment was unchanged.

Unknown/unroutable DID and route-lookup failure still take the legacy unresolved-tenant path. This slice repairs profile lookup after known ownership; it does not claim to repair all legacy route admission.

The outbound callback's missing-warmup default branch in `telephony/lifecycle.py` is unchanged. Read-only tracing confirms ordinary Asterisk origination waits for successful prewarm and stores it before calling the adapter. Non-Asterisk callback-before-return ordering and aged/lost local warmup state are separate candidates for reproduction; this evidence does not prove that a normal failed Asterisk prewarm bypasses its 503 gate. No lifecycle change was made without a separate demonstrated boundary.

## Root integration follow-up

Source integrated as `5c26f311`. At combined candidate `427022cd`, the owner
regression plus every named direct resolver/prewarm test caller and the adjacent
Admin receipt/AG04 replay modules passed: **620 passed, zero failures or skips**,
1,083 warnings, 12.68 seconds across **20 modules**. The source snapshot remained
unchanged. [Integrated command](artifacts/tenant-config-admission/integrated-command.json)
and [output](artifacts/tenant-config-admission/integrated-tests.txt) preserve the
test inventory and exact source identity. Counts overlap the earlier focused and
full suites; the 11,775-test full run remains attributed to its earlier candidate.

Root integrated the owner's initial evidence commit `e2d67de2` as `427022cd`
before the final owner notification. Final owner evidence `cae1b890` differs only
by removing trailing whitespace on two otherwise empty lines in a retained
fixture-teardown log. That exact corrected artifact was applied in a follow-up;
there was no redaction, source change or result change.

The README now describes strict known-tenant setup and distinguishes current
SIP campaign origination from separately enabled legacy cloud callbacks. The
missing/lost warmup and unresolved cloud ownership paths remain separate
investigations, without a live/deployed acceptance claim.
