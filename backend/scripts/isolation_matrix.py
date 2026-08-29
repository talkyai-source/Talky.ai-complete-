#!/usr/bin/env python3
"""Cross-tenant isolation matrix runner (goals.md §12, P0).

    python scripts/isolation_matrix.py --base-url http://localhost:8000 \
        --manifest tmp/validation_manifest.json --out tmp/isolation_report.json

WHY THIS EXISTS
---------------
``tests/security`` has ~617 passing tests, and none of them prove this. They run
against fakes that *emulate* the tenant predicate, so they are regression guards
for query construction — not evidence that a real HTTP endpoint refuses a real
cross-tenant request from a real logged-in user of another tenant.

That distinction is not academic here. The production app role is
superuser + BYPASSRLS, so every RLS policy is inert (see
``scripts/verify_rls.py``): isolation is worth exactly as much as the explicit
``tenant_id`` predicate in each hand-written SQL statement, and the only place
that is observable is the HTTP surface. So this runner speaks HTTP.

goals.md §12 "Release Acceptance Criteria" sets the bar at *zero successful
cross-tenant data-access attempts*. This script is what produces that number,
and — just as importantly — what refuses to produce it when the run was
vacuous.

THE NON-VACUITY PROBLEM (read this before trusting any output)
--------------------------------------------------------------
A cross-tenant probe that returns 404 looks identical whether the endpoint
correctly refused, or the URL was typo'd, or the token had expired, or the
resource never existed. This repository has a documented history of guards that
"passed" because their input was constant. So every cross-tenant probe here is
paired with controls, and the run is declared INVALID — never PASS — unless the
controls prove the probe could have detected a leak:

  POSITIVE CONTROL   tenant B's own user fetching B's own resource by the exact
                     same method and URL must SUCCEED (2xx). If it does not, the
                     matching cross-tenant "denial" proves nothing and is not
                     counted as a denial.

  STATUS TRIAGE      only 403/404/410 count as a denial. 401 (token rejected),
                     422/400 (id never reached authorisation), 429 and 5xx are
                     INCONCLUSIVE — they are reported as such and they make the
                     whole run INVALID rather than quietly inflating the
                     "denied" column.

  ABSENT CONTROL     (optional, ``--check-existence-disclosure``) the same
                     request for a random non-existent id. goals.md requires
                     rejection "without revealing whether the target resource
                     exists"; if the cross-tenant status differs from the
                     absent-id status, existence was disclosed. Reported as a
                     warning — it is a §12 finding, not a data leak.

A run with zero non-vacuous checks is INVALID. Read the VERDICT banner; it is
designed to be impossible to misread.

MANIFEST CONTRACT (assumed — reconcile with scripts/seed_validation_tenants.py)
-------------------------------------------------------------------------------
The seeder is being written in parallel, so this is the minimal shape this
runner reads. Unknown keys are ignored, and both ``id``/``tenant_id`` and
``users``/``accounts`` spellings are accepted so the two can converge without a
code change here.

    {
      "manifest_version": 1,
      "generator": "seed_validation_tenants.py",   // provenance, see SAFETY
      "seed": 20260829,
      "created_at": "2026-08-29T00:00:00Z",
      "environment": "validation",
      "tenants": [
        {
          "id": "<uuid>",                 // or "tenant_id"
          "name": "Validation Tenant 001",
          "seeded_by": "seed_validation_tenants.py",   // per-tenant provenance
          "users": [                      // or "accounts"
            {"email": "...", "password": "...", "role": "tenant_admin",
             "user_id": "<uuid>"}
          ],
          "resources": {                  // ids of rows OWNED BY THIS TENANT
            "calls":        ["<uuid>", ...],
            "recordings":   ["<uuid>", ...],
            "campaigns":    ["<uuid>", ...],
            "invoices":     ["<id>",   ...],
            "api_keys":     ["<uuid>", ...],
            "audit_events": ["<uuid>", ...],
            "connectors":   ["<uuid>", ...]
          }
        }
      ]
    }

Only the first id of each resource key is probed by default (``--ids-per-class``
raises it). A resource key that is absent or empty makes its probes SKIPPED —
loudly, and a class that is skipped everywhere contributes nothing to the
non-vacuity count.

RECONCILED against ``scripts/seed_validation_tenants.py`` as it landed on
2026-08-29: it emits both id spellings, per-user ``password`` plus a shared
``credentials.password``, ``seeded_by``/``synthetic`` provenance, and a flat
``resources`` map. Two consequences worth knowing:

  * the seeder creates ``campaigns``, ``leads`` and ``calls``; ``recordings``,
    ``invoices``, ``api_keys``, ``audit_events`` and ``connectors`` are
    deliberately absent, so those probes report SKIPPED until the seeder (or a
    hand-built manifest) supplies ids. SKIPPED is not a pass — it is counted
    and printed separately, and a run that skips everything is INVALID;
  * the API has no single-lead read endpoint (a contact is only addressable as
    ``/campaigns/{campaign_id}/contacts/{contact_id}``), so the seeder's
    ``leads`` ids are not probed directly. Leads are covered through the
    campaign-scoped probes: a campaign that leaks leaks its contacts with it.

A dry-run manifest (``applied: false``, every id null) is rejected — there is
nothing to probe.

SAFETY
------
  * Read-only by default. State-changing probes (does A's admin get to DELETE
    B's campaign?) require ``--allow-mutations``.
  * ``--allow-mutations`` refuses to run unless *every* tenant in the manifest
    carries seeder provenance, because a mutation probe against a real tenant
    would destroy real data.
  * A production-looking base URL is refused outright, in every mode, with no
    override flag.
  * Platform/master-admin users are never used as the actor: they are
    *supposed* to see across tenants, and using one would manufacture a false
    leak.

EXIT CODES
----------
    0  PASS     every cross-tenant probe denied, controls proved it non-vacuous
    1  LEAK     at least one cross-tenant request succeeded — listed in full
    2  INVALID  the run cannot be read as a pass (failed/absent controls,
                inconclusive statuses, or zero non-vacuous checks)
    3  ERROR    refused to run: production URL, unusable manifest, bad usage
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Protocol, Sequence
from urllib.parse import urlparse

# --------------------------------------------------------------------------
# Verdicts and exit codes
# --------------------------------------------------------------------------

VERDICT_PASS = "PASS"
VERDICT_LEAK = "LEAK"
VERDICT_INVALID = "INVALID"

EXIT_PASS = 0
EXIT_LEAK = 1
EXIT_INVALID = 2
EXIT_ERROR = 3

# Outcome of a single cross-tenant probe.
OUTCOME_DENIED = "denied"
OUTCOME_LEAKED = "leaked"
OUTCOME_INCONCLUSIVE = "inconclusive"
OUTCOME_SKIPPED = "skipped"

# Statuses that count as a genuine refusal. 401 is deliberately NOT here: a
# rejected token denies everything, including the probe's ability to detect a
# leak.
DENY_STATUSES = frozenset({403, 404, 410})

EXPECTED_GENERATOR = "seed_validation_tenants.py"

# Roles that legitimately cross tenant boundaries. Never used as the actor.
PLATFORM_ROLES = frozenset(
    {
        "platform_admin",
        "super_admin",
        "superadmin",
        "master_admin",
        "master-admin",
        "staff",
    }
)

# Preference order when the manifest offers several users for a tenant: the
# most privileged in-tenant role is the strongest actor for an IDOR probe.
ACTOR_ROLE_PREFERENCE = (
    "tenant_admin",
    "admin",
    "owner",
    "campaign_manager",
    "billing",
    "agent",
    "operator",
    "viewer",
    "read_only",
)


class IsolationMatrixError(Exception):
    """Refusal to run: bad manifest, unsafe URL, unusable arguments."""


# --------------------------------------------------------------------------
# Production-URL guard
# --------------------------------------------------------------------------

# Hosts that are production, or look enough like it that we will not gamble.
_PROD_HOST_MARKERS = (
    "144.76.17.150",  # Hetzner backend, Blaze-VoIP-API
    "talky.ai",
    "talkyai",
    "blaze-voip",
)
# A first label that says "this is not production" rescues an otherwise
# production-looking host (staging.talky.ai is fine; prod.talky.ai is not).
_NON_PROD_FIRST_LABELS = frozenset(
    {"localhost", "staging", "stage", "validation", "test", "testing", "qa", "dev", "local"}
)
_PROD_LABELS = frozenset({"prod", "production", "live", "www", "api", "app"})


def is_production_url(url: str) -> bool:
    """Does this URL look like production? Conservative: false positives are cheap."""
    host = (urlparse(url).hostname or "").lower().strip(".")
    if not host:
        return False
    labels = host.split(".")
    first = labels[0]
    if first in _NON_PROD_FIRST_LABELS:
        return False
    if first in _PROD_LABELS:
        return True
    return any(marker in host for marker in _PROD_HOST_MARKERS)


def assert_safe_url(url: str) -> None:
    if not urlparse(url).scheme:
        raise IsolationMatrixError(
            f"--base-url must include a scheme, got {url!r} "
            "(e.g. http://localhost:8000)"
        )
    if is_production_url(url):
        raise IsolationMatrixError(
            f"REFUSING to run the isolation matrix against a production-looking "
            f"URL: {url!r}. This runner authenticates as real users and (with "
            "--allow-mutations) deletes rows. Point it at a validation "
            "environment."
        )


# --------------------------------------------------------------------------
# Manifest
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class UserSpec:
    email: str
    password: str
    role: str = ""
    user_id: str = ""


@dataclass(frozen=True)
class TenantSpec:
    id: str
    name: str
    users: tuple[UserSpec, ...]
    resources: dict[str, tuple[str, ...]]
    seeded: bool

    def actor(self, preferred_role: Optional[str] = None) -> Optional[UserSpec]:
        """The user this tenant acts as. Platform admins are never eligible."""
        eligible = [u for u in self.users if u.role.lower() not in PLATFORM_ROLES]
        if not eligible:
            return None
        if preferred_role:
            for u in eligible:
                if u.role.lower() == preferred_role.lower():
                    return u
            return None
        for role in ACTOR_ROLE_PREFERENCE:
            for u in eligible:
                if u.role.lower() == role:
                    return u
        return eligible[0]

    def ids_for(self, key: str, limit: int = 1) -> tuple[str, ...]:
        return tuple(self.resources.get(key, ())[:limit])


@dataclass(frozen=True)
class Manifest:
    version: str
    generator: str
    seed: Optional[str]
    created_at: str
    environment: str
    tenants: tuple[TenantSpec, ...]
    path: str = ""

    @property
    def unseeded_tenant_ids(self) -> tuple[str, ...]:
        return tuple(t.id for t in self.tenants if not t.seeded)


def _as_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, bytes)):
        return (value.decode() if isinstance(value, bytes) else value,)
    if isinstance(value, dict):
        # {"calls": {"id": "..."} } — tolerate a single object.
        ident = value.get("id") or value.get("uuid")
        return (str(ident),) if ident else ()
    out: list[str] = []
    for item in value:
        if isinstance(item, dict):
            ident = item.get("id") or item.get("uuid")
            if ident:
                out.append(str(ident))
        elif item is not None:
            out.append(str(item))
    return tuple(out)


def parse_manifest(data: dict, *, path: str = "") -> Manifest:
    """Normalise the seeder's JSON into the shape this runner probes.

    Deliberately lenient about spelling (``id``/``tenant_id``,
    ``users``/``accounts``) and strict about the two things whose absence would
    silently produce a vacuous run: tenants, and credentials.
    """
    if not isinstance(data, dict):
        raise IsolationMatrixError("manifest root must be a JSON object")

    raw_tenants = data.get("tenants")
    if not isinstance(raw_tenants, list) or not raw_tenants:
        raise IsolationMatrixError("manifest has no 'tenants' list")

    if data.get("applied") is False:
        raise IsolationMatrixError(
            "this is a DRY-RUN manifest (applied=false): its tenant and resource "
            "ids are null, so nothing exists to probe. Re-run the seeder with "
            "--apply and pass the manifest it writes."
        )

    # The seeder publishes one shared synthetic password at the top level as
    # well as on each user; honour it, because a user parsed without a password
    # is dropped, and dropping users is how a matrix ends up with nothing to
    # authenticate as.
    shared_password = ""
    credentials = data.get("credentials")
    if isinstance(credentials, dict):
        shared_password = str(credentials.get("password") or "")

    generator = str(data.get("generator") or "")
    tenants: list[TenantSpec] = []
    for idx, raw in enumerate(raw_tenants):
        if not isinstance(raw, dict):
            raise IsolationMatrixError(f"tenants[{idx}] is not an object")
        tenant_id = raw.get("id") or raw.get("tenant_id")
        if not tenant_id:
            raise IsolationMatrixError(f"tenants[{idx}] has no 'id'/'tenant_id'")

        raw_users = raw.get("users") or raw.get("accounts") or []
        users: list[UserSpec] = []
        for u in raw_users:
            if not isinstance(u, dict):
                continue
            email = u.get("email") or u.get("username")
            password = u.get("password") or shared_password
            if not email or not password:
                # A user without credentials cannot authenticate, and a probe
                # that cannot authenticate is exactly the vacuous case.
                continue
            users.append(
                UserSpec(
                    email=str(email),
                    password=str(password),
                    role=str(u.get("role") or ""),
                    user_id=str(u.get("user_id") or u.get("id") or ""),
                )
            )

        raw_resources = raw.get("resources") or {}
        if not isinstance(raw_resources, dict):
            raise IsolationMatrixError(f"tenants[{idx}].resources is not an object")
        resources = {k: _as_str_tuple(v) for k, v in raw_resources.items()}

        seeded = bool(
            str(raw.get("seeded_by") or "").find(EXPECTED_GENERATOR.split(".")[0]) >= 0
            or raw.get("synthetic") is True
            or EXPECTED_GENERATOR.split(".")[0] in generator
        )

        tenants.append(
            TenantSpec(
                id=str(tenant_id),
                name=str(raw.get("name") or raw.get("business_name") or tenant_id),
                users=tuple(users),
                resources=resources,
                seeded=seeded,
            )
        )

    with_users = [t for t in tenants if t.users]
    if len(with_users) < 2:
        raise IsolationMatrixError(
            "need at least 2 tenants with usable credentials to build a "
            f"cross-tenant matrix; manifest has {len(with_users)}"
        )

    return Manifest(
        version=str(data.get("manifest_version") or data.get("version") or ""),
        generator=generator,
        # The seeder's seed is a string; older hand-built manifests used an int.
        seed=str(data["seed"]) if data.get("seed") is not None else None,
        created_at=str(data.get("created_at") or ""),
        environment=str(data.get("environment") or ""),
        tenants=tuple(tenants),
        path=path,
    )


def load_manifest(path: str) -> Manifest:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError as exc:
        raise IsolationMatrixError(f"manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise IsolationMatrixError(f"manifest is not valid JSON: {path}: {exc}") from exc
    return parse_manifest(data, path=path)


# --------------------------------------------------------------------------
# Pair generation
# --------------------------------------------------------------------------


def ordered_pairs(tenant_ids: Sequence[str]) -> list[tuple[str, str]]:
    """Every ordered pair (actor, target) of distinct tenants.

    Ordered, because isolation is not symmetric: A may be unable to read B while
    B reads A through a different code path.
    """
    return [(a, b) for a in tenant_ids for b in tenant_ids if a != b]


def select_pairs(
    tenant_ids: Sequence[str],
    *,
    max_pairs: Optional[int] = None,
    seed: int = 0,
) -> list[tuple[str, str]]:
    """All ordered pairs, or a deterministic sample of them.

    200 tenants is 39,800 ordered pairs; the full matrix is the release
    artefact, but a smoke run wants a bounded, reproducible subset. The sample
    always leads with the "ring" pairs (i, i+1) so every tenant appears at least
    once as both actor and target before any random pair is added.
    """
    pairs = ordered_pairs(tenant_ids)
    if max_pairs is None or max_pairs <= 0 or max_pairs >= len(pairs):
        return pairs

    n = len(tenant_ids)
    ring = [(tenant_ids[i], tenant_ids[(i + 1) % n]) for i in range(n)] if n > 1 else []
    chosen: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for pair in ring:
        if pair not in seen:
            seen.add(pair)
            chosen.append(pair)
        if len(chosen) >= max_pairs:
            return chosen[:max_pairs]

    rest = [p for p in pairs if p not in seen]
    random.Random(seed).shuffle(rest)
    chosen.extend(rest[: max_pairs - len(chosen)])
    return chosen


# --------------------------------------------------------------------------
# Resource classes and probes
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ProbeSpec:
    """One request template. ``path`` formats a single ``{id}`` placeholder.

    ``control_*`` exists because a mutating probe cannot use itself as its own
    positive control: "prove B can DELETE its own campaign" would destroy the
    fixture the matrix is built on. So a mutating probe names a safe READ of the
    same row, and that read is what proves the row exists and is reachable.
    """

    resource_class: str
    name: str
    method: str
    path: str
    manifest_key: str
    kind: str = "fetch_by_id"  # or "list_contains"
    mutating: bool = False
    control_method: str = ""
    control_path: str = ""
    control_kind: str = ""

    def url_for(self, resource_id: str) -> str:
        return self.path.format(id=resource_id)

    def control(self) -> tuple[str, str, str]:
        """(method, path template, kind) of this probe's positive control."""
        if self.mutating:
            if not self.control_path:
                raise IsolationMatrixError(
                    f"mutating probe {self.name} has no control_path; refusing to use "
                    "the mutation itself as its own control"
                )
            return (self.control_method or "GET", self.control_path, self.control_kind or "fetch_by_id")
        return (self.method, self.path, self.kind)

    def control_url_for(self, resource_id: str) -> str:
        return self.control()[1].format(id=resource_id)


