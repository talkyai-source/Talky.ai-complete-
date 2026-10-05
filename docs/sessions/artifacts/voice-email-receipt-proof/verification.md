# Voice email and form receipt traceability

The voice action adapter discarded safe evidence returned by `EmailService`: accepted results retained only provider/message ID, and unconfirmed results lost even the available message ID. As a result, the outer durable voice receipt lacked the original authorization and the explicit link to the inner email action despite that evidence being returned by the existing service.

The bounded repair preserves that evidence for both `send_email` and the existing email-backed `submit_form`. It reuses the existing `public_action_receipt` allowlist and explicitly maps the returned inner `action_id` to `child_action_id`. The projection-only wrapper has no action identity; only its `receipt` field is retained. The actual outer executor continues to own its own action ID. No recipient, body, credentials, arbitrary provider error, inferred account link or historical backfill is added.

The existing accepted, failed and unknown outcome branches remain unchanged. Retained identifiers are evidence for inspection; they do not turn an unconfirmed result into success, recipient delivery or permission to resend. The shared durable executor and public receipt contract are unchanged.

The final source/test commit is `1d122149ab6931b13544d13ead6a8915e707e87e`. Root and an independent read-only reviewer cleared the exact application diff and all 18 controls before commitment. No source or test changes followed the final run.

## Reproduction and verification

[baseline.txt](baseline.txt) records **16 failed, 2 passed** against baseline `303268a7`. The failures reproduced the same projection loss across accepted/unconfirmed/failed results, both existing actions, outer-save uncertainty and valid safe fields alongside malformed extras. The two missing-proof compatibility controls already passed. These are parameterized manifestations of one projection defect, not 16 independent production defects.

The final five-module run in [final.txt](final.txt) passed **138 tests, 0 failures, 0 skips, 24 warnings in 9.99 seconds**. It includes 18 new controls in `test_voice_email_receipt_proof.py` plus existing voice execution, spoken confirmation, action contract and action-envelope controls. [lint.txt](lint.txt) records strict Ruff F success on the two changed Python paths. Source whitespace checks also passed.

The new controls execute the actual voice policy/context methods, parameter capture, later-turn confirmation, durable outer claim/save/replay code and public receipt projection. Only their SQL port and `EmailService` return are synthetic; the executor itself is not mocked. The synthetic email return includes complete versioned authorization proof, a message reference and an explicit inner action ID. Tests verify:

- The proof and child ID survive in the returned, saved, public and replayed outer receipt, with or without a genuine external account identity.
- Accepted output retains its existing provider-acceptance wording; failed and unknown outcomes remain unconfirmed.
- A failed first outer save retains safe evidence inside the existing unconfirmed `provider_result`, and replay makes no second email attempt.
- Missing historical proof remains absent; no account is inferred from the current tenant, campaign or call.
- The shared allowlist excludes malformed, blank and overlong evidence and strips body, recipient, token and arbitrary error details from results and receipt output.
- An unconfirmed result with no message ID remains unconfirmed.

Network access is blocked inside all 18 new asynchronous controls after their event loop starts. No PostgreSQL or provider operation was performed. The synthetic SQL port exercises the executor's control flow but is not proof of SQL correctness, durable storage across process loss, production transactions or database concurrency.

Exact commands, explicit environment assignments and source/artifact hashes are retained in `commands.json` and `manifest.json`. The source hashes were recorded from the frozen final candidate after its test run; the pytest log itself does not embed those hashes. No new feature, public receipt schema change or readiness-gate promotion is claimed. Original-account provider inspection and designated live acceptance remain separately unfinished.
