# Release preparation — 8 October 2026

The requested agent repairs are pushed to `codex/production-ready-20261004` at `eea09632`. [Draft PR 19](https://github.com/talkyai-source/Talky.ai-complete-/pull/19) integrates them with the newer main branch on `codex/production-release-20261008`. This is release preparation, not production activation or paid-use acceptance. Unrelated work in the original checkout is preserved.

## Integration and verification

- Merge `d99ebcad` reconciles the readiness branch with main `8fb91256`, preserving the existing shared UI and provider behavior. A subsequent merge incorporates main `ea2b83a6`, including its existing dashboard/call process separation. That upstream change is preserved, not a new feature introduced by this release.
- Migration `0063_release_history_merge` joins the readiness and production heartbeat histories. [Migration evidence](2026-10-08-release-migration-merge.md) records 11 unit checks and three actual isolated PostgreSQL upgrade paths: heartbeat head, readiness head, and canonical bootstrap. No production migration was executed.
- The trunk updater remains one long-running loop refreshing every ten seconds. The old timer definition is retained solely so installation can disable existing symlinks before reloading systemd. An actual isolated `systemctl --root` reproduction proved why deleting the definition leaves the old timer enabled.
- `602a263a` pins the minimum compatible non-yanked LangGraph stack containing the SDK security patch. [Dependency qualification](artifacts/release-langgraph-security/verification.md) records all 156 resolved packages audited with zero known vulnerabilities, plus 327 passing assistant/compiled-graph checks against the exact upgraded 37-package closure.
- The final merged backend run at committed source `259b8789436cf74977c2c1d90ee185f778a93f4a` passed **2,158 tests across 137 modules**, with zero skipped/failing tests, 1,781 warnings, unchanged source hashes and zero prohibited network attempts. This was a Windows test environment with the exact patched dependency closure over the existing environment, not a clean Linux installation.
- Final review reproduced and fixed two defects in the inherited API split: public readiness/deep routes now target the call process, and the disabled dashboard role refuses manual telephony start and origination before effects. [Focused evidence](artifacts/release-process-split/verification.md) records six reproduced failures followed by 175 passing checks. The checked-in nginx site still needs explicit installation/validation during activation; the existing deploy script does not apply it.
- The affected dashboard run passed **74 tests across nine modules**. TypeScript passed; scoped lint had zero errors and one pre-existing cleanup warning. Those frontend files did not change in the later backend/process-role merge.
- The initial PR candidate `d99ebcad` passed the fast voice workflow, production CMake/CTest, gateway sanitizers/shutdown checks, and automatic Vercel preview deployment. These are explicitly earlier-candidate results; final-head CI remains separately required.
- Both frontend lockfiles patch `source-map-js` to 1.2.2. Admin audit reports zero findings. The dashboard full audit still reports 11 high-severity development-dependency paths to [the unpatched braces advisory](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm). Its production-only diagnostic reports zero, but that does not replace the existing full audit gate. No suppression or forced framework downgrade was used.
- The initial secret scan reported 114 historical evidence SHA-256 values. Every value was independently recomputed from its referenced Git source: 91 raw, one LF and 22 CRLF matches. Only the exact commit/file/rule/line fingerprints were excepted; all prior exceptions and credential comments remain. The same historical range then scanned clean. Final pushed-head CI is a separate check.

The [release artifacts](artifacts/release-20261008/) retain the combined manifest/output, frontend output, dependency audit results, safe checksum classification, retired-timer reproduction and sanitized server observations. Raw secret-scanner reports, server credentials and customer records are excluded.

A later broader `origin/main..HEAD` history scan reported 1,484 findings. Nineteen belong to the new evidence commit: seventeen source checksums were recomputed against their recorded Git versions, and two JWT matches are public [PyJWT 2.15.1 README examples](https://pypi.org/pypi/PyJWT/2.15.1/json), independently verified against public metadata. Only those nineteen exact fingerprints were added. The remaining 1,465 older-history findings have not been classified by this release and must not be described as clean or assumed to be credentials. This wider scan is distinct from the previously cleared 114-finding CI range; complete history/CI qualification remains open.

## Actual server state and activation requirements

At **2026-10-07 20:26 UTC**, `/opt/talky` was clean at `ea2b83a61435d170147b941453f1b112b0d597eb`. Main/server advanced independently during preparation; none of those production changes were performed by this release. API, gateway and worker services were active and all three health endpoints returned 200. The user reports that the system is not in use; that statement is not a measured carrier/PBX drain record.

The retired trunk timer was failed. Its repair is in the candidate and takes effect during installation. The latest backup service was failed: a 24,156,814-byte dump was rejected against the previous 690,149,225-byte backup. The cause and restorability need verification; lowering the size check is not a repair.

The database read-only preflight observed head `0048_audit_skip_heartbeat`, compatible existing data/indexes and permissions for the planned migration. Three existing paid plans do not supply the new approved price-option records; migration seeds only the free option. Paid checkout configuration and actual live acceptance remain unfinished.

Production activation still requires:

1. Authenticated operator sudo access. SSH works, but `sudo -n true` requires a password. No password is stored or requested in chat.
2. The fresh candidate-bound drain manifest and digest required by `deploy_to_server.sh`, containing observed carrier/PBX/Redis/database drain facts and the prescribed sign-offs. User deployment authorization is already present; missing operational evidence must not be fabricated.
3. A verified fresh backup and rollback/restore proof before the production migration.
4. Exact installed dependency parity and final candidate CI, including disposition of the unpatched frontend audit finding. The deploy script does not install Python requirements.
5. The supported pinned Git/systemd deployment followed by migrations, gateway/protocol checks, service health and real acceptance checks. The dormant container workflow is not the supported deployment path.

No production checkout, migration, service restart, call or connector action has been performed by this release preparation. Git object staging alone, if subsequently recorded, does not activate the release. No package or customer/release gate is closed by this checkpoint; the feature freeze and CP05/CP06/CP09 deferrals remain unchanged.
