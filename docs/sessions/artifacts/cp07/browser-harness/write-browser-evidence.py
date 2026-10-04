"""Record already-observed synthetic browser output and exact local provenance."""
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

root = Path.cwd()
destination = root / "docs/sessions/artifacts/cp07"
result = json.loads((destination / "browser-validation-run.txt").read_text(encoding="utf-8-sig"))
assert result["status"] == "passed", "Do not turn a failed run into acceptance evidence"
inputs = json.loads((root / "tmp/cp07-browser/bundle-inputs.json").read_text())
sources = sorted(name for name in inputs if name.startswith("Talk-Leee/src/"))
harness = sorted("tmp/cp07-browser/" + name for name in (
    "build.mjs", "harness.tsx", "backend-api.ts", "auth-context.ts",
    "server.mjs", "index.html", "browser-run.js", "write-browser-evidence.py",
))


def aggregate(paths):
    digest = hashlib.sha256()
    for name in paths:
        digest.update(name.encode("utf-8") + b"\0")
        digest.update((root / name).read_bytes() + b"\0")
    return digest.hexdigest()


def sha_file(name):
    return hashlib.sha256((root / name).read_bytes()).hexdigest()


result["recorded_at_utc"] = datetime.now(timezone.utc).isoformat()
result["provenance"] = {
    "base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "bundled_application_status": subprocess.check_output(["git", "status", "--porcelain", "--", *sources], text=True).strip(),
    "qualification": "Aggregate digest pins actual bundled application inputs; empty bundled_application_status means those inputs match the recorded commit. AuthProvider is deliberately aliased out and not browser-tested here.",
    "application_inputs": sources,
    "application_aggregate_sha256": aggregate(sources),
    "harness_inputs": harness,
    "harness_aggregate_sha256": aggregate(harness),
    "aggregate_method": "SHA256 over sorted relative UTF-8 filename + NUL + raw file bytes + NUL for each listed file",
    "bundle_sha256": sha_file("tmp/cp07-browser/bundle.js"),
    "dependency_lock_sha256": sha_file("Talk-Leee/package-lock.json"),
    "node_version": subprocess.check_output(["node", "--version"], text=True).strip(),
    "command": "npx --yes --package @playwright/cli playwright-cli -s=cp07-audit --raw run-code --filename tmp/cp07-browser/browser-run.js",
}
(destination / "browser-validation.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"status": result["status"], "observed_states": len(result["states"]),
                  "fetch_attempts": result["fetchAttempts"], "local_requests": len(result["requests"])}))
