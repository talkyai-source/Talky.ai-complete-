"""Record frozen LF hashes; later verify them against committed Git source bytes."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
PATHS = [
    "backend/app/api/v1/endpoints/admin/actions.py",
    "backend/app/infrastructure/connectors/calendar/google_calendar.py",
    "backend/app/infrastructure/connectors/calendar/outlook_calendar.py",
    "Admin/frontend/src/components/ActionReceiptPanel.tsx",
    "Admin/frontend/src/lib/api.ts",
    "backend/tests/unit/test_admin_calendar_inspection.py",
    "Admin/frontend/tests/action-receipts.test.mjs",
]
DEPENDENCIES = [
    "backend/app/services/connector_resolver.py",
    "backend/app/services/meeting_service.py",
    "backend/app/services/action_execution.py",
    "backend/tests/unit/test_admin_gmail_inspection.py",
    "backend/app/infrastructure/connectors/calendar/base.py",
]


def digest(value):
    return hashlib.sha256(value.replace(b"\r\n", b"\n")).hexdigest()


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


hashes = {name: digest((ROOT / name).read_bytes()) for name in PATHS + DEPENDENCIES}
if sys.argv[1:] == ["--freeze"]:
    (HERE / "source-hashes.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")
    print(f"Frozen {len(PATHS)} source/test and {len(DEPENDENCIES)} unchanged dependency hashes")
else:
    assert hashes == json.loads((HERE / "source-hashes.json").read_text(encoding="utf-8"))
    source_commit = "2010a1bf10b1305280ba6047099e0af44f3b7147"
    assert all(digest(git("show", f"{source_commit}:{name}")) == value for name, value in hashes.items())
    manifest = {
        "base_commit": "e81f525f6c476e74e22b6a41901e1d50c5c184dc",
        "source_commit": source_commit,
        "hash_scheme": "SHA-256 of bytes after CRLF-to-LF normalization only",
        "source_files": {name: hashes[name] for name in PATHS},
        "unchanged_dependencies": {name: hashes[name] for name in DEPENDENCIES},
        "artifacts": {p.name: digest(p.read_bytes()) for p in sorted(HERE.iterdir()) if p.is_file() and p.name != "manifest.json"},
        "source_git_parity": "12/12 frozen worktree and committed source hashes match",
        "execution_scope": "Synthetic IO only; no provider, actual SQL, browser, deployment or paid-readiness acceptance",
    }
    (HERE / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"source_commit": source_commit, "source_hashes": len(PATHS), "dependency_hashes": len(DEPENDENCIES), "artifact_hashes": len(manifest["artifacts"])}))