API = "/api/v1"

# The six highest-risk classes goals.md §12 names, mapped onto the routes that
# actually exist in app/api/v1/endpoints (verified against the router prefixes,
# not guessed). Each class bundles several probes because a class is only as
# isolated as its weakest endpoint: /calls/{id} and /calls/{id}/transcript are
# different SQL statements and only one of them has to forget the predicate.
RESOURCE_CLASSES: tuple[ProbeSpec, ...] = (
    # 1. calls, recordings and transcripts
    ProbeSpec("calls_recordings", "call_detail", "GET", f"{API}/calls/{{id}}", "calls"),
    ProbeSpec("calls_recordings", "call_transcript", "GET", f"{API}/calls/{{id}}/transcript", "calls"),
    ProbeSpec("calls_recordings", "call_events", "GET", f"{API}/calls/{{id}}/events", "calls"),
    ProbeSpec("calls_recordings", "recording_url", "GET", f"{API}/recordings/{{id}}/url", "recordings"),
    ProbeSpec("calls_recordings", "call_list_containment", "GET", f"{API}/calls/", "calls", kind="list_contains"),
    ProbeSpec("calls_recordings", "recording_delete", "DELETE", f"{API}/recordings/{{id}}", "recordings",
              mutating=True, control_method="GET", control_path=f"{API}/recordings/{{id}}/url"),
    # 2. campaigns, contacts and captured lead details
    ProbeSpec("campaigns_leads", "campaign_detail", "GET", f"{API}/campaigns/{{id}}", "campaigns"),
    ProbeSpec("campaigns_leads", "campaign_contacts", "GET", f"{API}/campaigns/{{id}}/contacts", "campaigns"),
    ProbeSpec("campaigns_leads", "campaign_calls", "GET", f"{API}/campaigns/{{id}}/calls", "campaigns"),
    ProbeSpec("campaigns_leads", "campaign_lead_fields", "GET", f"{API}/campaigns/{{id}}/lead-fields", "campaigns"),
    ProbeSpec("campaigns_leads", "call_lead_details", "GET", f"{API}/calls/{{id}}/lead-details", "calls"),
    ProbeSpec("campaigns_leads", "campaign_list_containment", "GET", f"{API}/campaigns/", "campaigns", kind="list_contains"),
    ProbeSpec("campaigns_leads", "campaign_delete", "DELETE", f"{API}/campaigns/{{id}}", "campaigns",
              mutating=True, control_method="GET", control_path=f"{API}/campaigns/{{id}}"),
    # 3. billing, invoices and the minute ledger
    ProbeSpec("billing_ledger", "invoice_detail", "GET", f"{API}/billing/invoices/{{id}}", "invoices"),
    ProbeSpec("billing_ledger", "invoice_list_containment", "GET", f"{API}/billing/invoices", "invoices", kind="list_contains"),
    # 4. api keys, audit logs and security settings
    ProbeSpec("api_keys_audit", "audit_event_detail", "GET", f"{API}/admin/audit/logs/{{id}}", "audit_events"),
    ProbeSpec("api_keys_audit", "api_key_list_containment", "GET", f"{API}/admin/api-keys", "api_keys", kind="list_contains"),
    ProbeSpec("api_keys_audit", "api_key_revoke", "POST", f"{API}/admin/api-keys/{{id}}/revoke", "api_keys",
              mutating=True, control_method="GET", control_path=f"{API}/admin/api-keys",
              control_kind="list_contains"),
    # 5. connectors and their encrypted credentials
    ProbeSpec("connectors_credentials", "connector_detail", "GET", f"{API}/connectors/{{id}}", "connectors"),
    ProbeSpec("connectors_credentials", "connector_list_containment", "GET", f"{API}/connectors", "connectors", kind="list_contains"),
    ProbeSpec("connectors_credentials", "connector_delete", "DELETE", f"{API}/connectors/{{id}}", "connectors",
              mutating=True, control_method="GET", control_path=f"{API}/connectors/{{id}}"),
    # 6. conversation reviews and rewards
    ProbeSpec("reviews", "call_reviews", "GET", f"{API}/calls/{{id}}/reviews", "calls"),
    ProbeSpec("reviews", "call_my_review", "GET", f"{API}/calls/{{id}}/review", "calls"),
)


