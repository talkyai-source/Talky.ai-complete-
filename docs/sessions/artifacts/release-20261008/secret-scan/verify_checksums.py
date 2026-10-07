"""Recheck reviewed scanner checksums without printing flagged values."""
import hashlib
import json
from pathlib import Path
import re
import subprocess

evidence = Path(__file__).parent
report = json.loads((evidence / "safe-verification.json").read_text(encoding="utf-8"))
scan = {row["fingerprint"] for row in report["findings"]}
cache = {}


def blob(ref, path):
    key = (ref, path)
    if key not in cache:
        completed = subprocess.run(["git", "show", f"{ref}:{path}"], capture_output=True)
        assert completed.returncode == 0, "Referenced Git object unavailable"
        cache[key] = completed.stdout
    return cache[key]


for row in report["findings"]:
    commit, path, rule, number = row["fingerprint"].split(":")
    assert rule == "generic-api-key"
    line = blob(commit, path).decode("utf-8-sig").splitlines()[int(number) - 1]
    match = re.search(r'"([^"\n]+)"\s*:\s*"([^"\n]+)"', line)
    assert match is not None, "Expected checksum entry absent"
    key, value = match.groups()
    assert key == row["referenced_path"]
    assert re.fullmatch("[0-9a-f]{64}", value), "Unexpected flagged value shape"
    proof = row["proof"][0]
    data = blob(proof["ref"], proof["source_path"])
    normalization = proof["normalization"]
    assert normalization in {"raw_git_blob", "LF", "CRLF"}
    if normalization in {"LF", "CRLF"}:
        data = data.replace(b"\r\n", b"\n")
    if normalization == "CRLF":
        data = data.replace(b"\n", b"\r\n")
    assert hashlib.sha256(data).hexdigest() == value, "Referenced checksum does not match"

ignore = Path(".gitleaksignore").read_text(encoding="utf-8")
active = {line.strip() for line in ignore.splitlines() if line.strip() and not line.lstrip().startswith("#")}
assert scan <= active
print(json.dumps({"verified_findings": len(scan), "all_exact_fingerprints_present": True, "flagged_values_printed": False}))
