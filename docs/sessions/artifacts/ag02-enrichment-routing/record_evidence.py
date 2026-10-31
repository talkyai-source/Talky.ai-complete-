"""Freeze local LF source hashes; verify source Git bytes and record artifacts."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
SOURCE = ["backend/app/services/scripts/knowledge/enricher.py", "backend/tests/unit/test_knowledge_enrichment_routing.py"]
DEPENDENCIES = [
    "backend/app/services/scripts/knowledge/ingest_service.py",
    "backend/app/services/scripts/knowledge/md_tree.py",
    "backend/app/services/scripts/knowledge/retrieval.py",
    "backend/app/services/scripts/knowledge/passages.py",
    "backend/app/domain/services/voice_pipeline/kb_budget.py",
    "backend/tests/fixtures/knowledge/ag02_gold.json",
    "backend/scripts/evaluate_ag02_knowledge.py",
    *[f"backend/tests/unit/{name}.py" for name in (
        "test_knowledge_enricher_model", "test_knowledge_ingest_staging",
        "test_reenrich_campaign_knowledge_script", "test_knowledge_relevance",
        "test_knowledge_budget", "test_ag02_gold_matrix")],
]


def digest(value):
    return hashlib.sha256(value.replace(b"\r\n", b"\n")).hexdigest()


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


hashes = {p: digest((ROOT / p).read_bytes()) for p in SOURCE + DEPENDENCIES}
if sys.argv[1:] == ["--freeze"]:
    (HERE / "source-hashes.json").write_text(json.dumps(hashes, indent=2) + "\n", encoding="utf-8")
    print(f"Frozen {len(SOURCE)} source/test and {len(DEPENDENCIES)} dependency hashes")
else:
    source_commit = sys.argv[1]
    assert hashes == json.loads((HERE / "source-hashes.json").read_text(encoding="utf-8"))
    assert all(digest(git("show", source_commit + ":" + p)) == h for p, h in hashes.items())
    base = "731759b1c5b53c29f45f56ef16d760341acf5d6f"
    assert all(digest(git("show", base + ":" + p)) == hashes[p] for p in DEPENDENCIES)
    manifest = {
        "source_commit": source_commit, "base_commit": base,
        "hash_scheme": "SHA-256 after CRLF-to-LF normalization only",
        "source_files": {p: hashes[p] for p in SOURCE},
        "unchanged_dependencies": {p: hashes[p] for p in DEPENDENCIES},
        "artifacts": {p.name: digest(p.read_bytes()) for p in sorted(HERE.iterdir()) if p.is_file() and p.name != "manifest.json"},
        "source_git_parity": "15/15 frozen worktree and source Git hashes match;13 dependencies match base",
        "scope": "Synthetic provider boundary; no SQL/live-provider/model-fidelity/human/customer acceptance",
    }
    (HERE / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"source_commit": source_commit, "source_hashes": len(SOURCE), "unchanged_hashes": len(DEPENDENCIES), "artifact_hashes": len(manifest["artifacts"])}))