def class_names(probes: Iterable[ProbeSpec] = RESOURCE_CLASSES) -> list[str]:
    seen: list[str] = []
    for p in probes:
        if p.resource_class not in seen:
            seen.append(p.resource_class)
    return seen


def select_probes(
    *,
    allow_mutations: bool,
    only_classes: Optional[Sequence[str]] = None,
    probes: Iterable[ProbeSpec] = RESOURCE_CLASSES,
) -> list[ProbeSpec]:
    wanted = set(only_classes) if only_classes else None
    out = []
    for p in probes:
        if p.mutating and not allow_mutations:
            continue
        if wanted is not None and p.resource_class not in wanted:
            continue
        out.append(p)
    return out


# --------------------------------------------------------------------------
# Status triage
# --------------------------------------------------------------------------


def classify_cross_tenant(
    spec: ProbeSpec, status: int, body: str, target_id: str
) -> tuple[str, str]:
    """(outcome, reason) for one cross-tenant response.

    The whole point of this function is that "not 200" is NOT "denied".
    """
    if spec.kind == "list_contains":
        if 200 <= status < 300:
            if target_id and target_id in body:
                return OUTCOME_LEAKED, f"target id present in {status} listing"
            return OUTCOME_DENIED, f"{status} listing excludes target id"
        if status in DENY_STATUSES:
            return OUTCOME_DENIED, f"{status} on listing"
        return OUTCOME_INCONCLUSIVE, f"listing returned {status}"

    if 200 <= status < 400:
        # A 3xx here is a redirect to a signed asset URL — still a leak.
        return OUTCOME_LEAKED, f"cross-tenant request succeeded with {status}"
    if status in DENY_STATUSES:
        return OUTCOME_DENIED, f"denied with {status}"
    if status == 401:
        return OUTCOME_INCONCLUSIVE, "401: the actor's token was not accepted — proves nothing"
    if status in (400, 422):
        return OUTCOME_INCONCLUSIVE, f"{status}: request never reached authorisation"
    if status == 429:
        return OUTCOME_INCONCLUSIVE, "429: rate limited, probe not evaluated"
    if status >= 500:
        return OUTCOME_INCONCLUSIVE, f"{status}: server error, probe not evaluated"
    return OUTCOME_INCONCLUSIVE, f"unexpected status {status}"


