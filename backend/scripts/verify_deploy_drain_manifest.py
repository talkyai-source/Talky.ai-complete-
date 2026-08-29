#!/usr/bin/env python3
"""Verify and consume a production traffic-drain approval manifest.

This is deliberately a local release-client gate.  It binds independently
recorded topology/change evidence to the exact candidate SHA and prevents
accidental replay through an atomic local ledger.  It does not inspect, freeze,
or prove the live carrier, Asterisk, Redis, or database state itself.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

_FULL_SHA_RE = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
_DIGEST_RE = re.compile(r"[0-9a-f]{64}\Z")
_MANIFEST_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,127}\Z")
_SAFE_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9@._:/+\-=]{2,255}\Z")
_UTC_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z"
)
_MAX_MANIFEST_BYTES = 64 * 1024
_MAX_ISSUED_AGE = timedelta(minutes=15)
_MAX_VALIDITY = timedelta(minutes=30)
_MAX_CLOCK_SKEW = timedelta(seconds=60)


class ManifestError(ValueError):
    """A fail-closed drain-manifest validation error."""


def _exact_keys(value: Any, expected: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{path} must be a JSON object")
    actual = set(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ManifestError(f"{path} has invalid keys (missing={missing}, extra={extra})")
    return value


def _safe_ref(value: Any, path: str) -> str:
    if not isinstance(value, str) or _SAFE_REF_RE.fullmatch(value) is None:
        raise ManifestError(f"{path} must be a 3-256 character immutable evidence reference")
    return value


def _manifest_id(value: Any) -> str:
    if not isinstance(value, str) or _MANIFEST_ID_RE.fullmatch(value) is None:
        raise ManifestError(
            "manifest.manifest_id must be 3-128 safe filename characters without separators"
        )
    return value


def _utc_timestamp(value: Any, path: str) -> datetime:
    if not isinstance(value, str) or _UTC_TIMESTAMP_RE.fullmatch(value) is None:
        raise ManifestError(f"{path} must be an RFC3339 UTC timestamp ending in Z")
    try:
        return datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ManifestError(f"{path} is not a valid timestamp") from exc


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ManifestError(f"duplicate JSON key is forbidden: {key}")
        result[key] = value
    return result


def _read_manifest(path: Path) -> tuple[bytes, dict[str, Any]]:
    if path.is_symlink():
        raise ManifestError("drain manifest must not be a symlink")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise ManifestError(f"cannot open drain manifest: {exc}") from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ManifestError("drain manifest must be a regular file")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            raw = handle.read(_MAX_MANIFEST_BYTES + 1)
    finally:
        os.close(fd)
    if len(raw) > _MAX_MANIFEST_BYTES:
        raise ManifestError("drain manifest exceeds 64 KiB")
    try:
        decoded = raw.decode("utf-8")
        value = json.loads(decoded, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError("drain manifest must be valid UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ManifestError("drain manifest root must be a JSON object")
    return raw, value


def verify_and_consume_manifest(
    *,
    manifest_path: Path,
    candidate_sha: str,
    expected_sha256: str,
    replay_dir: Path,
    now: datetime | None = None,
) -> str:
    """Validate one manifest and atomically mark its ID as consumed."""

    if _FULL_SHA_RE.fullmatch(candidate_sha) is None:
        raise ManifestError("candidate SHA must be one full lowercase 40- or 64-hex SHA")
    if _DIGEST_RE.fullmatch(expected_sha256) is None:
        raise ManifestError("expected manifest SHA-256 must be exactly 64 lowercase hex characters")

    raw, root = _read_manifest(manifest_path)
    actual_digest = hashlib.sha256(raw).hexdigest()
    if not hmac.compare_digest(actual_digest, expected_sha256):
        raise ManifestError("supplied manifest SHA-256 does not match the manifest bytes")

    root = _exact_keys(
        root,
        {
            "schema_version",
            "manifest_id",
            "candidate_sha",
            "environment",
            "issued_at",
            "expires_at",
            "traffic",
            "evidence",
            "approvers",
        },
        "manifest",
    )
    if type(root["schema_version"]) is not int or root["schema_version"] != 1:
        raise ManifestError("manifest.schema_version must be integer 1")
    manifest_id = _manifest_id(root["manifest_id"])
    if root["candidate_sha"] != candidate_sha:
        raise ManifestError("manifest candidate_sha does not equal the frozen deploy candidate")
    if root["environment"] != "production":
        raise ManifestError("manifest environment must be exactly production")

    issued_at = _utc_timestamp(root["issued_at"], "manifest.issued_at")
    expires_at = _utc_timestamp(root["expires_at"], "manifest.expires_at")
    current = now or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ManifestError("verification clock must be timezone-aware")
    current = current.astimezone(UTC)
    if issued_at > current + _MAX_CLOCK_SKEW:
        raise ManifestError("manifest issued_at is too far in the future")
    if current - issued_at > _MAX_ISSUED_AGE:
        raise ManifestError("manifest issued_at is older than 15 minutes")
    if expires_at <= current:
        raise ManifestError("manifest has expired")
    if expires_at <= issued_at or expires_at - issued_at > _MAX_VALIDITY:
        raise ManifestError("manifest validity must be positive and no longer than 30 minutes")

    traffic = _exact_keys(
        root["traffic"],
        {"ingress_disabled", "outbound_origination_disabled", "active_counts"},
        "manifest.traffic",
    )
    if traffic["ingress_disabled"] is not True:
        raise ManifestError("manifest.traffic.ingress_disabled must be true")
    if traffic["outbound_origination_disabled"] is not True:
        raise ManifestError("manifest.traffic.outbound_origination_disabled must be true")
    counts = _exact_keys(
        traffic["active_counts"],
        {"gateway_sessions", "asterisk_legs", "redis_leases", "db_live_calls"},
        "manifest.traffic.active_counts",
    )
    for name, value in counts.items():
        if type(value) is not int or value != 0:
            raise ManifestError(f"manifest.traffic.active_counts.{name} must be integer zero")

    evidence = _exact_keys(
        root["evidence"], {"topology_ref", "change_ref"}, "manifest.evidence"
    )
    _safe_ref(evidence["topology_ref"], "manifest.evidence.topology_ref")
    _safe_ref(evidence["change_ref"], "manifest.evidence.change_ref")
    if evidence["topology_ref"].casefold() == evidence["change_ref"].casefold():
        raise ManifestError("topology_ref and change_ref must be independent evidence references")

    approvers = root["approvers"]
    if not isinstance(approvers, list) or len(approvers) < 2:
        raise ManifestError("manifest.approvers must contain at least two independent approvers")
    principals: set[str] = set()
    roles: set[str] = set()
    approval_refs: set[str] = set()
    for index, item in enumerate(approvers):
        approver = _exact_keys(item, {"principal", "role", "approval_ref"}, f"approvers[{index}]")
        principal = _safe_ref(approver["principal"], f"approvers[{index}].principal").casefold()
        role = _safe_ref(approver["role"], f"approvers[{index}].role").casefold()
        approval_ref = _safe_ref(
            approver["approval_ref"], f"approvers[{index}].approval_ref"
        ).casefold()
        if principal in principals or role in roles or approval_ref in approval_refs:
            raise ManifestError("approvers must have distinct principals, roles, and approval refs")
        principals.add(principal)
        roles.add(role)
        approval_refs.add(approval_ref)

    try:
        replay_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        marker = replay_dir / f"{manifest_id}.used"
        marker_fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ManifestError(f"drain manifest {manifest_id} has already been consumed") from exc
    except OSError as exc:
        raise ManifestError(f"cannot update drain-manifest replay ledger: {exc}") from exc
    try:
        marker_body = f"sha256={actual_digest}\ncandidate_sha={candidate_sha}\n"
        os.write(marker_fd, marker_body.encode("ascii"))
        os.fsync(marker_fd)
    finally:
        os.close(marker_fd)
    return manifest_id


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--candidate-sha", required=True)
    parser.add_argument("--expected-sha256", required=True)
    parser.add_argument("--replay-dir", required=True, type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        manifest_id = verify_and_consume_manifest(
            manifest_path=args.manifest,
            candidate_sha=args.candidate_sha,
            expected_sha256=args.expected_sha256,
            replay_dir=args.replay_dir,
        )
    except ManifestError as exc:
        print(f"drain manifest rejected: {exc}", file=sys.stderr)
        return 2
    print(f"verified and consumed production drain manifest: {manifest_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
