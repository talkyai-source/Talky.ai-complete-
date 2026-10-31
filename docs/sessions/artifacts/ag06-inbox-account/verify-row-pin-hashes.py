"""Reproduce the historical manifest encoding error without reading mutable source."""
import argparse
import hashlib
import json
import locale
from pathlib import Path
import subprocess
import sys

SOURCE_COMMIT = "0f69c0f736ec0080ef68a575ccb13e9a6dbe1384"
EVIDENCE_COMMIT = "8141266afe40e86b14cd3d56655edd69c6e0b5b9"
MANIFEST_PATH = "docs/sessions/artifacts/ag06-inbox-account/row-pin-manifest.json"


def sha(value):
    return hashlib.sha256(value).hexdigest()


def git_blob(repo, commit, path):
    return subprocess.check_output(["git", "show", f"{commit}:{path}"], cwd=repo)


def report(repo):
    manifest_bytes = git_blob(repo, EVIDENCE_COMMIT, MANIFEST_PATH)
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    rows = []
    for path, recorded in manifest["normalized_source_sha256"].items():
        raw = git_blob(repo, SOURCE_COMMIT, path)
        normalized = raw.decode("utf-8").replace("\r\n", "\n").encode("utf-8")
        legacy = raw.decode("cp1252").replace("\r\n", "\n").encode("utf-8")
        row = {
            "path": path,
            "recorded_sha256": recorded,
            "committed_git_bytes_sha256": sha(raw),
            "explicit_utf8_lf_sha256": sha(normalized),
            "cp1252_decode_then_utf8_encode_sha256": sha(legacy),
            "recorded_matches_legacy_algorithm": recorded == sha(legacy),
            "recorded_matches_explicit_utf8": recorded == sha(normalized),
            "non_ascii_bytes": sum(value > 127 for value in raw),
        }
        assert row["recorded_matches_legacy_algorithm"], path
        rows.append(row)
    working_manifest = (repo / MANIFEST_PATH).read_bytes().decode("utf-8").replace("\r\n", "\n").encode("utf-8")
    assert working_manifest == manifest_bytes
    return {
        "source_commit": SOURCE_COMMIT,
        "historical_evidence_commit": EVIDENCE_COMMIT,
        "historical_manifest_path": MANIFEST_PATH,
        "historical_manifest_git_sha256": sha(manifest_bytes),
        "historical_manifest_unchanged": True,
        "interpreter": {"preferred_encoding": locale.getpreferredencoding(False), "utf8_mode": sys.flags.utf8_mode, "platform": sys.platform},
        "cause": "Path.read_text() used Windows CP1252 while str.encode() used UTF-8. Non-ASCII UTF-8 punctuation changed the hashed representation; no source content changed.",
        "all_recorded_hashes_reproduced_from_exact_committed_source": all(row["recorded_matches_legacy_algorithm"] for row in rows),
        "files": rows,
        "additional_source_test_run": False,
        "rerun_reason": "No post-test source delta indicated: all four recorded hashes exactly reproduce from the committed source under the historical encoding algorithm. This command validates fingerprints, not runtime behavior.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[4])
    args = parser.parse_args()
    print(json.dumps(report(args.repo), indent=2))
