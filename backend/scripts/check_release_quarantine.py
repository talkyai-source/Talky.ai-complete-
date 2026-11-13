"""Read-only quarantine identity/age gate; never rewrites the protected list."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess

LIST = Path("backend/tests/unit/release_quarantine_20261008.txt")
_ENTRY = re.compile(r"tests/(?:[A-Za-z0-9_-]+/)*test_[A-Za-z0-9_-]+\.py(?:::[A-Za-z_][A-Za-z0-9_]*)*\Z")


def parse_entries(text: str) -> set[str]:
    entries = set()
    for line in text.splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        if not _ENTRY.fullmatch(value) or value in entries:
            raise ValueError("Malformed or duplicate quarantine identity")
        entries.add(value)
    return entries


def evaluate(entries, baseline, first_seen, today, max_age_days=14):
    violations = []
    for entry in sorted(entries):
        if entry not in baseline:
            violations.append(f"new quarantine entry: {entry}")
        introduced = first_seen.get(entry)
        if introduced is None:
            violations.append(f"unknown quarantine age: {entry}")
            continue
        age = (today - introduced).days
        if age < 0:
            violations.append(f"future quarantine history: {entry}")
        elif age > max_age_days:
            violations.append(f"quarantine age {age} days exceeds {max_age_days}: {entry}")
    return violations


def _git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                            text=True, encoding="utf-8", errors="strict", timeout=10)
    if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
        raise ValueError("Git history unavailable or oversized")
    return result.stdout


def history_first_seen(repo, path=LIST):
    # First appearance survives comments, reordering and remove/re-add. Never
    # infer freshness from the filename, file mtime or a rewritten header.
    log = _git(repo, "log", "--reverse", "--format=%H %cI", "HEAD", "--", path.as_posix())
    rows = log.splitlines()
    if len(rows) > 2000:
        raise ValueError("Quarantine history exceeds review bound")
    first_seen = {}
    for row in rows:
        commit, timestamp = row.split(" ", 1)
        introduced = datetime.fromisoformat(timestamp).astimezone(timezone.utc).date()
        # A deletion commit has no blob; inspect its tree instead of treating a
        # failed git-show (which may indicate shallow/corrupt history) as empty.
        present = _git(repo, "ls-tree", "--name-only", commit, "--", path.as_posix()).strip()
        if not present:
            continue
        entries = parse_entries(_git(repo, "show", f"{commit}:{path.as_posix()}"))
        for entry in entries:
            first_seen[entry] = min(first_seen.get(entry, introduced), introduced)
    return first_seen


def _utc_today():
    return datetime.now(timezone.utc).date()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--base-ref", required=True, help="Trusted PR base SHA; no list growth is allowed")
    args = parser.parse_args(argv)
    try:
        repo = args.repo.resolve()
        base = _git(repo, "rev-parse", "--verify", "--end-of-options", args.base_ref + "^{commit}").strip()
        if not re.fullmatch(r"[0-9a-f]{40,64}", base):
            raise ValueError("Unresolved base commit")
        if _git(repo, "rev-parse", "--is-shallow-repository").strip() != "false":
            raise ValueError("Full Git history is required")
        path = repo / LIST
        if path.is_symlink() or path.stat().st_size > 1024 * 1024:
            raise ValueError("Unsafe or oversized quarantine list")
        entries = parse_entries(path.read_text(encoding="utf-8"))
        base_present = _git(repo, "ls-tree", "--name-only", base, "--", LIST.as_posix()).strip()
        baseline = parse_entries(_git(repo, "show", f"{base}:{LIST.as_posix()}")) if base_present else set()
        violations = evaluate(entries, baseline, history_first_seen(repo), _utc_today())
        for violation in violations:
            print(violation)
        print(f"Quarantine policy: {len(entries)} entries, {len(violations)} violations; maximum age 14 days")
        return 1 if violations else 0
    except (OSError, ValueError, subprocess.SubprocessError):
        print("Quarantine policy refused: complete readable list/base/history required.")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
