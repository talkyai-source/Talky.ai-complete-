"""Offline conversation evidence replay; never a provider or audio approval.

Run from backend with ``python -m scripts.evaluate_ag04_conversations --output
../tmp/ag04-replay.json``. The real runtime consumes captured/authored fake SDK
events. Network access is denied. Exit 1 means an observed failure or invalid
evidence; exit 2 means local controls ran but live/human qualification is absent.
This offline command deliberately has no successful production-approval exit.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import hashlib
import importlib
import json
from pathlib import Path
import socket
import subprocess
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
ENGINES = ("traditional", "native")


def _key(row: dict) -> tuple:
    profile = row["profile"]
    return (
        row["scenario_id"], row["engine"], profile["provider"], profile["model"],
        profile.get("voice"), json.dumps(profile.get("settings", {}), sort_keys=True),
    )


def _valid_identity(row: object) -> bool:
    if not isinstance(row, dict) or not isinstance(row.get("profile"), dict):
        return False
    profile = row["profile"]
    if profile.get("voice") is not None and not isinstance(profile["voice"], str):
        return False
    if not isinstance(profile.get("settings", {}), dict):
        return False
    try:
        json.dumps(profile.get("settings", {}), sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        return False
    return all(isinstance(value, str) and bool(value.strip()) for value in (
        row.get("scenario_id"), row.get("engine"), row["profile"].get("provider"),
        row["profile"].get("model"),
    )) and row["engine"] in ENGINES


def summarize(rows: list[dict], expected_inventory: list[dict], provenance: dict) -> dict:
    """Keep runtime controls, captured semantic failures and unrun gates apart.

    An empty/missing/duplicate replay or malformed finding cannot manufacture a
    green report. Even positive authored output is not live semantic evidence.
    """
    errors: list[str] = []
    expected = Counter()
    actual = Counter()
    for entry in expected_inventory:
        if not _valid_identity(entry):
            errors.append("Invalid declared scenario/profile identity")
            continue
        expected[_key(entry)] += 1
    if not expected:
        errors.append("No scenarios were declared for this run")
    if any(count != 1 for count in expected.values()):
        errors.append("Duplicate declared scenario/profile identity")
    controls, semantics = [], []
    for index, row in enumerate(rows):
        if not _valid_identity(row):
            errors.append(f"Invalid replay identity at row {index}")
            continue
        key = _key(row)
        actual[key] += 1
        if row["profile"].get("mode") != "offline_replay":
            errors.append(f"Unexpected non-offline evidence mode at row {index}")
        required_types = {
            "provenance": dict, "source_facts": list, "requests": list,
            "raw_output": list, "submitted_speech": list, "history": list,
            "end": dict, "effects": dict, "scope": str, "limitations": list,
        }
        for name, expected_type in required_types.items():
            if not isinstance(row.get(name), expected_type):
                errors.append(f"Missing or invalid {name} evidence at row {index}")
        ending = row.get("end")
        if isinstance(ending, dict) and (
            type(ending.get("requested")) is not bool or type(ending.get("dnc_flag")) is not bool
            or type(ending.get("shutdown_count")) is not int or ending["shutdown_count"] < 0
        ):
            errors.append(f"Invalid call-end/opt-out observations at row {index}")
        effects = row.get("effects")
        if isinstance(effects, dict) and (
            not isinstance(effects.get("attempts"), list)
            or type(effects.get("accepted")) is not int or effects["accepted"] < 0
        ):
            errors.append(f"Invalid external-effect observations at row {index}")
        findings = row.get("findings")
        if not isinstance(findings, dict):
            errors.append(f"Missing findings at row {index}")
            continue
        checks = findings.get("control")
        if not isinstance(checks, list) or not checks:
            errors.append(f"No runtime control checks at row {index}")
            checks = []
        for check in checks:
            if (not isinstance(check, dict) or not check.get("id")
                    or type(check.get("pass")) is not bool):
                errors.append(f"Invalid runtime control finding at row {index}")
            else:
                controls.append({"scenario_id": row["scenario_id"], "engine": row["engine"],
                                 "profile": row["profile"], **check})
        reviews = findings.get("semantic")
        if not isinstance(reviews, list) or not reviews:
            errors.append(f"Semantic review status must be explicit at row {index}")
            reviews = []
        for review in reviews:
            if (not isinstance(review, dict) or not review.get("id")
                    or review.get("status") not in {"pass", "fail", "unreviewed"}):
                errors.append(f"Invalid semantic finding at row {index}")
            else:
                semantics.append({"scenario_id": row["scenario_id"], "engine": row["engine"],
                                  "profile": row["profile"], **review})
    missing, unexpected = expected - actual, actual - expected
    if missing:
        errors.append(f"Missing {sum(missing.values())} declared replay rows")
    if unexpected:
        errors.append(f"Unexpected or duplicate replay rows: {sum(unexpected.values())}")
    attempts = provenance.get("blocked_network_attempts", [])
    if attempts:
        errors.append(f"Offline replay attempted network access {len(attempts)} times")
    errors.extend(provenance.get("runner_errors", []))
    failed_controls = [item for item in controls if not item["pass"]]
    failed_semantics = [item for item in semantics if item["status"] == "fail"]
    observed_failure = bool(errors or failed_controls or failed_semantics)
    return {
        "schema_version": 1,
        "mode": "offline_replay",
        "provenance": provenance,
        "status": "failed" if observed_failure else "incomplete",
        "production_approved": False,
        "counts": {
            "declared_profile_scenario_rows": sum(expected.values()),
            "observed_rows": len(rows),
            "distinct_replayed_scenario_ids": len({key[0] for key in actual}),
            "control_checks": len(controls),
            "control_failures": len(failed_controls),
            "captured_or_reviewed_semantic_failures": len(failed_semantics),
            "unreviewed_semantic_findings": sum(item["status"] == "unreviewed" for item in semantics),
        },
        "runtime_control_status": "failed" if errors or failed_controls else "passed",
        "semantic_status": "failed" if failed_semantics else "not_run",
        "live_profile_qualification": "not_run",
        "human_rubric_approval": "not_run",
        "telephone_audio_hearing_and_latency": "not_run",
        "external_effect_verification": "not_run",
        "evidence_errors": errors,
        "failed_controls": failed_controls,
        "failed_semantics": failed_semantics,
        "expected_inventory": expected_inventory,
        "rows": rows,
        "limitations": [
            "Authored SDK output tests runtime handling, not model comprehension or factual fidelity.",
            "A captured historical failure remains evidence about that recorded profile/sample, not a fresh provider response.",
            "Adapter/profile repetitions do not add distinct human scenarios. Compare the proposed matrix separately.",
            "The same reviewed semantic matrix must cover each profile intended for sale; no profile/cohort was approved here.",
            "Fake media receipts do not prove human hearing, PSTN performance, latency or acoustic quality.",
            "Connected effects are intercepted; no email/calendar/CRM/telephone delivery is certified.",
            "No model/prompt/default substitution or production mutation is performed by this command.",
        ],
    }


def exit_code(report: dict) -> int:
    # Never allow a canned positive response or a forged approval field to
    # turn this offline-only report into a successful qualification exit.
    return 1 if report.get("status") == "failed" else 2


def matrix_coverage(matrix: dict, rows: list[dict]) -> dict:
    """Report proposed coverage without counting engine copies as new cases."""
    catalog = {case["id"] for case in matrix["scenarios"]}
    configurations: dict[tuple, dict] = {}
    for row in rows:
        if not _valid_identity(row):
            continue
        config = _key(row)[1:]
        entry = configurations.setdefault(config, {
            "engine": row["engine"], "profile": row["profile"], "ids": set(),
        })
        semantic_ids = row.get("semantic_ids", [row["scenario_id"]])
        if not isinstance(semantic_ids, list) or not all(isinstance(value, str) for value in semantic_ids):
            semantic_ids = []
        entry["ids"].update(semantic_ids)
    observed = set()
    coverage = []
    for entry in configurations.values():
        ids = entry.pop("ids")
        observed |= ids
        coverage.append({**entry, "replayed_catalog_ids": sorted(ids & catalog),
                         "missing_catalog_ids": sorted(catalog - ids),
                         "additional_control_ids": sorted(ids - catalog)})
    return {
        "catalog_status": matrix["status"],
        "catalog_scenarios": len(catalog),
        "distinct_catalog_ids_replayed_anywhere": len(observed & catalog),
        "missing_catalog_ids_everywhere": sorted(catalog - observed),
        "observed_offline_configurations": coverage,
        "ratification": matrix["ratification"],
        "intended_sale_profiles": matrix["intended_profiles"],
        "note": "Replayed means observed locally, not a passed semantic or human case; each intended profile needs its reviewed matrix.",
    }


@contextmanager
def offline_network_guard(attempts: list[str]):
    def denied(*_args, **_kwargs):
        attempts.append("socket_network_operation")
        raise RuntimeError("AG04 offline replay blocks network access")

    targets = ["socket.socket.connect", "socket.socket.connect_ex", "socket.socket.sendto",
               "socket.create_connection", "socket.getaddrinfo"]
    if hasattr(socket.socket, "sendmsg"):
        targets.append("socket.socket.sendmsg")
    with ExitStack() as stack:
        for target in targets:
            stack.enter_context(patch(target, denied))
        yield


def source_provenance(root: Path) -> dict:
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()

    paths = git("ls-files", "backend/app", "backend/tests/qualification",
                "backend/tests/fixtures/conversation", "backend/scripts/evaluate_ag04_conversations.py").splitlines()
    paths += git("ls-files", "--others", "--exclude-standard", "--", "backend/tests/qualification",
                 "backend/tests/fixtures/conversation", "backend/scripts/evaluate_ag04_conversations.py").splitlines()
    hashes = {path: hashlib.sha256((root / path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
              for path in sorted(set(paths)) if (root / path).is_file()}
    return {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "candidate_base_revision": git("rev-parse", "HEAD"),
        "has_uncommitted_source_changes": bool(git("status", "--porcelain", "--", "backend")),
        "source_files_sha256_lf": hashes,
        "provider_calls": 0,
        "telephone_calls": 0,
        "blocked_network_attempts": [],
        "runner_errors": [],
    }


async def run_evaluation(root: Path, engines: list[str]) -> dict:
    provenance = source_provenance(root)
    rows, inventory = [], []
    with offline_network_guard(provenance["blocked_network_attempts"]):
        for engine in engines:
            try:
                module = importlib.import_module(f"tests.qualification.ag04_{engine}")
                inventory.extend(module.case_inventory(root))
                rows.extend(await module.run_cases(root))
            except Exception as exc:
                # Preserve successfully completed engines and write a failed
                # report even if another runner/import is unavailable.
                provenance["runner_errors"].append(f"{engine}: {type(exc).__name__}")
    report = summarize(rows, inventory, provenance)
    matrix_path = root / "docs/production-readiness/ag04-conversation-matrix.json"
    if matrix_path.is_file():
        matrix_bytes = matrix_path.read_bytes()
        matrix = json.loads(matrix_bytes)
        report["proposed_matrix_coverage"] = matrix_coverage(matrix, rows)
        provenance["proposed_matrix_sha256_lf"] = hashlib.sha256(matrix_bytes.replace(b"\r\n", b"\n")).hexdigest()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--engines", nargs="+", choices=ENGINES, default=list(ENGINES))
    args = parser.parse_args()
    report = asyncio.run(run_evaluation(ROOT, list(dict.fromkeys(args.engines))))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in (
        "status", "production_approved", "counts", "runtime_control_status", "semantic_status",
        "live_profile_qualification", "human_rubric_approval", "evidence_errors",
    )}, indent=2))
    return exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
