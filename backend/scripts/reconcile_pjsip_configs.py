"""Reconcile the complete repository-owned Asterisk edge configuration.

The database is the authority for generated PJSIP trunk files. An account is
routed only when the reviewed carrier inventory agrees with a currently active
``inbound_did_assignments`` row joined to the same tenant, trunk, and a verified
``tenant_phone_numbers`` row. Caller ID and trunk metadata are never accepted
as routing proof.

The default command is check-only. It writes a secured candidate that includes
a byte-preserving transform of the shared carrier endpoint's base ``context``
line, then emits a content-free drift summary. Applying requires the exact
digest from that candidate. Check and apply are serialized; apply proves zero
live channels, atomically replaces each managed file, reloads PJSIP and the
dialplan, verifies the runtime objects, and restores the complete prior set on
failure.

Run from ``backend/`` with the production environment loaded::

    venv/bin/python scripts/reconcile_pjsip_configs.py
    venv/bin/python scripts/reconcile_pjsip_configs.py \
        --apply --expected-digest <digest-from-check>

No password, ciphertext, rendered content, account, DID, or Asterisk output is
printed.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncpg

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND_ROOT.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from app.core.db_utils import acquire_with_tenant  # noqa: E402
from app.domain.services.phone_number_normalizer import (  # noqa: E402
    validate_e164_strict,
)
from app.domain.services.telephony.trunk_resolver import (  # noqa: E402
    platform_default_trunk_name,
)
from app.infrastructure.connectors.encryption import (  # noqa: E402
    get_encryption_service,
)
from app.infrastructure.telephony.pjsip_config_generator import (  # noqa: E402
    build_trunk_config_input,
    render_trunk_conf,
)

DEFAULT_CANDIDATE_DIR = Path(
    os.getenv("TALKY_ASTERISK_CANDIDATE_DIR", "/opt/talky/runtime/asterisk-candidate")
)
DEFAULT_PJSIP_LIVE_DIR = Path(
    os.getenv("TELEPHONY_PJSIP_CONF_DIR", "/etc/asterisk/pjsip.d")
)
DEFAULT_BASE_PJSIP_LIVE_PATH = Path(
    os.getenv("TALKY_ASTERISK_BASE_PJSIP_PATH", "/etc/asterisk/pjsip.conf")
)
DEFAULT_DIALPLAN_LIVE_PATH = Path(
    os.getenv(
        "TALKY_INBOUND_DIALPLAN_PATH",
        "/etc/asterisk/extensions.d/talky-inbound.conf",
    )
)
DEFAULT_DIALPLAN_TEMPLATE = (
    _REPO_ROOT / "telephony" / "asterisk" / "conf" / "talky-inbound.conf"
)
DEFAULT_VERIFIED_ACCOUNT_MAP = (
    _REPO_ROOT
    / "telephony"
    / "asterisk"
    / "conf"
    / "verified-carrier-account-dids.json"
)
DEFAULT_LOCK_PATH = Path(
    os.getenv("TALKY_ASTERISK_RECONCILE_LOCK", "/run/lock/talky-asterisk-reconcile.lock")
)

PJSIP_PREFIX = "pjsip.d/"
BASE_PJSIP_NAME = "pjsip.conf"
DIALPLAN_NAME = "extensions.d/talky-inbound.conf"
_MANAGED_GLOB = "trunk-*.conf"
_TRUNK_FILENAME_RE = re.compile(
    r"^trunk-[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-"
    r"[89ab][0-9a-f]{3}-[0-9a-f]{12}\.conf$",
    re.IGNORECASE,
)
_ACCOUNT_RE = re.compile(r"^[0-9]{1,64}$")
_ACTIVE_CHANNELS_RE = re.compile(r"(?im)^\s*(\d+)\s+active channels?\b")
_RELOAD_FAILURE_RE = re.compile(
    r"(?i)\b(?:error|failed|failure|unable|not found|no such command)\b"
)
_ROUTE_MARKER = "; TALKY_GENERATED_ACCOUNT_ROUTES"
_SHARED_ENDPOINT_SECTION_RE = re.compile(
    rb"(?ms)^[ \t]*\[blazedigitel-endpoint\][ \t]*(?:\r?\n|\Z).*?"
    rb"(?=^[ \t]*\[[^\]\r\n]+\][ \t]*(?:\r?\n|\Z)|\Z)"
)
_CONTEXT_LINE_RE = re.compile(
    rb"(?m)^(?P<indent>[ \t]*)context[ \t]*=[^\r\n]*(?P<cr>\r?)$"
)


class PJSIPReconciliationError(RuntimeError):
    """Base class for content-free operator-facing reconciliation failures."""


class UnsafeInboundMappingError(PJSIPReconciliationError):
    """An account-to-DID route lacks one unambiguous tenant-owned proof."""


class CandidateDigestMismatch(PJSIPReconciliationError):
    """The approved digest does not match the freshly generated candidate."""


class ActiveChannelsError(PJSIPReconciliationError):
    """A zero-active-channel state could not be proved."""


class ReconciliationLocked(PJSIPReconciliationError):
    """Another check or apply process owns the writer lock."""


class PJSIPReloadFailed(PJSIPReconciliationError):
    """Asterisk did not accept or expose the candidate at runtime."""


class PJSIPApplyFailed(PJSIPReconciliationError):
    """The file transaction failed and was rolled back."""


@dataclass(frozen=True, order=True)
class InboundRoute:
    account: str
    did: str
    tenant_id: str
    trunk_id: str
    assignment_id: str


@dataclass(frozen=True)
class CandidateSet:
    directory: Path
    files: Mapping[str, bytes]
    routes: tuple[InboundRoute, ...]
    digest: str


@dataclass(frozen=True)
class ReconciliationSummary:
    digest: str
    missing: tuple[str, ...]
    changed: tuple[str, ...]
    unexpected: tuple[str, ...]

    @property
    def drifted(self) -> bool:
        return bool(self.missing or self.changed or self.unexpected)

    def to_safe_dict(self) -> dict[str, object]:
        """Return managed names/counts only; rendered values never leave."""
        return {
            "digest": self.digest,
            "drifted": self.drifted,
            "missing": list(self.missing),
            "changed": list(self.changed),
            "unexpected": list(self.unexpected),
            "counts": {
                "missing": len(self.missing),
                "changed": len(self.changed),
                "unexpected": len(self.unexpected),
            },
        }


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class _FileSnapshot:
    content: bytes
    mode: int
    uid: int | None
    gid: int | None


@dataclass(frozen=True)
class LiveOwnership:
    pjsip_uid: int
    pjsip_gid: int
    dialplan_uid: int
    dialplan_gid: int


def _metadata(row: Mapping[str, Any]) -> dict[str, Any]:
    raw = row.get("metadata")
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            return parsed
    return {}


def _canonical_uuid(raw: object, *, label: str) -> str:
    try:
        return str(uuid.UUID(str(raw)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise PJSIPReconciliationError(f"{label} has an invalid id") from exc


def _canonical_trunk_id(raw: object) -> str:
    return _canonical_uuid(raw, label="tenant SIP trunk")


def _safe_trunk_label(row: Mapping[str, Any]) -> str:
    return f"trunk-{_canonical_trunk_id(row.get('id'))}"


def _as_utc(raw: object, *, label: str) -> datetime:
    if not isinstance(raw, datetime):
        raise UnsafeInboundMappingError(f"{label} has an invalid validity window")
    if raw.tzinfo is None:
        return raw.replace(tzinfo=timezone.utc)
    return raw.astimezone(timezone.utc)


def _assignment_is_current(row: Mapping[str, Any], observed_at: datetime) -> bool:
    if str(row.get("assignment_status") or "").strip().lower() != "active":
        return False
    valid_from = _as_utc(row.get("assignment_valid_from"), label="assignment")
    if valid_from > observed_at:
        return False
    valid_to = row.get("assignment_valid_to")
    return valid_to is None or _as_utc(valid_to, label="assignment") > observed_at


def _route_from_row(
    row: Mapping[str, Any],
    *,
    trunk_id: str,
    tenant_id: str,
    observed_at: datetime,
) -> InboundRoute | None:
    assignment_id_raw = row.get("assignment_id")
    if assignment_id_raw is None:
        return None
    assignment_id = _canonical_uuid(assignment_id_raw, label="inbound assignment")
    if not _assignment_is_current(row, observed_at):
        return None

    assignment_tenant_id = _canonical_uuid(
        row.get("assignment_tenant_id"), label="inbound assignment tenant"
    )
    assignment_trunk_id = _canonical_trunk_id(row.get("assignment_trunk_id"))
    if assignment_tenant_id != tenant_id or assignment_trunk_id != trunk_id:
        raise UnsafeInboundMappingError(
            f"{_safe_trunk_label(row)} has an inbound assignment tenant conflict"
        )
    if str(row.get("direction") or "").strip().lower() not in {"inbound", "both"}:
        raise UnsafeInboundMappingError(
            f"{_safe_trunk_label(row)} has an assignment on a non-inbound trunk"
        )
    if str(row.get("phone_status") or "").strip().lower() != "verified":
        return None

    try:
        did = validate_e164_strict(str(row.get("assignment_did") or ""))
        verified_did = validate_e164_strict(str(row.get("verified_did") or ""))
    except (TypeError, ValueError) as exc:
        raise UnsafeInboundMappingError(
            f"{_safe_trunk_label(row)} has an invalid verified DID proof"
        ) from exc
    if did != verified_did:
        raise UnsafeInboundMappingError(
            f"{_safe_trunk_label(row)} has a conflicting verified DID proof"
        )

    account = str(row.get("auth_username") or "").strip()
    if not _ACCOUNT_RE.fullmatch(account):
        raise UnsafeInboundMappingError(
            f"{_safe_trunk_label(row)} has an unroutable carrier account"
        )
    return InboundRoute(
        account=account,
        did=did,
        tenant_id=tenant_id,
        trunk_id=trunk_id,
        assignment_id=assignment_id,
    )


async def fetch_reconciliation_rows(
    pool: asyncpg.Pool,
    *,
    platform_trunk_name: str,
) -> list[Mapping[str, Any]]:
    """Fetch trunks and their current verified assignment proof in one query."""
    async with acquire_with_tenant(pool, None) as conn:
        rows = await conn.fetch(
            """
            WITH current_verified_assignments AS (
                SELECT
                    a.id AS assignment_id,
                    a.tenant_id AS assignment_tenant_id,
                    a.sip_trunk_id AS assignment_trunk_id,
                    a.canonical_did AS assignment_did,
                    a.status AS assignment_status,
                    a.valid_from AS assignment_valid_from,
                    a.valid_to AS assignment_valid_to,
                    pn.status AS phone_status,
                    pn.e164 AS verified_did
                FROM inbound_did_assignments a
                JOIN tenant_phone_numbers pn
                  ON pn.id = a.phone_number_id
                 AND pn.tenant_id = a.tenant_id
                 AND pn.e164 = a.canonical_did
                 AND pn.status = 'verified'
                WHERE a.status = 'active'
                  AND a.valid_from <= CURRENT_TIMESTAMP
                  AND (a.valid_to IS NULL OR a.valid_to > CURRENT_TIMESTAMP)
            )
            SELECT
                st.id,
                st.tenant_id,
                st.trunk_name,
                st.sip_domain,
                st.port,
                st.transport,
                st.direction,
                st.auth_username,
                st.auth_password_encrypted,
                st.metadata,
                a.assignment_id,
                a.assignment_tenant_id,
                a.assignment_trunk_id,
                a.assignment_did,
                a.assignment_status,
                a.assignment_valid_from,
                a.assignment_valid_to,
                a.phone_status,
                a.verified_did,
                CURRENT_TIMESTAMP AS observed_at
            FROM tenant_sip_trunks st
            LEFT JOIN current_verified_assignments a
              ON a.assignment_trunk_id = st.id
             AND a.assignment_tenant_id = st.tenant_id
            WHERE st.is_active = TRUE
              AND lower(btrim(st.trunk_name)) <> lower($1)
            ORDER BY st.id, a.assignment_id
            """,
            platform_trunk_name,
        )
    return list(rows)


def render_inbound_dialplan(
    routes: Iterable[InboundRoute],
    *,
    template: str,
) -> bytes:
    """Insert exact Request-URI account routes before the preserved ``_.``."""
    if template.count(_ROUTE_MARKER) != 1:
        raise PJSIPReconciliationError(
            "managed inbound dialplan template has an invalid route marker"
        )
    route_lines: list[str] = [_ROUTE_MARKER]
    for route in sorted(routes):
        route_lines.extend(
            [
                f"exten => {route.account},1,NoOp(Inbound mapped carrier account)",
                " same => n,Ringing()",
                f" same => n,Stasis(talky_ai,inbound,{route.did},${{CONTEXT}})",
                " same => n,Hangup()",
                "",
            ]
        )
    rendered = template.replace(_ROUTE_MARKER, "\n".join(route_lines), 1)
    if "exten => _.,1," not in rendered:
        raise PJSIPReconciliationError(
            "managed inbound dialplan lost the required catch-all pattern"
        )
    return rendered.encode("utf-8")


def render_shared_endpoint_base_pjsip(source: bytes) -> bytes:
    """Bind the one shared carrier endpoint to the managed inbound context.

    The base file also contains operator credentials and transport settings, so
    deploy must not regenerate it from guesses. Only the single endpoint
    ``context`` line is replaced; a missing or ambiguous section fails closed.
    The resulting full file is included in the candidate digest and rollback
    snapshot with the other managed files.
    """
    sections = list(_SHARED_ENDPOINT_SECTION_RE.finditer(bytes(source)))
    if len(sections) != 1:
        raise PJSIPReconciliationError(
            "shared carrier endpoint is missing or ambiguous in base PJSIP config"
        )
    section = sections[0]
    context_lines = list(_CONTEXT_LINE_RE.finditer(section.group(0)))
    if len(context_lines) != 1:
        raise PJSIPReconciliationError(
            "shared carrier endpoint context is missing or ambiguous"
        )
    context = context_lines[0]
    start = section.start() + context.start()
    end = section.start() + context.end()
    replacement = (
        context.group("indent")
        + b"context=from-talky-inbound"
        + context.group("cr")
    )
    return bytes(source)[:start] + replacement + bytes(source)[end:]


def load_verified_account_dids(path: Path) -> dict[str, str]:
    """Load the reviewed carrier inventory without repairing any value."""
    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        parsed: dict[str, object] = {}
        for key, value in pairs:
            if key in parsed:
                raise PJSIPReconciliationError(
                    "verified carrier account inventory contains a duplicate account"
                )
            parsed[key] = value
        return parsed

    try:
        raw = json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicate_keys,
        )
    except PJSIPReconciliationError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise PJSIPReconciliationError(
            "verified carrier account inventory is unreadable"
        ) from exc
    if not isinstance(raw, dict):
        raise PJSIPReconciliationError(
            "verified carrier account inventory must be an object"
        )
    verified: dict[str, str] = {}
    for account_raw, did_raw in raw.items():
        account = str(account_raw).strip()
        if not _ACCOUNT_RE.fullmatch(account):
            raise PJSIPReconciliationError(
                "verified carrier account inventory contains an invalid account"
            )
        try:
            did = validate_e164_strict(str(did_raw))
        except (TypeError, ValueError) as exc:
            raise PJSIPReconciliationError(
                "verified carrier account inventory contains an invalid DID"
            ) from exc
        if account in verified:
            raise PJSIPReconciliationError(
                "verified carrier account inventory contains a duplicate account"
            )
        verified[account] = did
    return verified


def compute_candidate_digest(files: Mapping[str, bytes]) -> str:
    """Hash sorted virtual filename + content pairs with length framing."""
    digest = hashlib.sha256()
    for name in sorted(files):
        encoded_name = name.encode("utf-8")
        content = bytes(files[name])
        digest.update(len(encoded_name).to_bytes(8, "big"))
        digest.update(encoded_name)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _validate_virtual_name(name: str) -> None:
    if name in {BASE_PJSIP_NAME, DIALPLAN_NAME}:
        return
    if name.startswith(PJSIP_PREFIX) and _TRUNK_FILENAME_RE.fullmatch(
        name.removeprefix(PJSIP_PREFIX)
    ):
        return
    raise PJSIPReconciliationError("candidate contains an invalid managed filename")


def _fsync_directory(path: Path) -> None:
    if os.name != "posix":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write(
    path: Path,
    content: bytes,
    *,
    mode: int,
    uid: int | None = None,
    gid: int | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        os.chmod(temporary, mode)
        if os.name == "posix" and uid is not None and gid is not None:
            os.fchown(descriptor, uid, gid)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, mode)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            os.unlink(temporary)


def _materialize_candidate(directory: Path, files: Mapping[str, bytes]) -> None:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if directory.is_symlink():
        raise PJSIPReconciliationError("candidate directory must not be a symlink")
    os.chmod(directory, 0o700)
    expected_paths: set[Path] = set()
    for name in sorted(files):
        _validate_virtual_name(name)
        target = directory / Path(name)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if target.parent.is_symlink():
            raise PJSIPReconciliationError("candidate subdirectory must not be a symlink")
        os.chmod(target.parent, 0o700)
        _atomic_write(target, files[name], mode=0o600)
        expected_paths.add(target)

    pjsip_candidate = directory / PJSIP_PREFIX.removesuffix("/")
    if pjsip_candidate.exists():
        for path in sorted(pjsip_candidate.glob(_MANAGED_GLOB)):
            if path not in expected_paths:
                if path.is_symlink():
                    raise PJSIPReconciliationError(
                        "candidate directory contains a managed symlink"
                    )
                path.unlink()
        _fsync_directory(pjsip_candidate)
    base_candidate = directory / BASE_PJSIP_NAME
    if base_candidate.exists() and base_candidate not in expected_paths:
        if base_candidate.is_symlink() or not base_candidate.is_file():
            raise PJSIPReconciliationError(
                "candidate directory contains an invalid base PJSIP file"
            )
        base_candidate.unlink()
    _fsync_directory(directory)


def build_candidate_set(
    rows: Iterable[Mapping[str, Any]],
    *,
    candidate_dir: Path,
    decrypt_password: Callable[[str], str],
    observed_at: datetime | None = None,
    dialplan_template: Path = DEFAULT_DIALPLAN_TEMPLATE,
    verified_account_dids: Mapping[str, str] | None = None,
    base_pjsip_source: Path | None = None,
) -> CandidateSet:
    """Render one deduplicated trunk set and one assignment-owned dialplan."""
    source_rows = [dict(row) for row in rows]
    if observed_at is None:
        observed_values = [row.get("observed_at") for row in source_rows]
        observed_at = next(
            (value for value in observed_values if isinstance(value, datetime)),
            datetime.now(timezone.utc),
        )
    observed_at = _as_utc(observed_at, label="reconciliation observation")
    if verified_account_dids is None:
        verified_account_dids = load_verified_account_dids(
            DEFAULT_VERIFIED_ACCOUNT_MAP
        )
    else:
        # Tests/operators may inject an already reviewed inventory, but it is
        # still validated with the same no-repair contract as the file.
        validated_inventory: dict[str, str] = {}
        for account_raw, did_raw in verified_account_dids.items():
            account = str(account_raw).strip()
            if not _ACCOUNT_RE.fullmatch(account):
                raise PJSIPReconciliationError(
                    "verified carrier account inventory contains an invalid account"
                )
            try:
                did = validate_e164_strict(str(did_raw))
            except (TypeError, ValueError) as exc:
                raise PJSIPReconciliationError(
                    "verified carrier account inventory contains an invalid DID"
                ) from exc
            if account in validated_inventory:
                raise PJSIPReconciliationError(
                    "verified carrier account inventory contains a duplicate account"
                )
            validated_inventory[account] = did
        verified_account_dids = validated_inventory

    trunks: dict[str, dict[str, Any]] = {}
    routes_by_account: dict[str, InboundRoute] = {}
    routes_by_did: dict[str, InboundRoute] = {}
    for row in source_rows:
        trunk_id = _canonical_trunk_id(row.get("id"))
        tenant_id = _canonical_uuid(row.get("tenant_id"), label="SIP trunk tenant")
        prepared = dict(row)
        prepared["id"] = trunk_id
        prepared["tenant_id"] = tenant_id
        metadata = _metadata(row)
        # Deprecated metadata was the 2026-08-30 workaround. It is not proof
        # of an active DID assignment and must never reach endpoint set_var.
        metadata.pop("inbound_did", None)
        prepared["metadata"] = metadata

        previous = trunks.get(trunk_id)
        if previous is None:
            trunks[trunk_id] = prepared
        else:
            stable_fields = (
                "tenant_id",
                "trunk_name",
                "sip_domain",
                "port",
                "transport",
                "direction",
                "auth_username",
                "auth_password_encrypted",
                "metadata",
            )
            if any(previous.get(field) != prepared.get(field) for field in stable_fields):
                raise PJSIPReconciliationError(
                    f"trunk-{trunk_id} has conflicting source rows"
                )

        route = _route_from_row(
            row,
            trunk_id=trunk_id,
            tenant_id=tenant_id,
            observed_at=observed_at,
        )
        if route is None:
            continue
        verified_did = verified_account_dids.get(route.account)
        if verified_did is None:
            # An active assignment alone cannot prove what a carrier places in
            # the Request-URI. Unknown accounts stay on the catch-all and are
            # rejected by admission rather than being guessed into a route.
            continue
        if route.did != verified_did:
            raise UnsafeInboundMappingError(
                f"trunk-{trunk_id} assignment conflicts with verified carrier routing"
            )
        prior_account = routes_by_account.get(route.account)
        if prior_account is not None and prior_account != route:
            raise UnsafeInboundMappingError(
                "active inbound assignments contain a duplicate account conflict"
            )
        prior_did = routes_by_did.get(route.did)
        if prior_did is not None and prior_did != route:
            raise UnsafeInboundMappingError(
                "active inbound assignments contain a duplicate DID conflict"
            )
        routes_by_account[route.account] = route
        routes_by_did[route.did] = route

    verified_trunks_by_account: dict[str, str] = {}
    for trunk_id, trunk in trunks.items():
        account = str(trunk.get("auth_username") or "").strip()
        if account not in verified_account_dids:
            continue
        if str(trunk.get("direction") or "").strip().lower() not in {
            "inbound",
            "both",
        }:
            continue
        prior_trunk = verified_trunks_by_account.get(account)
        if prior_trunk is not None and prior_trunk != trunk_id:
            raise UnsafeInboundMappingError(
                "active inbound trunks contain a duplicate verified carrier account"
            )
        verified_trunks_by_account[account] = trunk_id
        if account not in routes_by_account:
            raise UnsafeInboundMappingError(
                f"trunk-{trunk_id} verified carrier route lacks a matching active assignment"
            )

    files: dict[str, bytes] = {}
    for trunk_id in sorted(trunks):
        row = trunks[trunk_id]
        encrypted = row.get("auth_password_encrypted")
        plaintext: str | None = None
        try:
            if encrypted:
                plaintext = decrypt_password(str(encrypted))
            projection = build_trunk_config_input(row, decrypted_password=plaintext)
            rendered = render_trunk_conf(projection)
            if "set_var=TALKY_INBOUND_DID" in rendered:
                raise UnsafeInboundMappingError(
                    f"trunk-{trunk_id} rendered an endpoint-owned DID map"
                )
            files[f"{PJSIP_PREFIX}trunk-{trunk_id}.conf"] = rendered.encode("utf-8")
        finally:
            plaintext = None

    try:
        template = Path(dialplan_template).read_text(encoding="utf-8")
    except OSError as exc:
        raise PJSIPReconciliationError("managed inbound dialplan template is missing") from exc
    routes = tuple(sorted(routes_by_account.values()))
    files[DIALPLAN_NAME] = render_inbound_dialplan(routes, template=template)
    if base_pjsip_source is not None:
        try:
            base_pjsip = Path(base_pjsip_source).read_bytes()
        except OSError as exc:
            raise PJSIPReconciliationError(
                "base PJSIP configuration is unreadable"
            ) from exc
        files[BASE_PJSIP_NAME] = render_shared_endpoint_base_pjsip(base_pjsip)
    ordered_files = {name: files[name] for name in sorted(files)}
    digest = compute_candidate_digest(ordered_files)
    directory = Path(candidate_dir)
    _materialize_candidate(directory, ordered_files)
    return CandidateSet(
        directory=directory,
        files=ordered_files,
        routes=routes,
        digest=digest,
    )


def _dialplan_path_for(live_dir: Path, dialplan_live_path: Path | None) -> Path:
    if dialplan_live_path is not None:
        return Path(dialplan_live_path)
    # Direct unit callers remain hermetic. The CLI passes the explicit /etc path.
    return Path(live_dir).parent / "extensions.d" / "talky-inbound.conf"


def _managed_live_files(
    live_dir: Path,
    *,
    dialplan_live_path: Path,
    base_pjsip_live_path: Path | None = None,
) -> dict[str, Path]:
    managed: dict[str, Path] = {}
    if live_dir.exists():
        if live_dir.is_symlink():
            raise PJSIPReconciliationError("live PJSIP directory must not be a symlink")
        for path in sorted(live_dir.glob(_MANAGED_GLOB), key=lambda item: item.name):
            if not _TRUNK_FILENAME_RE.fullmatch(path.name):
                raise PJSIPReconciliationError(
                    "live PJSIP directory contains an invalid managed filename"
                )
            if path.is_symlink():
                raise PJSIPReconciliationError(
                    "live PJSIP directory contains a managed symlink"
                )
            if path.is_file():
                managed[f"{PJSIP_PREFIX}{path.name}"] = path
    if dialplan_live_path.exists():
        if dialplan_live_path.is_symlink() or not dialplan_live_path.is_file():
            raise PJSIPReconciliationError("live inbound dialplan is not a regular file")
        managed[DIALPLAN_NAME] = dialplan_live_path
    if base_pjsip_live_path is not None and base_pjsip_live_path.exists():
        if base_pjsip_live_path.is_symlink() or not base_pjsip_live_path.is_file():
            raise PJSIPReconciliationError(
                "live base PJSIP configuration is not a regular file"
            )
        managed[BASE_PJSIP_NAME] = base_pjsip_live_path
    return managed


def compare_live(
    candidate: CandidateSet,
    *,
    live_dir: Path,
    dialplan_live_path: Path | None = None,
    base_pjsip_live_path: Path | None = None,
) -> ReconciliationSummary:
    dialplan_path = _dialplan_path_for(Path(live_dir), dialplan_live_path)
    live = _managed_live_files(
        Path(live_dir),
        dialplan_live_path=dialplan_path,
        base_pjsip_live_path=base_pjsip_live_path,
    )
    desired_names = set(candidate.files)
    live_names = set(live)
    missing = tuple(sorted(desired_names - live_names))
    unexpected = tuple(sorted(live_names - desired_names))
    changed = tuple(
        name
        for name in sorted(desired_names & live_names)
        if live[name].read_bytes() != candidate.files[name]
    )
    return ReconciliationSummary(
        digest=candidate.digest,
        missing=missing,
        changed=changed,
        unexpected=unexpected,
    )


@contextmanager
def exclusive_apply_lock(path: Path) -> Iterator[None]:
    """Take a non-blocking cross-process lock for check and apply writers."""
    lock_path = Path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    os.chmod(lock_path, 0o600)
    if os.fstat(descriptor).st_size == 0:
        os.write(descriptor, b"\0")
    os.lseek(descriptor, 0, os.SEEK_SET)
    try:
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ReconciliationLocked(
                    "another Asterisk reconciliation is active"
                ) from exc
        else:
            import fcntl

            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ReconciliationLocked(
                    "another Asterisk reconciliation is active"
                ) from exc
        try:
            yield
        finally:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _run_asterisk(command: str) -> CommandResult:
    try:
        process = subprocess.run(
            ["asterisk", "-rx", command],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return CommandResult(127, "", "")
    return CommandResult(process.returncode, process.stdout or "", process.stderr or "")


def _prove_zero_active_channels(run_asterisk: Callable[[str], CommandResult]) -> None:
    result = run_asterisk("core show channels count")
    if result.returncode != 0:
        raise ActiveChannelsError("could not prove zero Asterisk active channels")
    match = _ACTIVE_CHANNELS_RE.search(result.stdout)
    if match is None:
        raise ActiveChannelsError("could not prove zero Asterisk active channels")
    active = int(match.group(1))
    if active != 0:
        raise ActiveChannelsError(f"refusing Asterisk apply with {active} active channels")


def _command_succeeded(result: CommandResult) -> bool:
    output = f"{result.stdout}\n{result.stderr}"
    return result.returncode == 0 and not _RELOAD_FAILURE_RE.search(output)


def _reload_only(run_asterisk: Callable[[str], CommandResult]) -> bool:
    return all(
        _command_succeeded(run_asterisk(command))
        for command in ("pjsip reload", "dialplan reload")
    )


def _reload_and_prove_runtime(
    candidate: CandidateSet,
    *,
    run_asterisk: Callable[[str], CommandResult],
) -> None:
    if not _reload_only(run_asterisk):
        raise PJSIPReloadFailed("Asterisk rejected the generated configuration reload")

    shared = run_asterisk("pjsip show endpoint blazedigitel-endpoint")
    shared_output = f"{shared.stdout}\n{shared.stderr}"
    if (
        not _command_succeeded(shared)
        or "blazedigitel-endpoint" not in shared_output
        or "from-talky-inbound" not in shared_output
    ):
        raise PJSIPReloadFailed(
            "the shared carrier endpoint is not bound to the managed inbound context"
        )

    for name in sorted(candidate.files):
        if not name.startswith(PJSIP_PREFIX):
            continue
        endpoint_name = name.removeprefix(PJSIP_PREFIX).removesuffix(".conf")
        result = run_asterisk(f"pjsip show endpoint {endpoint_name}")
        output = f"{result.stdout}\n{result.stderr}"
        if (
            not _command_succeeded(result)
            or endpoint_name not in output
            or "from-talky-inbound" not in output
        ):
            raise PJSIPReloadFailed(
                "a generated tenant endpoint is absent from the Asterisk runtime"
            )

    dialplan = run_asterisk("dialplan show from-talky-inbound")
    output = f"{dialplan.stdout}\n{dialplan.stderr}"
    if (
        not _command_succeeded(dialplan)
        or "from-talky-inbound" not in output
        or "Stasis" not in output
        or "_." not in output
    ):
        raise PJSIPReloadFailed("the managed inbound dialplan is absent at runtime")
    for route in candidate.routes:
        if route.account not in output or route.did not in output:
            raise PJSIPReloadFailed(
                "a generated account route is absent from the Asterisk runtime"
            )


def _target_for(
    name: str,
    *,
    live_dir: Path,
    dialplan_live_path: Path,
    base_pjsip_live_path: Path | None = None,
) -> Path:
    _validate_virtual_name(name)
    if name == DIALPLAN_NAME:
        return dialplan_live_path
    if name == BASE_PJSIP_NAME:
        if base_pjsip_live_path is None:
            raise PJSIPApplyFailed("base PJSIP target path is required")
        return base_pjsip_live_path
    return live_dir / name.removeprefix(PJSIP_PREFIX)


def _snapshot_live(
    live_dir: Path,
    *,
    dialplan_live_path: Path,
    base_pjsip_live_path: Path | None = None,
) -> dict[str, _FileSnapshot]:
    snapshots: dict[str, _FileSnapshot] = {}
    for name, path in _managed_live_files(
        live_dir,
        dialplan_live_path=dialplan_live_path,
        base_pjsip_live_path=base_pjsip_live_path,
    ).items():
        details = path.stat()
        snapshots[name] = _FileSnapshot(
            content=path.read_bytes(),
            mode=stat.S_IMODE(details.st_mode),
            uid=getattr(details, "st_uid", None),
            gid=getattr(details, "st_gid", None),
        )
    return snapshots


def _prove_base_pjsip_source_matches_candidate(
    candidate: CandidateSet,
    snapshot: Mapping[str, _FileSnapshot],
) -> None:
    """Refuse to overwrite an operator edit made after candidate generation."""
    expected = candidate.files.get(BASE_PJSIP_NAME)
    if expected is None:
        return
    saved = snapshot.get(BASE_PJSIP_NAME)
    if saved is None:
        raise CandidateDigestMismatch(
            "live base PJSIP configuration changed after candidate generation"
        )
    try:
        current_candidate = render_shared_endpoint_base_pjsip(saved.content)
    except PJSIPReconciliationError as exc:
        raise CandidateDigestMismatch(
            "live base PJSIP configuration changed after candidate generation"
        ) from exc
    if not hmac.compare_digest(current_candidate, expected):
        raise CandidateDigestMismatch(
            "live base PJSIP configuration changed after candidate generation"
        )


def _restore_snapshot(
    live_dir: Path,
    *,
    dialplan_live_path: Path,
    base_pjsip_live_path: Path | None,
    snapshot: Mapping[str, _FileSnapshot],
) -> None:
    current = _managed_live_files(
        live_dir,
        dialplan_live_path=dialplan_live_path,
        base_pjsip_live_path=base_pjsip_live_path,
    )
    for name in sorted(set(current) - set(snapshot)):
        current[name].unlink()
    for name in sorted(snapshot):
        saved = snapshot[name]
        _atomic_write(
            _target_for(
                name,
                live_dir=live_dir,
                dialplan_live_path=dialplan_live_path,
                base_pjsip_live_path=base_pjsip_live_path,
            ),
            saved.content,
            mode=saved.mode or 0o640,
            uid=saved.uid,
            gid=saved.gid,
        )
    if live_dir.exists():
        _fsync_directory(live_dir)
    if dialplan_live_path.parent.exists():
        _fsync_directory(dialplan_live_path.parent)


def _production_ownership() -> LiveOwnership:
    if os.name != "posix" or getattr(os, "geteuid", lambda: -1)() != 0:
        raise PJSIPApplyFailed("production Asterisk apply must run as root on POSIX")
    try:
        import grp
        import pwd

        asterisk_user = pwd.getpwnam("asterisk")
        asterisk_group = grp.getgrnam("asterisk")
        root_user = pwd.getpwnam("root")
    except (ImportError, KeyError) as exc:
        raise PJSIPApplyFailed("required Asterisk ownership identities are missing") from exc
    return LiveOwnership(
        pjsip_uid=asterisk_user.pw_uid,
        pjsip_gid=asterisk_group.gr_gid,
        dialplan_uid=root_user.pw_uid,
        dialplan_gid=asterisk_group.gr_gid,
    )


def _install_candidate(
    candidate: CandidateSet,
    *,
    live_dir: Path,
    dialplan_live_path: Path,
    base_pjsip_live_path: Path | None,
    snapshot: Mapping[str, _FileSnapshot],
    ownership: LiveOwnership | None,
) -> None:
    live_dir.mkdir(parents=True, exist_ok=True)
    dialplan_live_path.parent.mkdir(parents=True, exist_ok=True)
    if live_dir.is_symlink() or dialplan_live_path.parent.is_symlink():
        raise PJSIPApplyFailed("managed Asterisk directory must not be a symlink")
    if ownership is not None:
        os.chown(live_dir, ownership.pjsip_uid, ownership.pjsip_gid)
        os.chmod(live_dir, 0o2770)
        os.chown(
            dialplan_live_path.parent,
            ownership.dialplan_uid,
            ownership.dialplan_gid,
        )
        os.chmod(dialplan_live_path.parent, 0o755)

    for name in sorted(candidate.files):
        if name == DIALPLAN_NAME:
            mode = 0o644
            uid = ownership.dialplan_uid if ownership else None
            gid = ownership.dialplan_gid if ownership else None
        elif name == BASE_PJSIP_NAME:
            mode = 0o640
            uid = ownership.dialplan_uid if ownership else None
            gid = ownership.pjsip_gid if ownership else None
        else:
            mode = 0o640
            uid = ownership.pjsip_uid if ownership else None
            gid = ownership.pjsip_gid if ownership else None
        _atomic_write(
            _target_for(
                name,
                live_dir=live_dir,
                dialplan_live_path=dialplan_live_path,
                base_pjsip_live_path=base_pjsip_live_path,
            ),
            candidate.files[name],
            mode=mode,
            uid=uid,
            gid=gid,
        )
    for name in sorted(set(snapshot) - set(candidate.files)):
        if name.startswith(PJSIP_PREFIX):
            _target_for(
                name,
                live_dir=live_dir,
                dialplan_live_path=dialplan_live_path,
            ).unlink()
    _fsync_directory(live_dir)
    _fsync_directory(dialplan_live_path.parent)


def _apply_candidate_set_locked(
    candidate: CandidateSet,
    *,
    live_dir: Path,
    dialplan_live_path: Path,
    base_pjsip_live_path: Path | None,
    expected_digest: str,
    run_asterisk: Callable[[str], CommandResult],
    ownership: LiveOwnership | None,
) -> ReconciliationSummary:
    current_digest = compute_candidate_digest(candidate.files)
    if not expected_digest or not hmac.compare_digest(expected_digest, current_digest):
        raise CandidateDigestMismatch("expected digest does not match current candidate")
    if not hmac.compare_digest(candidate.digest, current_digest):
        raise CandidateDigestMismatch("candidate changed after its digest was created")

    _prove_zero_active_channels(run_asterisk)
    summary = compare_live(
        candidate,
        live_dir=live_dir,
        dialplan_live_path=dialplan_live_path,
        base_pjsip_live_path=base_pjsip_live_path,
    )
    snapshot = _snapshot_live(
        live_dir,
        dialplan_live_path=dialplan_live_path,
        base_pjsip_live_path=base_pjsip_live_path,
    )
    _prove_base_pjsip_source_matches_candidate(candidate, snapshot)
    try:
        # Install even when bytes already match. That repairs owner/mode drift
        # and makes a crash-recovery rerun perform the same atomic publication
        # before it reloads and proves runtime state.
        _install_candidate(
            candidate,
            live_dir=live_dir,
            dialplan_live_path=dialplan_live_path,
            base_pjsip_live_path=base_pjsip_live_path,
            snapshot=snapshot,
            ownership=ownership,
        )
    except Exception as exc:
        try:
            _restore_snapshot(
                live_dir,
                dialplan_live_path=dialplan_live_path,
                base_pjsip_live_path=base_pjsip_live_path,
                snapshot=snapshot,
            )
        except Exception as rollback_exc:
            raise PJSIPApplyFailed(
                "Asterisk file apply failed and rollback could not be completed"
            ) from rollback_exc
        raise PJSIPApplyFailed(
            "Asterisk file apply failed and prior files were restored"
        ) from exc

    # Always reload and prove runtime, including a no-drift rerun after a crash
    # between the prior file replacement and reload.
    try:
        _reload_and_prove_runtime(candidate, run_asterisk=run_asterisk)
    except PJSIPReloadFailed as exc:
        try:
            _restore_snapshot(
                live_dir,
                dialplan_live_path=dialplan_live_path,
                base_pjsip_live_path=base_pjsip_live_path,
                snapshot=snapshot,
            )
        except Exception as rollback_exc:
            raise PJSIPReloadFailed(
                "Asterisk runtime verification failed and file rollback failed"
            ) from rollback_exc
        if _reload_only(run_asterisk):
            raise PJSIPReloadFailed(
                "Asterisk runtime verification failed; prior files were restored"
            ) from exc
        raise PJSIPReloadFailed(
            "Asterisk runtime verification failed; files were restored but rollback reload failed"
        ) from exc
    return summary


def apply_candidate_set(
    candidate: CandidateSet,
    *,
    live_dir: Path,
    expected_digest: str,
    lock_path: Path,
    dialplan_live_path: Path | None = None,
    base_pjsip_live_path: Path | None = None,
    run_asterisk: Callable[[str], CommandResult] = _run_asterisk,
    ownership: LiveOwnership | None = None,
) -> ReconciliationSummary:
    """Apply exactly one approved candidate or leave the prior set intact."""
    live_path = Path(live_dir)
    dialplan_path = _dialplan_path_for(live_path, dialplan_live_path)
    with exclusive_apply_lock(Path(lock_path)):
        return _apply_candidate_set_locked(
            candidate,
            live_dir=live_path,
            dialplan_live_path=dialplan_path,
            base_pjsip_live_path=base_pjsip_live_path,
            expected_digest=expected_digest,
            run_asterisk=run_asterisk,
            ownership=ownership,
        )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-dir", type=Path, default=DEFAULT_CANDIDATE_DIR)
    parser.add_argument("--live-dir", type=Path, default=DEFAULT_PJSIP_LIVE_DIR)
    parser.add_argument(
        "--base-pjsip-live-path",
        type=Path,
        default=DEFAULT_BASE_PJSIP_LIVE_PATH,
    )
    parser.add_argument(
        "--dialplan-live-path", type=Path, default=DEFAULT_DIALPLAN_LIVE_PATH
    )
    parser.add_argument(
        "--dialplan-template", type=Path, default=DEFAULT_DIALPLAN_TEMPLATE
    )
    parser.add_argument(
        "--verified-account-map", type=Path, default=DEFAULT_VERIFIED_ACCOUNT_MAP
    )
    parser.add_argument("--lock-path", type=Path, default=DEFAULT_LOCK_PATH)
    parser.add_argument("--apply", action="store_true", help="apply approved candidate")
    parser.add_argument("--expected-digest", help="digest printed by a prior check")
    args = parser.parse_args(argv)
    if args.apply and not args.expected_digest:
        parser.error("--apply requires --expected-digest")
    if not args.apply and args.expected_digest:
        parser.error("--expected-digest is only valid with --apply")
    return args


async def _execute(args: argparse.Namespace) -> int:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise PJSIPReconciliationError("DATABASE_URL is required")

    # The same lock covers DB fetch, candidate materialization, comparison and
    # apply. A second writer cannot corrupt the candidate while the first
    # process is still deriving its digest.
    with exclusive_apply_lock(Path(args.lock_path)):
        pool = await asyncpg.create_pool(database_url, min_size=1, max_size=1)
        try:
            rows = await fetch_reconciliation_rows(
                pool,
                platform_trunk_name=platform_default_trunk_name(),
            )
        finally:
            await pool.close()

        encryption = get_encryption_service()
        candidate = build_candidate_set(
            rows,
            candidate_dir=args.candidate_dir,
            decrypt_password=encryption.decrypt,
            dialplan_template=args.dialplan_template,
            verified_account_dids=load_verified_account_dids(
                args.verified_account_map
            ),
            base_pjsip_source=args.base_pjsip_live_path,
        )
        summary = compare_live(
            candidate,
            live_dir=args.live_dir,
            dialplan_live_path=args.dialplan_live_path,
            base_pjsip_live_path=args.base_pjsip_live_path,
        )
        if args.apply:
            summary = _apply_candidate_set_locked(
                candidate,
                live_dir=args.live_dir,
                dialplan_live_path=args.dialplan_live_path,
                base_pjsip_live_path=args.base_pjsip_live_path,
                expected_digest=args.expected_digest,
                run_asterisk=_run_asterisk,
                ownership=_production_ownership(),
            )
    output = {
        "mode": "apply" if args.apply else "check",
        "route_count": len(candidate.routes),
        **summary.to_safe_dict(),
    }
    print(json.dumps(output, sort_keys=True))
    return 0 if args.apply or not summary.drifted else 1


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        return asyncio.run(_execute(args))
    except PJSIPReconciliationError as exc:
        print(
            json.dumps(
                {
                    "mode": "apply" if args.apply else "check",
                    "status": "blocked",
                    "error": str(exc),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
