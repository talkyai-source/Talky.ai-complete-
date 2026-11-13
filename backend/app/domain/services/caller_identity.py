"""Conservative carrier assertions and durable caller-identity projections.

An endpoint allowlist is an operator trust contract, not a SIP header claim.
It must remain empty until carrier ingress and assertion rewriting are proven.
Raw PAI is used only to bind the assertion to ANI; it is never persisted here.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Mapping

from app.core import config

_MAX_HEADER = 4096
_NUMBER = re.compile(r"\+[1-9][0-9]{1,14}\Z")
_ENDPOINT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_VERSTATS = {value.casefold(): value for value in (
    "TN-Validation-Passed", "TN-Validation-Failed", "No-TN-Validation"
)}


def _header(value):
    if not isinstance(value, str) or len(value) > _MAX_HEADER:
        return None
    if any(ord(char) < 32 or ord(char) > 126 for char in value):
        return None
    return value.strip()


def extract_caller_identity_headers(args) -> dict:
    """Decode named Stasis arguments; legacy DID/context/agent slots stay put."""
    names = {"cid_pai_b64": "pai", "cid_attestation_b64": "attestation"}
    result = {}
    if not isinstance(args, (list, tuple)) or len(args) > 32:
        return result
    for arg in args:
        if not isinstance(arg, str):
            continue
        name, sep, encoded = arg.partition("=")
        if name not in names:
            continue
        if not sep or names[name] in result or len(encoded) > 5464:
            return {}
        try:
            decoded = base64.b64decode(encoded, validate=True).decode("ascii")
        except (ValueError, UnicodeError, binascii.Error):
            return {}
        value = _header(decoded)
        if value is None:
            return {}
        result[names[name]] = value
    return result if result.get("pai") else {}


def _pai(value):
    """Accept one telephone identity, with one unambiguous verstat parameter."""
    text = _header(value)
    if not text or "," in text:
        return None, None
    if "<" in text or ">" in text:
        match = re.fullmatch(r'(?:"[^"\\]*"\s*)?<([^<>]+)>([^<>]*)', text)
        if not match:
            return None, None
        uri, suffix = match.groups()
    else:
        uri, suffix = text, ""
    match = re.fullmatch(r"(sips?|tel):(\+[1-9][0-9]{1,14})(.*)", uri, re.IGNORECASE)
    if not match:
        return None, None
    scheme, number, tail = match.groups()
    if scheme.lower() != "tel":
        if tail.count("@") != 1:
            return None, None
        before_host, host_tail = tail.split("@", 1)
        host, sep, after_host = host_tail.partition(";")
        if not re.fullmatch(r"(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\])(?::[0-9]{1,5})?", host):
            return None, None
        tail = before_host + (";" + after_host if sep else "")
    parameters = tail + suffix
    values = {}
    if parameters:
        if not parameters.startswith(";"):
            return None, None
        for parameter in parameters[1:].split(";"):
            match = re.fullmatch(r"([A-Za-z0-9_-]+)=([A-Za-z0-9._~-]+)", parameter)
            if not match or match[1].lower() in values:
                return None, None
            values[match[1].lower()] = match[2]
    return number, _VERSTATS.get(values.get("verstat", "").casefold())


def _trusted_endpoint(endpoint) -> bool:
    configured = config.get_settings().inbound_caller_id_trusted_endpoints
    if not isinstance(configured, str) or len(configured) > 4096:
        return False
    allowed = set()
    for value in configured.split(","):
        value = value.strip()
        if not value:
            continue
        if not _ENDPOINT.fullmatch(value) or value.lower() == "anonymous" or value.lower().startswith("trunk-"):
            return False
        allowed.add(value)
    return isinstance(endpoint, str) and endpoint in allowed


def caller_identity_from_metadata(metadata, *, caller_ani, ingress_endpoint) -> dict:
    raw = metadata.get("caller_identity_headers") if isinstance(metadata, Mapping) else None
    raw = raw if isinstance(raw, Mapping) else {}
    number, verstat = _pai(raw.get("pai"))
    attestation = _header(raw.get("attestation"))
    attestation = attestation if attestation in {"A", "B", "C"} else None
    trusted = bool(raw) and _trusted_endpoint(ingress_endpoint)
    verified = bool(trusted and isinstance(caller_ani, str) and _NUMBER.fullmatch(caller_ani)
                    and number == caller_ani and verstat == "TN-Validation-Passed" and attestation == "A")
    return {"version": 1, "caller_id_verified": verified, "attestation": attestation,
            "verstat": verstat, "source": "trusted_carrier" if trusted else ("untrusted" if raw else "missing")}


def _object(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError, RecursionError):
            return {}
    return value if isinstance(value, Mapping) else {}


def caller_identity_projection(call) -> dict:
    """Strict saved evidence, never truthiness of model/request-supplied flags."""
    call = call or {}
    saved = _object(_object(call.get("route_snapshot")).get("caller_identity"))
    valid_shape = (type(saved.get("version")) is int and saved.get("version") == 1
                   and saved.get("source") in ("trusted_carrier", "untrusted", "missing")
                   and type(saved.get("caller_id_verified")) is bool)
    attestation = saved.get("attestation") if valid_shape else None
    attestation = attestation if isinstance(attestation, str) and attestation in {"A", "B", "C"} else None
    verified = bool(valid_shape and call.get("direction") == "inbound"
                    and not call.get("caller_ani_private")
                    and saved.get("source") == "trusted_carrier"
                    and saved.get("caller_id_verified") is True and attestation == "A"
                    and saved.get("verstat") == "TN-Validation-Passed")
    return {"caller_id_verified": verified, "caller_id_attestation": attestation}


def automatic_identity_change_allowed(call) -> bool:
    return bool(call and (call.get("direction") == "outbound"
                         or caller_identity_projection(call)["caller_id_verified"]))


async def load_call_identity(conn, tenant_id, call_id):
    """Keep the authority read in the caller's existing mutation transaction."""
    return await conn.fetchrow(
        "SELECT direction, route_snapshot, caller_ani_private FROM calls "
        "WHERE tenant_id=$1::uuid AND id=$2::uuid FOR SHARE",
        str(tenant_id), str(call_id),
    )
