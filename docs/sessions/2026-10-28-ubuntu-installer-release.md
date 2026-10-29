# Ubuntu installer test release — 28 October 2026

The installer is published for Ubuntu testing. This record covers preparation,
dependency and build qualification; it does not claim that a fully configured
application server or a carrier call has passed acceptance.

## Source and scope

- Repository: <https://github.com/talkyai-source/Talky.ai-complete->.
- Test branch: `codex/ubuntu-installer-rehearsal-20261028`.
- Integrated base: `ad33e42c6729799a3205c4509173632ef3d038a5` from current main.
- Main already contained the previous installer/recovery work through
  `f892ddd3`; the older, dirty Windows checkout was not the published baseline.
- Work was isolated from that checkout. No production deployment, DNS cutover,
  database migration, payment or phone call was performed.

The October 30 deliverable calls for a fresh Linux host, a complete repository
checkout plus protected inputs, the dashboard/Admin/API/C++ gateway/Asterisk
online, health checks and an approved controlled call. It also calls for
independent operator reproduction and handover. Passing preparation and builds
does not close those acceptance requirements or the production-readiness freeze.

## Changes delivered

- Full Linux frontend hosting is the default. npm installs/builds the two
  frontend projects; Bash and Python install/configure the overall platform.
- A preparation helper records the exact source, copies a matching Linux Python
  3.12 hash lock with provenance, collects Ubuntu package candidates, resolves
  the official Node archive checksum and an immutable Redis manifest digest,
  and creates protected input templates outside the checkout.
- The 157-package Python closure was resolved with uv 0.9.17 for Linux x86_64
  and Python 3.12. Installation with hash checking and `pip check` passed on
  Ubuntu CI. This is separate from an application startup or DB qualification.
- Installer corrections cover safe requirement markers, reviewed frontend
  environment isolation, the existing explicit Stripe test-mode contract,
  database identity/revision checks, source-directory permissions and atomic
  secret copies. Caller-supplied database `options` overrides are refused.
- Root can inspect and clone an operator-owned checkout using exact-path Git
  trust confined to the process. Ubuntu's local clone child receives a private
  temporary config, cleaned up even on failure. No persistent global Git trust
  is added. Explicit untracked-file checks preserve the clean-source boundary.
- Clone-first and external-database preparation instructions preserve the
  actual selected Alembic head. The inspected base has one head,
  `0072_allowance_anchor`; historic snapshot stamps are not treated as that head.

## Recorded Ubuntu evidence

The first published candidate was
`5fff9a79f42cde15ed6ba583c34918cb7d8742b1`.
[Its Ubuntu workflow passed](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/40567177393):

- 50 installer/preparation tests, 23 recovery control tests and seven Alembic
  connection tests passed. The recovery tests in this workflow are mocked;
  they do not prove a production restore.
- The complete hashed Python dependency install passed; `pip check` reported no
  broken requirements and selected native/provider modules imported.
- The actual C++ release build passed all four CTest suites.
- Both customer and Admin frontend builds passed. This first run used the
  dashboard's development application flag; the final run below supersedes
  that result with the production application flag.
- Preparation used real public Node/Redis metadata and real Ubuntu APT candidates.

An isolated WSL2 distribution named `Talklee-Installer-Test` was installed on
this Windows machine. It reports Ubuntu 24.04.5 LTS, x86_64, Python 3.12.3 and
systemd. The first 50 installer/preparation tests also passed there. A public
GitHub clone of the first candidate succeeded, its preparation helper produced
protected drafts, and APT simulated installation of all 18 pinned top-level
packages successfully. This was a simulation; it did not install or start the
full application stack. The distro's official Ubuntu APT mirror URLs were
changed from HTTP to HTTPS after the image's initial HTTP requests failed;
Ubuntu signature verification remained enabled.

The isolated source is `/var/lib/talklee-installer-rehearsal/source`. Initial
placeholder inputs are in `/var/lib/talklee-installer-rehearsal/initial-inputs`.
Logs are retained under `/var/tmp/talklee-*`. These are test artifacts and do
not contain designated production credentials.

## Final test candidate

The follow-up code candidate is
`a152195b8b15257f33837932ee96917d7e3d3ba6`. All 54 installer/preparation tests
passed on Windows and on the local Ubuntu 24.04 system. An additional Ubuntu
fixture used an actual UID-65534-owned repository: untrusted Git access failed
with exit 128, the root installer clone succeeded, and both its exact commit
and the source Git configuration were preserved. Normal and linked-worktree
clone regressions, inherited-config isolation, private permissions and cleanup
after a failed clone are covered in the suite.

The public Ubuntu clone was fetched and detached at this exact candidate, then
preparation succeeded again using real metadata. Final draft inputs are in
`/var/lib/talklee-installer-rehearsal/inputs`; their source SHA, Linux frontend
mode, `database_prepared=false`, directory mode `0700` and file modes `0600`
were checked. `plan` succeeded. `validate` correctly returned exit 2 with
`Prepare the external database and verify its schema before installation`.
No full `apply` was attempted with placeholder configuration.

The final workflow is
<https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/40570118355>.
All three jobs passed: the hashed Python install and dependency check,
54 installer/preparation tests, 23 recovery control tests, seven Alembic
connection tests, real dependency-metadata preparation, all four C++ gateway
CTest suites, and both frontend builds. The frontends used the production
application flag, authentication required, development Admin authentication
disabled, and an unused loopback HTTPS API URL. These are build artifacts for
verification, not bundles configured for a deployed API. The workflow retains
nonsecret plan, APT-version, preparation and lock-provenance artifacts.

An independent code review within the implementation team checked temporary
trust scope, permissions, inherited environment isolation and failure cleanup.
Its clone-command fail-fast and hidden-untracked-file findings were corrected.
This does not substitute for the independent engineer's installation acceptance.
This report and the execution-tracker update are a documentation-only follow-up
to the tested code candidate; the application/installer revision above is the
one to pin for the Ubuntu trial.

## Remaining operating acceptance

The required next input is a designated clean Ubuntu VM and its environment
configuration: API/dashboard/Admin domains, restricted network ranges, prepared
external PostgreSQL with verified TLS and runtime grants, retained encryption
keys where data is restored, provider/SMTP credentials, public TLS material,
and reviewed carrier/Asterisk configuration. Use the
[input preparation guide](../../scripts/installation/PREPARE-UBUNTU.md).

With those inputs, run `validate`, then `apply` from a root shell with
`umask 0022`, retain the install receipt and versions, verify HTTPS/services,
reboot, and make the approved controlled call. External database restore,
off-host backup/recovery, update/rollback, independent operator reproduction
and handover recordings remain required. Stripe and Admin business acceptance
remain their existing workstreams. OP10/OP12 and the overall plan remain open.
