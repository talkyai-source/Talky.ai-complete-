# Admin coordinated release — 28 October 2026

The coordinated backend and Admin frontend release completed. Fresh hosted QA
passed the five previously failing contracts, maintenance reopening succeeded
and the final read-only collector passed all 19 checks.

This continues the existing Admin remediation and production-readiness scope.
It supersedes the activation status in the [current-billing compatibility
checkpoint](2026-10-28-admin-current-billing-compatibility.md). Earlier failed
tests, live failures and isolated recovery evidence remain historical records.
CP09 remains in progress; the feature freeze remains active.

## Authorization and release artifact

The platform owner identified themselves as the sole current operator and
authorized proceeding. The documented version-2 `single-owner` manifest records
that actual owner decision. It still
requires the exact candidate, manifest digest, freshness, actual ingress and
origination freeze, four zero counts and single-use replay protection. Version 1
retains its two-operator rule. Approval references bind retained evidence; they
are not cryptographic proof of identity.

Reviewed and deployed candidate: `10a8b9f0f9d8e7d5c59140f1e4829790050161b9`.
[PR 22](https://github.com/talkyai-source/Talky.ai-complete-/pull/22) merged at
2026-10-28 08:42:45 UTC as `a99e11d99e87bff7a49f9e6fdf0ac0b3a4fa5166`.
The deployed backend runtime remains the exact tested candidate; the merge
records that candidate in the main branch.
The owner-policy change is `bbe7bd5f`; the final candidate also adds bounded
gateway readiness retries while preserving strict session/protocol/build checks.
There is no new provider or unrelated feature.

All 14 CI checks passed for this candidate. The actual tested merge tree equals
the candidate tree, corroborated by all 12 workflow-job checkout logs.

| Gate | Observed result |
|---|---|
| Admin PostgreSQL | 701 passed |
| Admin frontend | 943 passed; lint/build/audit passed |
| Customer frontend | 1,320 passed, two existing skips |
| Backend | 15,628 passed, 174 existing skips; 72.54% coverage |
| Voice/security | 15,582 passed, 220 unchanged skips |
| Native gateway | Production tests and sanitizer/shutdown gate passed |
| Container | Build and configured HIGH/CRITICAL, ignore-unfixed scan passed |

Counts overlap and are not summed. The separately rebuilt Compose image was
not rescanned. A GitHub status-read transport timeout recovered without any
workflow or test rerun. [Main CI](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/40650718347),
[voice CI](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/40650718112),
[gateway CI](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/40650718115).

CI receipt: `output/single-owner-live-20261028/ci-final.json`, SHA-256
`df423ffcbd4ae367bc57f0a4fe97cfd51fdc0db903c658bf826bc85a2181a3df`.

## Measured maintenance and backup

The release operator applied the existing application inbound/outbound pause,
an owned nftables maintenance table and a synthetic-call skip override. The
actual gateway, Asterisk, Redis and database active counters all reported zero.
The owner-authorized v2 manifest was accepted. These are release-window facts;
they do not establish a permanent external carrier guarantee.

- Freeze receipt SHA-256: `f579534a8d686c81f3d4fa2e08d98be108fc6835eff4a85ecf0a655836ae9038`.
- Verification receipt SHA-256: `ed8ab31647711ddd10a8229804acf9853d9d2719e5afb5b46a7c975162b121db`.
- Fresh backup: `/var/backups/talky/releases/admin-single-owner-20261028/talky-20261028T075814Z.dump`.
- Archive size: 24,722,125 bytes; 121 TABLE DATA entries.
- Verified archive SHA-256: `0c6a9b971b1db982b29557676e3fb23c98fe3039fd5f7d96651d14eb016c0c7f`.

The fresh backup creation and the earlier isolated production-shaped restore
are separate evidence. The new archive was created and verified; it was not the archive used in the
earlier isolated restore rehearsal. No backup archive, secret,
raw customer row or full traffic configuration is added to Git.

## Actual deployment result

| Check | Recorded result |
|---|---|
| Deployment command/final exit | Coordinated deployment completed with exit 0; native candidate CTest 4/4 passed in 141.95 seconds |
| Live backend checkout | `10a8b9f0f9d8e7d5c59140f1e4829790050161b9` |
| Database head and restricted-role gate | `0081_admin_payment_merge`; current runtime privilege checks passed, with the retained elevated-role warnings below |
| API and API-web process versions | Both actual API process-version checks passed against exact candidate `10a8b9f0` |
| Gateway build/protocol/readiness | Deployment gateway readiness/protocol/build checks passed against exact candidate `10a8b9f0` |
| Required service/timer and health results | All eight required services active; all four required health endpoint checks passed |
| Admin frontend deployment/source | READY promotion `dpl_82iZaGbWFL3yjAe3ZETk6dfCU8Uj`; promotion receipt SHA-256 `b60668c5c1cb5a7ab2318fedb8e0acc2f1f93bbb7a7679cbbf91025415844373` |
| Customer frontend deployment/source | Main merge `a99e11d9`, deployment `dpl_5dBYTpjWJThezEZQLRwWUpYQm1c7` READY; `talkleeai.com` alias verified; Admin alias remained on its promoted deployment |
| Scheduled backup unit/configuration | Full installer replaced the `eae` maintenance unit with the canonical candidate unit; canonical unit verified at `/opt/talky/backend/systemd/talky-db-backup.service`; backup configuration verified root-owned mode `0600`; timer checks passed |
| Traffic pause and owned firewall removal/restoration | Original runtime controls restored; owned nftables table and temporary drop-in removed; reopening completed successfully |
| Synthetic-call override/timer disposition | Temporary `/bin/echo` skip override removed; probe configuration restored and timer active; the probe service was inactive with an empty `ExecMainStartTimestamp` when checked; no completed real-call claim |

The coordinator and final read-only collector both exited 0. All 19 final checks
passed. The actual command and maintenance records remain in the private release
evidence, including these verified receipts:

- Final server receipt SHA-256: `ac2d8cc78b2d6fdd3f38ec95e9c896c557330018744e77707c43efbd1c5a61ee`.
- Reopening wrapper SHA-256: `d6043dba07c4d656507f1f28f9017d24c045777efbbc6e6f35bcce618e19bff7`.
- Reopening inner result SHA-256: `79b6c7e2321109989ababb72187209fe4bc37fb662536821815a3e91bc258371`.
- Verified local evidence bundle: `output/single-owner-live-20261028/server-evidence.tar.gz`, SHA-256 `e61a2fd80a1b58402b76eb55ed9d8e56af8f68ca09bb19b9c69fde1458a6d81a`.

Customer deployment and alias receipt: `output/customer-postmerge-live-20261028/ready-alias-receipt-v1.json`,
SHA-256 `f9199689b0b11b767271297b47e3f037f6e53066944aad473d5e6cdf3031bbb2`.
This verifies deployment completion and source/alias identity; no additional
customer business-flow acceptance is claimed.

The runtime database role still has `CREATEROLE`/`CREATEDB` and owns tables.
The current privilege gate passed with those warnings; this does not complete
least-privilege remediation. PBX reconciliation found no drift, zero active
routes and one verified but unrouted trunk associated with a pre-existing
inactive assignment. No inbound call availability is inferred from that result.
The full installer replaced the prior `eae0465e` maintenance pin with the
canonical candidate unit containing the same backup repair. The temporary
synthetic-call skip was removed. Restored scheduling does not establish a
successful end-to-end call.

## Hosted Admin QA

| Previously reproduced failure | Fresh observed result |
|---|---|
| Users operations returned 500 without required CORS | Passed: pending/terminal discovery HTTP 200, required CORS and pagination |
| Inbound All history returned 400 invalid_status | Passed: All-history HTTP 200 and pagination |
| Connector summary/review response was missing | Passed: global connector summary present |
| Media deletion history returned 404 | Passed: history HTTP 200 and pagination |
| Populated enquiry response lacked revision | Passed: populated revisions present and cards rendered |

All six views passed and displayed the exact API version `10a8b9f0`. Normal
login/logout passed, with no business writes, page exceptions or blocked
requests. Logout, browser/process and private-data cleanup passed; no child
processes or private browser residue remain. The live QA receipt has SHA-256
`eb1f3fbdb435da7a43ff8b540ab8919cc130d0daf1aec29a49ad34a915d86d90`.

These five API/UI contract failures are closed by their real hosted read checks.
Read-only discovery is not destructive-operation, MFA, provider delivery or
call-quality acceptance. The remaining operating requirements stay explicit.

## Remaining acceptance

Designated MFA, current-role and business-operation journeys; real call,
connector, provider and media effects; an approved alert receiver/fault window;
Groq/Cartesia source mappings, supplier-account allocation and finance review;
off-host recovery with measured RTO/RPO; load and policy/customer acceptance
remain separate unfinished requirements. Retained Deepgram usage charges are
not verified net spending. Any observed change to this list must cite its
specific evidence.

CP09 remains in progress, CP05/CP06 remain deferred and unrelated feature work
remains frozen. Successful deployment and five repaired read contracts do not
close the entire Admin product or the overall production-readiness goal.
