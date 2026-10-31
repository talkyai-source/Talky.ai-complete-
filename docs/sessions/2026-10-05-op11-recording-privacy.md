# OP11 local recording creation privacy

Date: 5 October 2026. Base: `d26f0ff026c76c1ca53ad710fcee249a7116787a`.
Source candidate: `cf4779d8d82e0dae7fd7c10efd7f85ac2c772af4`.
Worktree: `tmp/production-ready-op11-recording-20261005`; branch: `codex/production-ready-op11-recording-20261005`.

## Bounded problem and change

`RecordingService._save_local` already sanitizes generated object-key segments,
checks lexical containment, applies recording policy and offloads its file writer.
The writer previously used default-mode `makedirs` and `open`. New local audio
privacy therefore depended on the process umask and parent directory permissions.
The checked-in ordinary systemd units do not set a private umask. This is a source
contract gap, not evidence of current deployed file permissions or unauthorized
customer access.

The writer now:

- Resolves both configured root and target, matching the existing local
  recording read/delete containment contract. Outside targets fail before any
  directory creation or file truncation.
- Creates each missing directory level with mode `0700` and opens new audio files
  with mode `0600`. It does not change the process-wide umask or chmod any existing
  file/directory. OS permission semantics and restrictive masks still apply.
- Retains binary bytes, existing regular-file overwrite behavior, async offload,
  and authorized owner read/delete. `O_NOFOLLOW` fences the resolved leaf where
  available; Windows also retains binary-mode handling.

A configured root may itself be a symlink to the operator-selected storage.
An existing symlink whose resolved target is inside that root may still address
that target. This does not reject every symlink and does not implement
descriptor-relative traversal against hostile parent-directory swaps. Trusted
parent ownership remains a prerequisite.

## Observed verification

Initial actual temporary-file controls against the unchanged base produced
**3 failures, 3 passes, 4 skips**. Both directory and file symlinks inside the root
could direct the writer outside it; a direct internal-helper outside target was
also accepted. These were real local Windows filesystem operations, with synthetic
audio and temporary targets. They do not establish a remotely reachable tenant
exploit. POSIX permission checks skipped explicitly rather than claiming Windows
ACLs prove Linux mode behavior.

The final five-module batch produced **79 passes, 4 skips, 63 existing datetime
deprecation warnings** in 10.87 seconds. New-module coverage is 8 passes and 4
POSIX skips. It covers byte-preserving owner read/delete, overwrite with a preserved
neighbor, outside and symlink rejection, an explicitly configured symlink root,
an existing parent-file error, and an actual `_save_local` file-open denial without
DB registration, link update or alternate storage fallback. Existing modules cover
recording retention/admission, async offload, scoped endpoint behavior and storage
deletion contracts with synthetic DB/storage seams.

Four authored POSIX controls remain **not run** on this Windows host: new directory
and file modes under masks `000`, `022`, `077`, and preservation of existing modes
when saving new or replacement recordings. Each umask variation runs in a child
process; the test suite does not mutate its own process-wide mask. Run the same new
module in the designated Linux CI/runtime before claiming effective POSIX mode
acceptance. No WSL distribution or Docker runtime was available locally.

Ruff passed with the repository's existing CI rule selection
`--select F --extend-ignore F401,F841`; `git diff --check` passed. A stricter local
`--select F` inspection found two unchanged baseline F401 imports (`struct`,
`BotoCoreError`) outside the owned writer; they were not altered or suppressed by
this change. Root and the LLM agent independently read the writer diff and reported
no material defect. Their review is not an independent test execution count.

## Reproduction and evidence

From the isolated repository root, using the existing Python 3.12.12 interpreter:

```text
python -m pytest backend/tests/unit/test_op11_recording_file_privacy.py backend/tests/unit/test_recording_offload.py backend/tests/unit/test_recordings_endpoint_contract.py backend/tests/unit/test_recording_storage_permanent_delete.py backend/tests/unit/test_op07_recording_retention.py -q
python -m ruff check backend/app/domain/services/recording_service.py backend/tests/unit/test_op11_recording_file_privacy.py --select F --extend-ignore F401,F841
git diff --check
```

Exact interpreter, file inventory, commands and result scopes are in
[validation-manifest.json](artifacts/op11-recording/validation-manifest.json).
The [initial failure log](artifacts/op11-recording/initial.txt),
[final batch log](artifacts/op11-recording/focused-final.txt), and
[CI-rule lint log](artifacts/op11-recording/ruff-ci.txt) are retained. The earlier
77-pass compatibility run is also retained; counts are not added together.

## Remaining operational acceptance

Existing broad file/directory modes are unchanged and require separately approved
operator remediation. Separate service accounts may need explicit reviewed group
or ACL access for read/delete; this patch does not silently broaden permissions.
No service account, systemd unit, storage location, recording policy, database,
provider or deployed host was changed. No customer audio or credentials were used.
Effective Linux permissions, separate-account access, hostile parent ownership
controls, deployed read/delete and legacy-file remediation remain unproved here.
This is a bounded OP11 source repair, not completion of least-privilege rollout or
the production-readiness package.
