"""Per-tenant namespaced PJSIP config generation (Phase B).

Renders ONE Asterisk pjsip config file per tenant-owned SIP trunk into
``/etc/asterisk/pjsip.d/trunk-<trunkid>.conf`` (via ``#include pjsip.d/*.conf``
from the base ``pjsip.conf``). Every object is namespaced by the trunk id
— endpoint ``trunk-<id>``, ``trunk-<id>-auth``, ``-aor``, ``-reg`` (only when
registration is enabled) and ``-identify`` — so two tenants can never
collide, and the generator owns ``pjsip.d/`` EXCLUSIVELY: a reload driven by
this generator can never touch the hand-edited base ``pjsip.conf`` or the
shared upstream endpoint.

Split by design:
  * :func:`render_trunk_conf` is PURE (projection in → exact .conf string
    out). No I/O, no logging of secrets — unit-testable offline. The
    decrypted password is passed IN; this function never decrypts.
  * :func:`apply_trunk_config` / :func:`remove_trunk_config` do the I/O:
    decrypt in memory, render, write the file atomically (temp + os.replace,
    chmod 0640), then request a debounced ``pjsip reload``.

The reload is a HOOK, not an action: by default this module NEVER executes
``asterisk`` (it logs the command for an operator to run). Set
``TELEPHONY_PJSIP_AUTO_RELOAD=on`` to let it shell out — kept off so live
Asterisk is only ever touched deliberately.

SECURITY: the Fernet-encrypted trunk password is decrypted only in memory at
render time and written into the (chmod-0640) file. It is NEVER logged — apply
logs only the path + byte length. Newlines are rejected in every rendered
value to prevent config-object injection via a crafted credential.

File permissions: the generated file is written 0640 (owner rw, GROUP read).
Asterisk runs as user ``asterisk`` while the backend runs as ``admins``, so
the file must be GROUP-readable by the asterisk process — a 0600 file the
backend writes is unreadable by asterisk, the ``#include`` silently skips it,
and the trunk never loads (a reload still reports "success"). Proven live.

One-time base-config / deployment prerequisites (ops):
  (a) ``pjsip.conf`` contains ``#include pjsip.d/*.conf`` and defines the
      ``[transport-udp]`` / ``[transport-tcp]`` / ``[transport-tls]`` objects
      the generated endpoints reference.
  (b) ``/etc/asterisk/pjsip.d`` is owned ``asterisk:asterisk`` mode ``2770``
      (setgid) so files created inside inherit group ``asterisk``.
  (c) the backend service user (``admins``) is a member of the ``asterisk``
      group — combined with (b) + the 0640 mode, asterisk can read the files
      the backend creates.
  (d) if ``TELEPHONY_PJSIP_AUTO_RELOAD=on``, a sudoers rule lets the backend
      user run ``asterisk -rx 'pjsip reload'`` (otherwise reload stays a
      logged hook an operator runs by hand).
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_DEFAULT_PJSIP_D_DIR = "/etc/asterisk/pjsip.d"
_DEFAULT_REGISTER_INTERVAL = 3600
_ALLOWED_TRANSPORTS = {"udp", "tcp", "tls"}


# ---------------------------------------------------------------------------
# Projection + pure render
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TrunkConfigInput:
    """Everything :func:`render_trunk_conf` needs — no DB, no secrets beyond
    the already-decrypted password passed in explicitly."""
    trunk_id: str
    tenant_id: str
    trunk_name: str
    sip_domain: str
    port: int
    transport: str
    auth_username: Optional[str]
    auth_password: Optional[str]          # DECRYPTED, in-memory only
    caller_id: Optional[str] = None
    register: bool = False
    register_interval: int = _DEFAULT_REGISTER_INTERVAL
    dtmf_mode: Optional[str] = None
    source_host: Optional[str] = None     # identify match; defaults to sip_domain
    auth_realm: Optional[str] = None      # digest realm; defaults to "asterisk" to mirror the working primary


def _reject_newlines(field: str, value: str) -> str:
    """Guard against config-object injection through a crafted value."""
    if "\n" in value or "\r" in value:
        raise ValueError(f"{field} must not contain newlines")
    return value


def _norm_transport(transport: Optional[str]) -> str:
    t = (transport or "udp").strip().lower()
    if t not in _ALLOWED_TRANSPORTS:
        raise ValueError(f"unsupported transport {transport!r}")
    return t


def render_trunk_conf(inp: TrunkConfigInput) -> str:
    """Render the exact pjsip .conf text for one own-trunk row (pure).

    Raises ``ValueError`` for values that would corrupt the config
    (newlines, bad transport). The caller treats a render error as
    fail-soft — the trunk row is still saved; the config just isn't applied.
    """
    tid = _reject_newlines("trunk_id", str(inp.trunk_id))
    tenant = _reject_newlines("tenant_id", str(inp.tenant_id))
    domain = _reject_newlines("sip_domain", inp.sip_domain.strip())
    transport = _norm_transport(inp.transport)
    port = int(inp.port)
    if port < 1 or port > 65535:
        raise ValueError(f"port out of range: {port}")

    ep = f"trunk-{tid}"
    transport_obj = f"transport-{transport}"
    has_auth = bool(inp.auth_username and inp.auth_password)
    # The IDENTITY we register + present. Mirror the working blazedigitel primary,
    # which registers its DID/number (not the raw SIP login). Prefer the tenant's
    # configured caller-id/number; fall back to the auth username.
    reg_identity = (
        _reject_newlines("caller_id", inp.caller_id.strip()) if inp.caller_id
        else (_reject_newlines("auth_username", inp.auth_username.strip()) if inp.auth_username else "")
    )
    source_host = _reject_newlines(
        "source_host", (inp.source_host or domain).strip()
    )

    lines: list[str] = []
    safe_name = _reject_newlines("trunk_name", (inp.trunk_name or "").strip())
    lines.append("; Managed by Talky.ai trunk generator - DO NOT EDIT BY HAND.")
    lines.append(f"; tenant={tenant} trunk={tid} name={safe_name}")
    lines.append("; Regenerated automatically on trunk activate/update; removed on deactivate/delete.")
    lines.append("")

    # --- endpoint ---
    lines.append(f"[{ep}]")
    lines.append("type=endpoint")
    lines.append(f"transport={transport_obj}")
    lines.append(f"context=from-tenant-{tenant}")
    lines.append("disallow=all")
    lines.append("allow=ulaw,alaw")
    if has_auth:
        lines.append(f"outbound_auth={ep}-auth")
    lines.append(f"aors={ep}-aor")
    if reg_identity:
        lines.append(f"from_user={reg_identity}")
    lines.append(f"from_domain={domain}")
    if inp.caller_id:
        cid = _reject_newlines("caller_id", inp.caller_id.strip())
        # Present the tenant's own caller-ID on outbound legs.
        lines.append(f'callerid=<{cid}>')
    if inp.dtmf_mode:
        # PJSIP (res_pjsip) does NOT accept the legacy chan_sip names. Map the
        # UI's values to valid PJSIP ones or Asterisk refuses to create the
        # endpoint ("Error parsing dtmf_mode=rfc2833"). Valid: rfc4733, inband,
        # info, auto, auto_info.
        _dm_raw = inp.dtmf_mode.strip().lower()
        _dm = {
            "rfc2833": "rfc4733",
            "sip-info": "info",
            "sipinfo": "info",
        }.get(_dm_raw, _dm_raw)
        if _dm in {"rfc4733", "inband", "info", "auto", "auto_info"}:
            lines.append(f"dtmf_mode={_reject_newlines('dtmf_mode', _dm)}")
    lines.append("direct_media=no")
    # NAT / symmetric-RTP — mirror the working primary so audio flows through the
    # carrier's NAT and the Contact is rewritten to the public address.
    lines.append("rtp_symmetric=yes")
    lines.append("force_rport=yes")
    lines.append("rewrite_contact=yes")
    lines.append("")

    # --- auth (only when credentials present) ---
    if has_auth:
        lines.append(f"[{ep}-auth]")
        lines.append("type=auth")
        lines.append("auth_type=userpass")
        lines.append(f"username={_reject_newlines('auth_username', inp.auth_username.strip())}")
        lines.append(f"password={_reject_newlines('auth_password', inp.auth_password)}")
        # Digest realm — the working primary sets realm=asterisk; a missing/wrong
        # realm is a classic 403-on-REGISTER cause. Default to "asterisk", override
        # via the trunk's Advanced auth_realm.
        realm = _reject_newlines("auth_realm", (inp.auth_realm or "asterisk").strip())
        if realm:
            lines.append(f"realm={realm}")
        lines.append("")

    # --- aor ---
    lines.append(f"[{ep}-aor]")
    lines.append("type=aor")
    lines.append(f"contact=sip:{domain}:{port}")
    lines.append("qualify_frequency=60")
    lines.append("")

    # --- registration (only when register enabled + auth present) ---
    if inp.register and has_auth:
        interval = int(inp.register_interval or _DEFAULT_REGISTER_INTERVAL)
        if interval < 60 or interval > 86400:
            interval = _DEFAULT_REGISTER_INTERVAL
        lines.append(f"[{ep}-reg]")
        lines.append("type=registration")
        lines.append(f"transport={transport_obj}")
        lines.append(f"outbound_auth={ep}-auth")
        lines.append(f"server_uri=sip:{domain}:{port}")
        # Register the NUMBER as the identity (client_uri + contact_user), auth
        # with the SIP login — exactly how the working primary registers.
        lines.append(f"client_uri=sip:{reg_identity}@{domain}")
        lines.append(f"contact_user={reg_identity}")
        lines.append(f"retry_interval={interval}")
        lines.append(f"expiration={interval}")
        # Registration resilience (telephony-audit #4, 2026-07-02). PJSIP
        # defaults (max_retries=10, auth_rejection_permanent=yes) let a
        # >10-retry outage OR a single 403 permanently halt re-registration
        # until a manual reload. Mirror the Blaze-trunk fix so every BYO
        # registration self-heals instead of dying silently.
        lines.append("max_retries=10000")
        lines.append("auth_rejection_permanent=no")
        lines.append("fatal_retry_interval=30")
        lines.append("forbidden_retry_interval=60")
        lines.append("")

    # --- identify (source-host match → tags inbound legs to this endpoint) ---
    lines.append(f"[{ep}-identify]")
    lines.append("type=identify")
    lines.append(f"endpoint={ep}")
    lines.append(f"match={source_host}")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Row → projection mapping (extracts metadata fields; decrypt done by caller)
# ---------------------------------------------------------------------------

def _coerce_metadata(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        import json as _json
        try:
            parsed = _json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def build_trunk_config_input(row: Any, *, decrypted_password: Optional[str]) -> TrunkConfigInput:
    """Map a ``tenant_sip_trunks`` row (+ decrypted password) to the render
    projection. Pulls caller_id / register / register_interval / dtmf_mode /
    source_host out of the trunk ``metadata`` JSON (see schemas.normalize_
    trunk_metadata for their shape)."""
    md = _coerce_metadata(row["metadata"])
    return TrunkConfigInput(
        trunk_id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        trunk_name=row["trunk_name"],
        sip_domain=row["sip_domain"],
        port=int(row["port"]),
        transport=row["transport"],
        auth_username=row["auth_username"],
        auth_password=decrypted_password,
        caller_id=(md.get("caller_id") or None),
        register=bool(md.get("register", False)),
        register_interval=int(md.get("register_interval", _DEFAULT_REGISTER_INTERVAL) or _DEFAULT_REGISTER_INTERVAL),
        dtmf_mode=(md.get("dtmf_mode") or None),
        source_host=(md.get("source_host") or None),
        auth_realm=(md.get("auth_realm") or None),
    )


# ---------------------------------------------------------------------------
# File apply (atomic) + reload hook
# ---------------------------------------------------------------------------

def pjsip_d_dir() -> Path:
    return Path(os.getenv("TELEPHONY_PJSIP_CONF_DIR", _DEFAULT_PJSIP_D_DIR))


def trunk_conf_path(trunk_id: str, *, base_dir: Optional[Path] = None) -> Path:
    base = base_dir or pjsip_d_dir()
    return base / f"trunk-{trunk_id}.conf"


def write_trunk_file(
    trunk_id: str,
    content: str,
    *,
    base_dir: Optional[Path] = None,
) -> Path:
    """Atomically write the trunk's config file (temp + os.replace), 0640.

    Full-file render — the caller passes the COMPLETE desired file content;
    this never appends. os.replace is atomic on POSIX + Windows so a reload
    can never observe a partially written file. Mode is 0640 (owner rw, GROUP
    read) so the asterisk process — which shares the file's `asterisk` group
    but is not the owner — can read it (see module docstring).
    """
    base = base_dir or pjsip_d_dir()
    base.mkdir(parents=True, exist_ok=True)
    target = trunk_conf_path(trunk_id, base_dir=base)

    fd, tmp_name = tempfile.mkstemp(dir=str(base), prefix=f".trunk-{trunk_id}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            # 0640 = owner rw + GROUP read. Secrets live here, so no world
            # access — but asterisk (running as user `asterisk`, in the file's
            # `asterisk` group via the setgid pjsip.d dir) MUST be able to read
            # it, else the #include silently skips the file and the trunk never
            # loads. 0600 would make it invisible to asterisk. Proven live.
            os.chmod(tmp_name, 0o640)
        except OSError:
            pass  # Windows dev boxes; enforced on the Linux server
        os.replace(tmp_name, target)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.remove(tmp_name)
            except OSError:
                pass
    # NEVER log content — it contains the decrypted password.
    logger.info(
        "pjsip_trunk_file_written trunk=%s path=%s bytes=%d",
        trunk_id, target, len(content.encode("utf-8")),
    )
    return target


def remove_trunk_file(trunk_id: str, *, base_dir: Optional[Path] = None) -> bool:
    """Delete the trunk's config file if present. Returns True if removed."""
    target = trunk_conf_path(trunk_id, base_dir=base_dir)
    try:
        target.unlink()
        logger.info("pjsip_trunk_file_removed trunk=%s path=%s", trunk_id, target)
        return True
    except FileNotFoundError:
        return False


