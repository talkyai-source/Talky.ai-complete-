"""Exact mounted success-schema debt; new operations cannot inherit exemptions."""
from __future__ import annotations

import inspect
import json
from pathlib import Path

from fastapi.routing import iter_route_contexts
import pytest
from starlette.routing import WebSocketRoute

from tests.unit.test_openapi_contract import METHODS, contract_snapshot, dereference  # noqa: F401


ROOT = Path(__file__).resolve().parents[3]
BASELINE = ROOT / "backend/tests/fixtures/openapi_response_debt.json"
# Reviewed inherited debt; the five round-three operations have explicit schemas.
RESPONSE_DEBT_CEILING = 193


def write_mounted_route_inventory(app):
    """Share this already-loaded router inventory with the report-only F2-12 audit."""
    schema = app.openapi()
    rows = []
    for route in iter_route_contexts(app.routes):
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue
        endpoint = inspect.unwrap(endpoint)
        identity = f"{endpoint.__module__}.{endpoint.__qualname__}"
        try:
            source = inspect.getsourcefile(endpoint)
            line = inspect.getsourcelines(endpoint)[1]
        except (TypeError, OSError):
            source, line = None, None
        if source:
            source_path = Path(source).resolve()
            source = source_path.relative_to(ROOT).as_posix() if source_path.is_relative_to(ROOT) else source_path.as_posix()
        methods = route.methods or (["WEBSOCKET"] if isinstance(route.original_route, WebSocketRoute) else ["UNKNOWN"])
        for method in sorted(methods):
            operation = schema.get("paths", {}).get(route.path, {}).get(method.lower(), {})
            rows.append({
                "method": method, "path": route.path, "endpoint": identity,
                "source_file": source, "line": line,
                "include_in_schema": bool(getattr(route, "include_in_schema", False)),
                "tags": operation.get("tags", []),
                "documented_security": operation.get("security", schema.get("security")),
                "security_explicit": "security" in operation,
                "dependencies": [
                    f"{dependency.call.__module__}.{getattr(dependency.call, '__qualname__', type(dependency.call).__qualname__)}"
                    for dependency in getattr(getattr(route, "dependant", None), "dependencies", [])
                    if getattr(dependency, "call", None) is not None
                ],
            })
    evidence = ROOT / "tmp/qa-round3-20261106/item42-mounted-routes.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps({"routes": sorted(rows, key=lambda row: (row["path"] or "", row["method"]))}, indent=2) + "\n", encoding="utf-8")


def response_debt(app):
    schema = app.openapi()
    result = {}
    for route in iter_route_contexts(app.routes):
        if not getattr(route, "include_in_schema", False) or not getattr(route, "endpoint", None):
            continue
        endpoint = inspect.unwrap(route.endpoint)
        identity = f"{endpoint.__module__}.{endpoint.__qualname__}"
        for method in getattr(route, "methods", set()):
            if method.lower() not in METHODS:
                continue
            operation = schema["paths"][route.path][method.lower()]
            for code, raw_response in operation.get("responses", {}).items():
                if not str(code).startswith("2") or str(code) in {"204", "205"}:
                    continue
                response = dereference(schema, raw_response)
                content = response.get("content", {})
                # Explicit binary/text representations are documented too.
                documented = any(bool(media.get("schema")) for media in content.values())
                if not documented:
                    result[f"{method} {route.path}"] = {
                        "endpoint": identity,
                        "reason": "Existing successful response lacks a declared body schema; F2-11 response-model follow-up remains open.",
                    }
                    break
    return result


def ratchet_errors(current, baseline, maximum):
    errors = []
    if not isinstance(maximum, int) or maximum < 0 or len(baseline) > maximum:
        errors.append("Legacy response-schema debt exceeded its frozen ceiling")
    for key, value in current.items():
        prior = baseline.get(key)
        if prior is None:
            errors.append(f"{key}: new undocumented operation")
        elif prior.get("endpoint") != value.get("endpoint"):
            errors.append(f"{key}: endpoint changed; its old debt entry cannot be inherited")
        elif not isinstance(prior.get("reason"), str) or not prior["reason"].strip():
            errors.append(f"{key}: missing individual debt reason")
    for key in baseline.keys() - current.keys():
        errors.append(f"{key}: stale debt entry; remove it and reduce the ceiling")
    if len(current) > maximum:
        errors.append("Mounted undocumented operation count increased")
    return errors


def test_actual_mounted_response_debt_matches_reviewed_frozen_baseline(contract_snapshot):
    write_mounted_route_inventory(contract_snapshot.app)
    current = response_debt(contract_snapshot.app)
    evidence = ROOT / "tmp/qa-round3-20261106/item41-response-debt-observed.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps({"count": len(current), "operations": current}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert BASELINE.exists(), f"Review {len(current)} actual response-schema gaps in {evidence}; no automatic exemptions"
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert baseline["maximum"] == len(baseline["operations"]) == RESPONSE_DEBT_CEILING
    errors = ratchet_errors(current, baseline["operations"], baseline["maximum"])
    assert not errors, "\n".join(errors)


@pytest.mark.parametrize("change", ["new", "replaced", "reason_missing", "ceiling", "stale"])
def test_ratchet_rejects_unreviewed_growth_replacements_and_stale_debt(change):
    old = {"GET /legacy": {"endpoint": "app.legacy", "reason": "F2-11 existing response model debt"}}
    current = {key: dict(value) for key, value in old.items()}
    maximum = 1
    if change == "new":
        current["GET /new"] = {"endpoint": "app.new", "reason": ""}
    elif change == "replaced":
        current["GET /legacy"]["endpoint"] = "app.replacement"
    elif change == "reason_missing":
        old["GET /legacy"]["reason"] = ""
    elif change == "ceiling":
        maximum = 0
    elif change == "stale":
        current.clear()
    assert ratchet_errors(current, old, maximum)
