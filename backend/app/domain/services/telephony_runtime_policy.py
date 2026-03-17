"""
WS-G runtime policy compiler.

Compiles tenant telephony policy records from PostgreSQL into deterministic
runtime artifacts for:
- OpenSIPS dispatcher runtime reload flow
- FreeSWITCH mod_xml_curl dynamic XML flow
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional
from xml.sax.saxutils import escape as xml_escape


POLICY_SCHEMA_VERSION = "ws-g.v1"


@dataclass(frozen=True)
class CompileIssue:
    code: str
    message: str
    route_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "route_id": self.route_id,
        }


class PolicyCompilationError(ValueError):
    def __init__(self, issues: List[CompileIssue]):
        super().__init__("Policy compilation failed")
        self.issues = issues

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": "policy_compilation_failed",
            "issues": [issue.to_dict() for issue in self.issues],
        }


@dataclass(frozen=True)
class RuntimeCompileResult:
    schema_version: str
    source_hash: str
    input_snapshot: Dict[str, Any]
    artifact: Dict[str, Any]


def _stable_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalize_trunk(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(row["id"]),
        "trunk_name": str(row["trunk_name"]).strip(),
        "sip_domain": str(row["sip_domain"]).strip().lower(),
        "port": int(row["port"]),
        "transport": str(row["transport"]).strip().lower(),
        "direction": str(row["direction"]).strip().lower(),
        "is_active": bool(row.get("is_active", True)),
        "metadata": row.get("metadata") or {},
    }


def _normalize_codec(row: Mapping[str, Any]) -> Dict[str, Any]:
    allowed = [str(codec).strip().upper() for codec in (row.get("allowed_codecs") or [])]
    return {
        "id": str(row["id"]),
        "policy_name": str(row["policy_name"]).strip(),
        "allowed_codecs": allowed,
        "preferred_codec": str(row["preferred_codec"]).strip().upper(),
        "sample_rate_hz": int(row["sample_rate_hz"]),
        "ptime_ms": int(row["ptime_ms"]),
        "max_bitrate_kbps": row.get("max_bitrate_kbps"),
        "jitter_buffer_ms": int(row["jitter_buffer_ms"]),
        "is_active": bool(row.get("is_active", True)),
        "metadata": row.get("metadata") or {},
    }


def _normalize_route(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(row["id"]),
        "policy_name": str(row["policy_name"]).strip(),
        "route_type": str(row["route_type"]).strip().lower(),
        "priority": int(row["priority"]),
        "match_pattern": str(row["match_pattern"]),
        "target_trunk_id": str(row["target_trunk_id"]),
        "codec_policy_id": str(row["codec_policy_id"]) if row.get("codec_policy_id") else None,
        "strip_digits": int(row["strip_digits"]),
        "prepend_digits": row.get("prepend_digits"),
        "is_active": bool(row.get("is_active", True)),
        "metadata": row.get("metadata") or {},
    }


def _normalize_trust_policy(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "id": str(row["id"]),
        "policy_name": str(row["policy_name"]).strip(),
        "allowed_source_cidrs": [str(cidr).strip() for cidr in (row.get("allowed_source_cidrs") or [])],
        "blocked_source_cidrs": [str(cidr).strip() for cidr in (row.get("blocked_source_cidrs") or [])],
        "kamailio_group": int(row.get("kamailio_group") or 1),
        "priority": int(row.get("priority") or 100),
        "is_active": bool(row.get("is_active", True)),
        "metadata": row.get("metadata") or {},
    }


def _sort_trunks(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(rows, key=lambda r: (r["trunk_name"].lower(), r["id"]))


def _sort_codecs(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(rows, key=lambda r: (r["policy_name"].lower(), r["id"]))


def _sort_routes(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        rows,
        key=lambda r: (
            r["route_type"],
            r["priority"],
            r["policy_name"].lower(),
            r["id"],
        ),
    )


def _sort_trust_policies(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(
        rows,
        key=lambda r: (
            r["priority"],
            r["kamailio_group"],
            r["policy_name"].lower(),
            r["id"],
        ),
    )


def _render_freeswitch_dialplan_xml(tenant_id: str, routes: List[Dict[str, Any]]) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<document type=\"freeswitch/xml\">",
        "  <section name=\"dialplan\">",
        f"    <context name=\"tenant-{xml_escape(tenant_id)}\">",
    ]
    for route in routes:
        route_name = f"{route['policy_name']}-{route['id'][:8]}"
        bridge_target = (
            "sofia/external/${destination_number}@"
            f"{route['target']['sip_domain']}:{route['target']['port']};"
            f"transport={route['target']['transport']}"
        )
        lines.extend(
            [
                f"      <extension name=\"{xml_escape(route_name)}\">",
                (
                    "        <condition field=\"destination_number\" "
                    f"expression=\"{xml_escape(route['match_pattern'])}\">"
                ),
                f"          <action application=\"set\" data=\"talky_tenant_id={xml_escape(tenant_id)}\"/>",
                (
                    "          <action application=\"set\" "
                    f"data=\"talky_route_id={xml_escape(route['id'])}\"/>"
                ),
                (
                    "          <action application=\"bridge\" "
                    f"data=\"{xml_escape(bridge_target)}\"/>"
                ),
                "        </condition>",
                "      </extension>",
            ]
        )
    lines.extend(["    </context>", "  </section>", "</document>"])
    return "\n".join(lines)


def _render_freeswitch_directory_xml(tenant_id: str) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        "<document type=\"freeswitch/xml\">",
        "  <section name=\"directory\">",
        f"    <domain name=\"tenant-{xml_escape(tenant_id)}.talky.local\">",
        "      <groups/>",
        "      <users/>",
        "    </domain>",
        "  </section>",
        "</document>",
    ]
    return "\n".join(lines)


def compile_tenant_runtime_policy(
    *,
    tenant_id: str,
    trunks: List[Mapping[str, Any]],
    codec_policies: List[Mapping[str, Any]],
    route_policies: List[Mapping[str, Any]],
    trust_policies: Optional[List[Mapping[str, Any]]] = None,
) -> RuntimeCompileResult:
    norm_trunks = _sort_trunks([_normalize_trunk(row) for row in trunks if row.get("is_active", True)])
    norm_codecs = _sort_codecs(
        [_normalize_codec(row) for row in codec_policies if row.get("is_active", True)]
    )
    norm_routes = _sort_routes([_normalize_route(row) for row in route_policies if row.get("is_active", True)])
    norm_trust = _sort_trust_policies(
        [
            _normalize_trust_policy(row)
            for row in (trust_policies or [])
            if row.get("is_active", True)
        ]
    )

    issues: List[CompileIssue] = []
    if not norm_trunks:
        issues.append(CompileIssue(code="no_active_trunks", message="No active SIP trunks found."))
    if not norm_routes:
        issues.append(CompileIssue(code="no_active_routes", message="No active route policies found."))

    trunk_map = {row["id"]: row for row in norm_trunks}
    codec_map = {row["id"]: row for row in norm_codecs}

    compiled_routes: List[Dict[str, Any]] = []
    for route in norm_routes:
        try:
            re.compile(route["match_pattern"])
        except re.error as exc:
            issues.append(
                CompileIssue(
                    code="invalid_regex",
                    route_id=route["id"],
                    message=f"Invalid route regex: {exc}",
                )
            )
            continue

        target = trunk_map.get(route["target_trunk_id"])
        if not target:
            issues.append(
                CompileIssue(
                    code="missing_target_trunk",
                    route_id=route["id"],
                    message="Route references trunk that is missing or inactive.",
                )
            )
            continue

        if route["route_type"] == "outbound" and target["direction"] not in {"outbound", "both"}:
            issues.append(
                CompileIssue(
                    code="trunk_direction_mismatch",
                    route_id=route["id"],
                    message="Outbound route references non-outbound trunk.",
                )
            )
            continue

        if route["route_type"] == "inbound" and target["direction"] not in {"inbound", "both"}:
            issues.append(
                CompileIssue(
                    code="trunk_direction_mismatch",
                    route_id=route["id"],
                    message="Inbound route references non-inbound trunk.",
                )
            )
            continue

        codec = None
        if route["codec_policy_id"]:
            codec = codec_map.get(route["codec_policy_id"])
            if not codec:
                issues.append(
                    CompileIssue(
                        code="missing_codec_policy",
                        route_id=route["id"],
                        message="Route references codec policy that is missing or inactive.",
                    )
                )
                continue

        compiled_routes.append(
            {
                **route,
                "target": target,
                "codec": codec,
            }
        )

    if issues:
        raise PolicyCompilationError(issues)

    input_snapshot = {
        "tenant_id": tenant_id,
        "trunks": norm_trunks,
        "codec_policies": norm_codecs,
        "route_policies": norm_routes,
        "trust_policies": norm_trust,
    }
    source_hash = _stable_hash(input_snapshot)

    dispatcher_sets: Dict[str, List[Dict[str, Any]]] = {"1": [], "2": []}
    for route in _sort_routes(compiled_routes):
        set_id = "1" if route["route_type"] == "outbound" else "2"
        destination = (
            f"sip:{route['target']['sip_domain']}:"
            f"{route['target']['port']};transport={route['target']['transport']}"
        )
        dispatcher_sets[set_id].append(
            {
                "destination": destination,
                "priority": route["priority"],
                "route_id": route["id"],
                "route_name": route["policy_name"],
                "codec_policy_id": route["codec_policy_id"],
                "strip_digits": route["strip_digits"],
                "prepend_digits": route["prepend_digits"],
            }
        )

    for set_id in dispatcher_sets:
        dispatcher_sets[set_id] = sorted(
            dispatcher_sets[set_id],
            key=lambda row: (row["priority"], row["route_name"].lower(), row["route_id"]),
        )

    sip_edge_artifact = {
        "dispatcher": {
            "reload_rpc": "ds_reload",
            "sets": dispatcher_sets,
        },
        "permissions": {
            "model": "opensips.permissions",
            "rules": [
                {
                    "policy_id": policy["id"],
                    "policy_name": policy["policy_name"],
                    "group": policy["kamailio_group"],
                    "priority": policy["priority"],
                    "allow": policy["allowed_source_cidrs"],
                    "deny": policy["blocked_source_cidrs"],
                }
                for policy in norm_trust
            ],
        },
    }

    artifact = {
        "schema_version": POLICY_SCHEMA_VERSION,
        "source_hash": source_hash,
        "opensips": sip_edge_artifact,
        "kamailio": sip_edge_artifact,  # Backward-compatible alias for existing API contracts.
        "freeswitch": {
            "xml_curl": {
                "dialplan_xml": _render_freeswitch_dialplan_xml(tenant_id, compiled_routes),
                "directory_xml": _render_freeswitch_directory_xml(tenant_id),
            },
            "reload_command": "reloadxml",
        },
    }

    return RuntimeCompileResult(
        schema_version=POLICY_SCHEMA_VERSION,
        source_hash=source_hash,
        input_snapshot=input_snapshot,
        artifact=artifact,
    )