# --- reload hook (debounced; does NOT execute asterisk unless opted in) ----

_reload_lock = asyncio.Lock()
_reload_pending = False
_RELOAD_DEBOUNCE_S = float(os.getenv("TELEPHONY_PJSIP_RELOAD_DEBOUNCE_S", "2.0"))


def pjsip_reload_command() -> str:
    """The exact command an operator runs on the Asterisk box."""
    return "asterisk -rx 'pjsip reload'"


def _auto_reload_enabled() -> bool:
    return os.getenv("TELEPHONY_PJSIP_AUTO_RELOAD", "").strip().lower() in {
        "1", "true", "on", "yes",
    }


async def request_pjsip_reload(*, execute: Optional[bool] = None) -> bool:
    """Hook invoked after any config file change.

    Default (``execute`` unset and ``TELEPHONY_PJSIP_AUTO_RELOAD`` off): logs
    the command for an operator to run and returns False — this module never
    touches live Asterisk on its own. When enabled, coalesces rapid changes
    within a debounce window and runs ``asterisk -rx 'pjsip reload'`` once.
    """
    if execute is None:
        execute = _auto_reload_enabled()

    if not execute:
        logger.info(
            "pjsip_reload_needed — run on the Asterisk host: %s",
            pjsip_reload_command(),
        )
        return False

    global _reload_pending
    async with _reload_lock:
        if _reload_pending:
            return False
        _reload_pending = True
    try:
        await asyncio.sleep(_RELOAD_DEBOUNCE_S)  # debounce: coalesce a burst
    finally:
        async with _reload_lock:
            _reload_pending = False

    try:
        proc = await asyncio.create_subprocess_exec(
            "asterisk", "-rx", "pjsip reload",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            logger.error(
                "pjsip_reload_failed rc=%s err=%s", proc.returncode,
                (err or b"").decode(errors="replace")[:300],
            )
            return False
        logger.info("pjsip_reload_ok out=%s", (out or b"").decode(errors="replace")[:200])
        return True
    except FileNotFoundError:
        logger.warning(
            "pjsip_reload_skipped: asterisk binary not found — run manually: %s",
            pjsip_reload_command(),
        )
        return False
    except Exception as exc:  # noqa: BLE001
        logger.error("pjsip_reload_error err=%s", exc)
        return False


# ---------------------------------------------------------------------------
# Orchestrators used by the trunk lifecycle hooks
# ---------------------------------------------------------------------------

async def apply_trunk_config(
    row: Any,
    *,
    decrypted_password: Optional[str],
    base_dir: Optional[Path] = None,
    reload: bool = True,
) -> Path:
    """Render + atomically write the trunk file, then request a reload.

    ``decrypted_password`` is the plaintext (decrypted by the caller via the
    encryption service). Raises on a render/write error so the caller can
    log + swallow (fail-soft) without persisting a broken file.
    """
    inp = build_trunk_config_input(row, decrypted_password=decrypted_password)
    content = render_trunk_conf(inp)
    path = write_trunk_file(inp.trunk_id, content, base_dir=base_dir)
    if reload:
        await request_pjsip_reload()
    return path


async def remove_trunk_config(
    trunk_id: str,
    *,
    base_dir: Optional[Path] = None,
    reload: bool = True,
) -> bool:
    """Remove the trunk file (deactivate/delete) then request a reload."""
    removed = remove_trunk_file(trunk_id, base_dir=base_dir)
    if reload:
        await request_pjsip_reload()
    return removed