def control_passed(kind: str, status: int, body: str, own_id: str) -> tuple[bool, str]:
    """Did the positive control prove the probe could have seen a leak?"""
    if not (200 <= status < 300):
        return False, f"own-resource request returned {status}"
    if kind == "list_contains":
        if own_id and own_id not in body:
            return False, "own listing does not contain the tenant's own resource id"
    return True, f"own resource readable ({status})"


# --------------------------------------------------------------------------
# Result / report model
# --------------------------------------------------------------------------


@dataclass
class ProbeResult:
    resource_class: str
    probe: str
    method: str
    url: str
    actor_tenant: str
    actor_email: str
    target_tenant: str
    resource_id: str
    status: Optional[int]
    outcome: str
    reason: str
    positive_control_passed: bool
    positive_control_reason: str = ""
    body_excerpt: str = ""
    existence_disclosed: Optional[bool] = None

    def to_dict(self) -> dict:
        return {
            "resource_class": self.resource_class,
            "probe": self.probe,
            "method": self.method,
            "url": self.url,
            "actor_tenant": self.actor_tenant,
            "actor_email": self.actor_email,
            "target_tenant": self.target_tenant,
            "resource_id": self.resource_id,
            "status": self.status,
            "outcome": self.outcome,
            "reason": self.reason,
            "positive_control_passed": self.positive_control_passed,
            "positive_control_reason": self.positive_control_reason,
            "body_excerpt": self.body_excerpt,
            "existence_disclosed": self.existence_disclosed,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ProbeResult":
        return cls(
            resource_class=data["resource_class"],
            probe=data["probe"],
            method=data["method"],
            url=data["url"],
            actor_tenant=data["actor_tenant"],
            actor_email=data.get("actor_email", ""),
            target_tenant=data["target_tenant"],
            resource_id=data.get("resource_id", ""),
            status=data.get("status"),
            outcome=data["outcome"],
            reason=data.get("reason", ""),
            positive_control_passed=bool(data.get("positive_control_passed")),
            positive_control_reason=data.get("positive_control_reason", ""),
            body_excerpt=data.get("body_excerpt", ""),
            existence_disclosed=data.get("existence_disclosed"),
        )


def _empty_counters() -> dict:
    return {
        "attempted": 0,
        "denied": 0,
        "leaked": 0,
        "inconclusive": 0,
        "skipped": 0,
        "positive_control_passed": 0,
        "positive_control_failed": 0,
        "non_vacuous": 0,
    }


def tally(results: Iterable[ProbeResult]) -> dict:
    c = _empty_counters()
    for r in results:
        if r.outcome == OUTCOME_SKIPPED:
            c["skipped"] += 1
            continue
        c["attempted"] += 1
        c[r.outcome] += 1
        if r.positive_control_passed:
            c["positive_control_passed"] += 1
            if r.outcome in (OUTCOME_DENIED, OUTCOME_LEAKED):
                c["non_vacuous"] += 1
        else:
            c["positive_control_failed"] += 1
    return c


@dataclass
class Report:
    run_id: str
    started_at: str
    finished_at: str
    base_url: str
    manifest_path: str
    manifest_version: str
    manifest_generator: str
    manifest_seed: Optional[str]
    tenant_count: int
    mutations_enabled: bool
    concurrency: int
    pairs: list[tuple[str, str]] = field(default_factory=list)
    results: list[ProbeResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    unseeded_tenants: list[str] = field(default_factory=list)

    # ---- derived ---------------------------------------------------------
    @property
    def leaks(self) -> list[ProbeResult]:
        return [r for r in self.results if r.outcome == OUTCOME_LEAKED]

    @property
    def totals(self) -> dict:
        return tally(self.results)

    def by_resource_class(self) -> dict:
        out: dict[str, dict] = {}
        for name in class_names():
            subset = [r for r in self.results if r.resource_class == name]
            if subset:
                out[name] = tally(subset)
        for r in self.results:  # classes from a custom probe set
            out.setdefault(
                r.resource_class,
                tally([x for x in self.results if x.resource_class == r.resource_class]),
            )
        return out

    def by_pair(self) -> list[dict]:
        index: dict[tuple[str, str], list[ProbeResult]] = {}
        for r in self.results:
            index.setdefault((r.actor_tenant, r.target_tenant), []).append(r)
        rows = []
        for (a, b), subset in index.items():
            classes = {}
            for name in sorted({r.resource_class for r in subset}):
                classes[name] = tally([r for r in subset if r.resource_class == name])
            rows.append({"actor_tenant": a, "target_tenant": b, "classes": classes})
        return rows

    def verdict(self) -> tuple[str, list[str]]:
        """(verdict, reasons). INVALID beats PASS; LEAK beats everything."""
        t = self.totals
        reasons: list[str] = []
        if t["leaked"]:
            reasons.append(f"{t['leaked']} cross-tenant request(s) succeeded")
            return VERDICT_LEAK, reasons

        if self.errors:
            reasons.append(f"{len(self.errors)} runner error(s), e.g. {self.errors[0]}")
        if t["positive_control_failed"]:
            reasons.append(
                f"{t['positive_control_failed']} probe(s) had a FAILED positive control "
                "— their 'denials' prove nothing"
            )
        if t["inconclusive"]:
            reasons.append(
                f"{t['inconclusive']} probe(s) returned an inconclusive status "
                "(401/400/422/429/5xx)"
            )
        if t["non_vacuous"] == 0:
            reasons.append("zero non-vacuous cross-tenant checks were performed")
        if reasons:
            return VERDICT_INVALID, reasons
        return VERDICT_PASS, [
            f"{t['non_vacuous']} non-vacuous cross-tenant checks, all denied"
        ]

    def exit_code(self) -> int:
        verdict, _ = self.verdict()
        return {
            VERDICT_PASS: EXIT_PASS,
            VERDICT_LEAK: EXIT_LEAK,
            VERDICT_INVALID: EXIT_INVALID,
        }[verdict]

    # ---- serialisation ---------------------------------------------------
    def to_dict(self) -> dict:
        verdict, reasons = self.verdict()
        return {
            "schema": "talky.isolation_matrix/1",
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "base_url": self.base_url,
            "manifest": {
                "path": self.manifest_path,
                "version": self.manifest_version,
                "generator": self.manifest_generator,
                "seed": self.manifest_seed,
                "tenant_count": self.tenant_count,
                "unseeded_tenants": list(self.unseeded_tenants),
            },
            "mode": {
                "mutations_enabled": self.mutations_enabled,
                "concurrency": self.concurrency,
                "pair_count": len(self.pairs),
            },
            "verdict": verdict,
            "verdict_reasons": reasons,
            "exit_code": self.exit_code(),
            "totals": self.totals,
            "by_resource_class": self.by_resource_class(),
            "by_pair": self.by_pair(),
            "leaks": [r.to_dict() for r in self.leaks],
            "results": [r.to_dict() for r in self.results],
            "errors": list(self.errors),
            "pairs": [list(p) for p in self.pairs],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Report":
        manifest = data.get("manifest", {})
        mode = data.get("mode", {})
        return cls(
            run_id=data["run_id"],
            started_at=data.get("started_at", ""),
            finished_at=data.get("finished_at", ""),
            base_url=data.get("base_url", ""),
            manifest_path=manifest.get("path", ""),
            manifest_version=manifest.get("version", ""),
            manifest_generator=manifest.get("generator", ""),
            manifest_seed=manifest.get("seed"),
            tenant_count=int(manifest.get("tenant_count") or 0),
            mutations_enabled=bool(mode.get("mutations_enabled")),
            concurrency=int(mode.get("concurrency") or 1),
            pairs=[(p[0], p[1]) for p in data.get("pairs", [])],
            results=[ProbeResult.from_dict(r) for r in data.get("results", [])],
            errors=list(data.get("errors", [])),
            unseeded_tenants=list(manifest.get("unseeded_tenants", [])),
        )


# --------------------------------------------------------------------------
# Transport
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: str


class ProbeClient(Protocol):
    """The only thing the matrix needs from the world. Fakeable in tests."""

    async def login(self, email: str, password: str) -> str: ...

    async def request(self, method: str, path: str, token: str) -> HttpResponse: ...


class HttpxProbeClient:
    """Real HTTP. Bearer auth only — bearer requests are CSRF-exempt
    (app/core/security/csrf.py), so mutation probes do not need an Origin."""

    def __init__(self, base_url: str, *, timeout: float = 20.0, verify: bool = True):
        import httpx  # imported lazily so the pure logic is testable without it

        self._base = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout, verify=verify, follow_redirects=False)

    async def __aenter__(self) -> "HttpxProbeClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def login(self, email: str, password: str) -> str:
        resp = await self._client.post(
            f"{self._base}{API}/auth/login", json={"email": email, "password": password}
        )
        if resp.status_code != 200:
            raise IsolationMatrixError(
                f"login failed for {email}: HTTP {resp.status_code} {resp.text[:200]}"
            )
        data = resp.json()
        if data.get("mfa_required"):
            raise IsolationMatrixError(
                f"login for {email} requires MFA; seed validation users without MFA"
            )
        token = data.get("access_token")
        if not token:
            raise IsolationMatrixError(f"login for {email} returned no access_token")
        return str(token)

    async def request(self, method: str, path: str, token: str) -> HttpResponse:
        resp = await self._client.request(
            method,
            f"{self._base}{path}",
            headers={"Authorization": f"Bearer {token}"},
        )
        return HttpResponse(status=resp.status_code, body=resp.text)


# --------------------------------------------------------------------------
# The matrix
# --------------------------------------------------------------------------

_EXCERPT = 300


class MatrixRunner:
    def __init__(
        self,
        client: ProbeClient,
        manifest: Manifest,
        probes: Sequence[ProbeSpec],
        *,
        base_url: str = "",
        concurrency: int = 2,
        ids_per_class: int = 1,
        actor_role: Optional[str] = None,
        check_existence_disclosure: bool = False,
        recheck_controls: bool = False,
        mutations_enabled: bool = False,
    ) -> None:
        self.client = client
        self.manifest = manifest
        self.probes = list(probes)
        self.base_url = base_url
        self.concurrency = max(1, int(concurrency))
        self.ids_per_class = max(1, int(ids_per_class))
        self.actor_role = actor_role
        self.check_existence_disclosure = check_existence_disclosure
        self.recheck_controls = recheck_controls
        self.mutations_enabled = mutations_enabled

        self._tenants = {t.id: t for t in manifest.tenants}
        self._tokens: dict[str, str] = {}
        self._token_locks: dict[str, asyncio.Lock] = {}
        # (target_tenant, probe_name, resource_id) -> (passed, reason)
        self._controls: dict[tuple[str, str, str], tuple[bool, str]] = {}
        self._control_locks: dict[tuple[str, str, str], asyncio.Lock] = {}
        self.errors: list[str] = []

    # -- auth --------------------------------------------------------------
    async def token_for(self, tenant_id: str) -> tuple[str, str]:
        """(token, actor_email) for a tenant. Cached; one login per tenant."""
        lock = self._token_locks.setdefault(tenant_id, asyncio.Lock())
        async with lock:
            tenant = self._tenants[tenant_id]
            user = tenant.actor(self.actor_role)
            if user is None:
                raise IsolationMatrixError(
                    f"tenant {tenant_id} has no usable non-platform user"
                    + (f" with role {self.actor_role}" if self.actor_role else "")
                )
            cached = self._tokens.get(tenant_id)
            if cached is None:
                cached = await self.client.login(user.email, user.password)
                self._tokens[tenant_id] = cached
            return cached, user.email

    # -- controls ----------------------------------------------------------
    async def positive_control(
        self, spec: ProbeSpec, target: TenantSpec, resource_id: str
    ) -> tuple[bool, str]:
        """Target tenant's own user reading its own resource. Must succeed.

        Evaluated once per (target tenant, probe, id) and reused across the
        pairs that share it — the request is identical, and re-issuing it
        39,799 times would not add information. ``--recheck-controls`` disables
        the cache when you want the belt-and-braces version.
        """
        key = (target.id, spec.name, resource_id)
        if not self.recheck_controls and key in self._controls:
            return self._controls[key]
        lock = self._control_locks.setdefault(key, asyncio.Lock())
        async with lock:
            if not self.recheck_controls and key in self._controls:
                return self._controls[key]
            try:
                method, _template, kind = spec.control()
                token, _ = await self.token_for(target.id)
                resp = await self.client.request(
                    method, spec.control_url_for(resource_id), token
                )
                outcome = control_passed(kind, resp.status, resp.body, resource_id)
            except IsolationMatrixError as exc:
                outcome = (False, f"control request failed: {exc}")
            except Exception as exc:  # noqa: BLE001 — a control that errored is a failed control
                outcome = (False, f"control request errored: {type(exc).__name__}: {exc}")
            self._controls[key] = outcome
            return outcome

    async def absent_control(self, spec: ProbeSpec, actor_tenant: str) -> Optional[int]:
        """Status the actor gets for an id that exists in no tenant at all."""
        try:
            token, _ = await self.token_for(actor_tenant)
            resp = await self.client.request(
                spec.method, spec.url_for(str(uuid.uuid4())), token
            )
            return resp.status
        except Exception:  # noqa: BLE001 — best effort; this only produces a warning
            return None

    # -- one probe ---------------------------------------------------------
    async def run_probe(
        self, spec: ProbeSpec, actor: TenantSpec, target: TenantSpec
    ) -> list[ProbeResult]:
        ids = target.ids_for(spec.manifest_key, self.ids_per_class)
        if not ids:
            return [
                ProbeResult(
                    resource_class=spec.resource_class,
                    probe=spec.name,
                    method=spec.method,
                    url=spec.path,
                    actor_tenant=actor.id,
                    actor_email="",
                    target_tenant=target.id,
                    resource_id="",
                    status=None,
                    outcome=OUTCOME_SKIPPED,
                    reason=(
                        f"manifest has no '{spec.manifest_key}' ids for target tenant "
                        f"{target.id} — nothing to attempt"
                    ),
                    positive_control_passed=False,
                    positive_control_reason="not run (no resource id)",
                )
            ]

        out: list[ProbeResult] = []
        for resource_id in ids:
            # For a mutation probe the positive control would itself mutate, so
            # the control is the target's own READ of the same row before the
            # attempt: it proves the row exists and is reachable.
            control_ok, control_reason = await self.positive_control(spec, target, resource_id)

            url = spec.url_for(resource_id)
            try:
                token, actor_email = await self.token_for(actor.id)
                resp = await self.client.request(spec.method, url, token)
            except IsolationMatrixError as exc:
                self.errors.append(str(exc))
                out.append(
                    ProbeResult(
                        resource_class=spec.resource_class,
                        probe=spec.name,
                        method=spec.method,
                        url=url,
                        actor_tenant=actor.id,
                        actor_email="",
                        target_tenant=target.id,
                        resource_id=resource_id,
                        status=None,
                        outcome=OUTCOME_INCONCLUSIVE,
                        reason=f"probe could not be issued: {exc}",
                        positive_control_passed=control_ok,
                        positive_control_reason=control_reason,
                    )
                )
                continue
            except Exception as exc:  # noqa: BLE001
                self.errors.append(f"{spec.name} {url}: {type(exc).__name__}: {exc}")
                out.append(
                    ProbeResult(
                        resource_class=spec.resource_class,
                        probe=spec.name,
                        method=spec.method,
                        url=url,
                        actor_tenant=actor.id,
                        actor_email="",
                        target_tenant=target.id,
                        resource_id=resource_id,
                        status=None,
                        outcome=OUTCOME_INCONCLUSIVE,
                        reason=f"transport error: {type(exc).__name__}: {exc}",
                        positive_control_passed=control_ok,
                        positive_control_reason=control_reason,
                    )
                )
                continue

            outcome, reason = classify_cross_tenant(spec, resp.status, resp.body, resource_id)
            if outcome == OUTCOME_DENIED and not control_ok:
                # The single most important line in this file: a denial whose
                # control failed is not a denial, it is an unknown.
                outcome = OUTCOME_INCONCLUSIVE
                reason = f"{reason}, but the positive control failed ({control_reason})"

            disclosed: Optional[bool] = None
            if (
                self.check_existence_disclosure
                and spec.kind == "fetch_by_id"
                and outcome == OUTCOME_DENIED
            ):
                absent = await self.absent_control(spec, actor.id)
                if absent is not None:
                    disclosed = absent != resp.status

            out.append(
                ProbeResult(
                    resource_class=spec.resource_class,
                    probe=spec.name,
                    method=spec.method,
                    url=url,
                    actor_tenant=actor.id,
                    actor_email=actor_email,
                    target_tenant=target.id,
                    resource_id=resource_id,
                    status=resp.status,
                    outcome=outcome,
                    reason=reason,
                    positive_control_passed=control_ok,
                    positive_control_reason=control_reason,
                    body_excerpt=resp.body[:_EXCERPT],
                    existence_disclosed=disclosed,
                )
            )
        return out

    # -- the run -----------------------------------------------------------
    async def run(self, pairs: Sequence[tuple[str, str]]) -> Report:
        started = datetime.now(timezone.utc).isoformat()
        sem = asyncio.Semaphore(self.concurrency)
        results: list[ProbeResult] = []

        async def one_pair(a: str, b: str) -> list[ProbeResult]:
            async with sem:
                actor, target = self._tenants[a], self._tenants[b]
                got: list[ProbeResult] = []
                for spec in self.probes:
                    got.extend(await self.run_probe(spec, actor, target))
                return got

        gathered = await asyncio.gather(
            *(one_pair(a, b) for a, b in pairs), return_exceptions=True
        )
        for item in gathered:
            if isinstance(item, BaseException):
                self.errors.append(f"pair failed: {type(item).__name__}: {item}")
            else:
                results.extend(item)

        return Report(
            run_id=uuid.uuid4().hex[:12],
            started_at=started,
            finished_at=datetime.now(timezone.utc).isoformat(),
            base_url=self.base_url,
            manifest_path=self.manifest.path,
            manifest_version=self.manifest.version,
            manifest_generator=self.manifest.generator,
            manifest_seed=self.manifest.seed,
            tenant_count=len(self.manifest.tenants),
            mutations_enabled=self.mutations_enabled,
            concurrency=self.concurrency,
            pairs=list(pairs),
            results=results,
            errors=list(self.errors),
            unseeded_tenants=list(self.manifest.unseeded_tenant_ids),
        )


# --------------------------------------------------------------------------
# Human summary
# --------------------------------------------------------------------------

_BAR = "=" * 78


def render_summary(report: Report) -> str:
    verdict, reasons = report.verdict()
    t = report.totals
    lines: list[str] = []
    lines.append(_BAR)
    lines.append(f"  CROSS-TENANT ISOLATION MATRIX — run {report.run_id}")
    lines.append(f"  {report.base_url}   manifest={report.manifest_path or '(inline)'}")
    lines.append(
        f"  tenants={report.tenant_count}  pairs={len(report.pairs)}  "
        f"mutations={'ON' if report.mutations_enabled else 'off'}  "
        f"concurrency={report.concurrency}"
    )
    lines.append(_BAR)
    lines.append("")
    lines.append(
        f"  {'class':<26} {'att':>5} {'deny':>5} {'LEAK':>5} {'incon':>6} "
        f"{'skip':>5} {'ctl+':>5} {'ctl-':>5} {'valid':>6}"
    )
    lines.append("  " + "-" * 74)
    for name, c in report.by_resource_class().items():
        lines.append(
            f"  {name:<26} {c['attempted']:>5} {c['denied']:>5} {c['leaked']:>5} "
            f"{c['inconclusive']:>6} {c['skipped']:>5} {c['positive_control_passed']:>5} "
            f"{c['positive_control_failed']:>5} {c['non_vacuous']:>6}"
        )
    lines.append("  " + "-" * 74)
    lines.append(
        f"  {'TOTAL':<26} {t['attempted']:>5} {t['denied']:>5} {t['leaked']:>5} "
        f"{t['inconclusive']:>6} {t['skipped']:>5} {t['positive_control_passed']:>5} "
        f"{t['positive_control_failed']:>5} {t['non_vacuous']:>6}"
    )
    lines.append("")

    if report.leaks:
        lines.append("  LEAKS — each of these is a successful cross-tenant access:")
        for r in report.leaks:
            lines.append(
                f"    * [{r.resource_class}/{r.probe}] tenant {r.actor_tenant} "
                f"({r.actor_email}) read tenant {r.target_tenant}'s "
                f"{r.resource_id or '(listing)'}"
            )
            lines.append(f"        {r.method} {r.url}  ->  HTTP {r.status}  ({r.reason})")
            if r.body_excerpt:
                excerpt = r.body_excerpt.replace("\n", " ")[:200]
                lines.append(f"        body: {excerpt}")
        lines.append("")

    disclosures = [r for r in report.results if r.existence_disclosed]
    if disclosures:
        lines.append(
            f"  WARNING — {len(disclosures)} denial(s) revealed that the target resource "
            "exists (§12: reject without disclosing existence):"
        )
        for r in disclosures[:10]:
            lines.append(f"    * {r.method} {r.url} -> {r.status} (differs from absent-id status)")
        lines.append("")

    if report.errors:
        lines.append(f"  RUNNER ERRORS ({len(report.errors)}):")
        for e in report.errors[:10]:
            lines.append(f"    * {e}")
        lines.append("")

    banner = {
        VERDICT_PASS: "VERDICT: PASS — zero successful cross-tenant accesses",
        VERDICT_LEAK: "VERDICT: LEAK — CROSS-TENANT DATA WAS SERVED",
        VERDICT_INVALID: "VERDICT: INVALID — THIS RUN IS NOT A PASS AND PROVES NOTHING",
    }[verdict]
    lines.append("#" * 78)
    lines.append(f"#  {banner}")
    for reason in reasons:
        lines.append(f"#    - {reason}")
    if verdict == VERDICT_INVALID:
        lines.append("#  Do not record this run as evidence. Fix the controls and re-run.")
    lines.append(f"#  exit code {report.exit_code()}")
    lines.append("#" * 78)
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="isolation_matrix.py",
        description="Cross-tenant isolation matrix over the live HTTP API (goals.md §12).",
    )
    p.add_argument("--base-url", required=True, help="API base, e.g. http://localhost:8000")
    p.add_argument("--manifest", required=True, help="manifest.json from seed_validation_tenants.py")
    p.add_argument("--out", default="", help="write the JSON report here (default: stdout only)")
    p.add_argument("--max-pairs", type=int, default=0, help="0 = every ordered pair")
    p.add_argument("--sample-seed", type=int, default=0, help="seed for --max-pairs sampling")
    p.add_argument(
        "--concurrency", type=int, default=2, help="pairs probed in parallel (default 2)"
    )
    p.add_argument("--ids-per-class", type=int, default=1, help="resource ids probed per class")
    p.add_argument("--actor-role", default="", help="force the acting role, e.g. tenant_admin")
    p.add_argument(
        "--classes", default="", help=f"comma-separated subset of {','.join(class_names())}"
    )
    p.add_argument(
        "--allow-mutations",
        action="store_true",
        help="also attempt DELETE/POST against the other tenant's rows (seeded tenants only)",
    )
    p.add_argument(
        "--check-existence-disclosure",
        action="store_true",
        help="also probe a random absent id to detect existence disclosure",
    )
    p.add_argument(
        "--recheck-controls",
        action="store_true",
        help="re-issue the positive control for every pair instead of caching it",
    )
    p.add_argument("--insecure", action="store_true", help="skip TLS verification")
    p.add_argument(
        "--plan-only",
        action="store_true",
        help="print the probe plan and exit without touching the network",
    )
    return p


def build_plan(manifest: Manifest, probes: Sequence[ProbeSpec], pairs: Sequence[tuple[str, str]]) -> dict:
    return {
        "tenants": len(manifest.tenants),
        "pairs": len(pairs),
        "probes_per_pair": len(probes),
        "total_requests_upper_bound": len(pairs) * len(probes) * 2,
        "classes": {
            name: [p.name for p in probes if p.resource_class == name]
            for name in class_names(probes)
        },
    }


async def _amain(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        assert_safe_url(args.base_url)
        manifest = load_manifest(args.manifest)
    except IsolationMatrixError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR

    if args.allow_mutations and manifest.unseeded_tenant_ids:
        print(
            "ERROR: --allow-mutations refused: the manifest contains "
            f"{len(manifest.unseeded_tenant_ids)} tenant(s) without seeder provenance "
            f"(e.g. {manifest.unseeded_tenant_ids[0]}). Mutation probes delete rows; "
            "they run only against tenants created by "
            f"{EXPECTED_GENERATOR}.",
            file=sys.stderr,
        )
        return EXIT_ERROR

    only = [c.strip() for c in args.classes.split(",") if c.strip()] or None
    if only:
        unknown = sorted(set(only) - set(class_names()))
        if unknown:
            print(f"ERROR: unknown resource class(es): {', '.join(unknown)}", file=sys.stderr)
            return EXIT_ERROR

    probes = select_probes(allow_mutations=args.allow_mutations, only_classes=only)
    if not probes:
        print("ERROR: no probes selected", file=sys.stderr)
        return EXIT_ERROR

    tenant_ids = [t.id for t in manifest.tenants if t.users]
    pairs = select_pairs(tenant_ids, max_pairs=args.max_pairs, seed=args.sample_seed)

    if args.plan_only:
        print(json.dumps(build_plan(manifest, probes, pairs), indent=2))
        return EXIT_PASS

    client = HttpxProbeClient(args.base_url, verify=not args.insecure)
    try:
        runner = MatrixRunner(
            client,
            manifest,
            probes,
            base_url=args.base_url,
            concurrency=args.concurrency,
            ids_per_class=args.ids_per_class,
            actor_role=args.actor_role or None,
            check_existence_disclosure=args.check_existence_disclosure,
            recheck_controls=args.recheck_controls,
            mutations_enabled=args.allow_mutations,
        )
        report = await runner.run(pairs)
    finally:
        await client.aclose()

    payload = report.to_dict()
    if args.out:
        directory = os.path.dirname(os.path.abspath(args.out))
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        print(f"report written to {args.out}", file=sys.stderr)
    else:
        print(json.dumps(payload, indent=2))

    print(render_summary(report), file=sys.stderr)
    return report.exit_code()


def main(argv: Optional[Sequence[str]] = None) -> int:
    try:
        return asyncio.run(_amain(argv))
    except IsolationMatrixError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
