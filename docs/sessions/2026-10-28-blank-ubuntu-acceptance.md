# Blank Ubuntu installation and reboot — 28 October 2026

The actual fresh-host installer passed in a newly created Ubuntu 24.04.5 amd64
VM, including both frontend builds, service startup, HTTPS and a real reboot.
[Run 40674826947](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/40674826947)
started at 10:47:56 UTC and completed at 10:56:52 UTC.

Candidate: `9c06d8c6510b288b190a20a6a17354259c8bb6b6`.
Actual tested merge: `cb63c48fbebb2a21116e45f0f1754f607307f7a4`.
Both have tree `2ce14657e67d6ba7ff33ea7c5418e948e816fcef`; no source difference
is inferred away. The [unaltered receipt](artifacts/ubuntu-blank-20261028/passed-cb63c48f.json)
has SHA-256 `2199a89784c5564960a12030d6ab865b65e6da0384465fdadffec9312e9c52ee`.

## Observed outcome

- The checksum-verified official Ubuntu image was installed into a new owned
  QEMU overlay using KVM on an ephemeral GitHub runner.
- The external synthetic PostgreSQL fixture used verified TLS and the actual
  restricted runtime-grants procedure before installation.
- The installer built the locked Python runtime, C++ gateway, Next dashboard
  and Vite Admin frontend and configured Asterisk, nginx and authenticated Redis.
- All 12 expected services/timers were active before and after reboot.
  Application services used `talky`; operator identity and scheduling checks
  passed. Unapproved migration, legacy backup and carrier-call schedules were
  not enabled.
- Local readiness and all three configured HTTPS endpoints passed. The actual
  API process environment and gateway build identified the tested source.
- The database route stayed on `enp0s2`; Redis used host networking and bound
  only to loopback. No Docker bridge shadowed the external database route.
- A changed kernel boot ID was required before the second verification. The
  receipt records `reboot_verified=true`.
- Owned VM and database resources were removed; cleanup succeeded before the
  overall result became `passed`.

The separate installer workflow also passed both locked frontend builds and
the exact hashed Linux runtime audit on this candidate. The 54 installer tests
passed on actual Ubuntu. Whole-backend CI still required its separately recorded
prompt-version and test-fixture corrections; this note does not call the entire
candidate's CI green.

## Limits and next acceptance

This is an actual Ubuntu installation/reboot result, with synthetic disabled
provider credentials. Calls, email and billing were disabled. No controlled
telephone call, public DNS, real provider experience or independent engineer
handover is claimed. The application VM and database containers shared one
runner, so this is not an off-host recovery demonstration.

The production host was not changed. Its database separation, restricted runtime
conversion and operating acceptance remain separate. The next installer work
qualifies a real descendant update and a same-schema rollback using this
successful source as the base; that work is not passed by this receipt.
