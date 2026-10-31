#!/usr/bin/env python3
"""Seed deterministic synthetic tenants for the goals.md §12 validation run.

    python scripts/seed_validation_tenants.py                       # dry run
    python scripts/seed_validation_tenants.py --tenants 50 \
        --api-base-url http://localhost:8000 --admin-token "$TOKEN" --apply

WHY THIS EXISTS
---------------
goals.md §12 ("Client Management and 200-Tenant Validation") is a P0 that has
never produced any evidence, because there was nothing to produce it *with*:
no synthetic population, no manifest, and until now no tenant create verb to
call.  Every downstream artefact §12 asks for -- the isolation matrix, the
billing reconciliation, the 200-session soak -- consumes a population and a
manifest.  This script is that population and that manifest.

FIFTY, NOT TWO HUNDRED
----------------------
The default is ``--tenants 50``.  Two hundred tenants with no seeder and no
matrix is a number; fifty tenants with a real isolation matrix and a real
soak is evidence.  The count is a parameter -- raise it once the matrix that
consumes this manifest exists and passes.

WHAT IT GUARANTEES
------------------
* **Deterministic.**  Everything -- names, slugs, emails, plans, statuses,
  phone numbers, lead names, call durations -- is derived from ``--seed`` and
  an index.  The one RNG is a ``random.Random`` explicitly seeded from a
  blake2b digest of ``seed|slug`` (never Python's salted ``hash()``), and no
  wall-clock value reaches any seeded row: timestamps are offsets from the
  fixed ``DATA_EPOCH``.  Two runs with the same arguments produce byte-identical
  plans, and ``manifest["plan_digest"]`` proves it.
* **Idempotent end to end.**  See "IDEMPOTENCY" below -- one natural key per
  entity type, stated explicitly.
* **Tenant-scoped by construction.**  Every generated INSERT carries an
  explicit ``tenant_id`` and ``PlannedInsert.__post_init__`` refuses to build
  one that does not.  The prod app role is superuser+BYPASSRLS, so RLS would
  not catch a leak here; a seeder that mixed rows across tenants would
  silently invalidate the very matrix it exists to feed.
* **Dry run by default.**  Nothing is written without ``--apply``.

IDEMPOTENCY -- WHAT EACH ENTITY RELIES ON
-----------------------------------------
* ``tenants``        -- ``POST /api/v1/admin/tenants`` is idempotent on the
                        case-insensitive natural key ``business_name`` and
                        returns the existing row with ``created: false``.
                        This script never INSERTs a tenant itself.
* ``tenant_quotas``  -- created by that endpoint (``ON CONFLICT (tenant_id)``).
* ``user_profiles``  -- ``email`` is globally UNIQUE and every generated email
                        embeds the tenant slug, so it is unique per tenant and
                        collision-free across them.
* ``campaigns``      -- no natural unique key exists, so idempotency rests on
                        a deterministic UUIDv5 primary key derived from
                        ``(seed, tenant_id, "campaign", index)``.
* ``leads``          -- deterministic UUIDv5 primary key, and additionally the
                        partial unique index
                        ``idx_leads_campaign_phone_unique (campaign_id,
                        phone_number) WHERE status <> 'deleted'``.
* ``calls``          -- deterministic UUIDv5 primary key.

Every statement is ``INSERT ... ON CONFLICT DO NOTHING`` with **no arbiter**,
so it is skipped on a conflict with *any* unique index, not just the one we
were thinking of.  (A partial unique index cannot be named as an ``ON
CONFLICT`` target unless the predicate is repeated exactly; omitting the
target sidesteps that trap entirely.)  A re-run therefore reports every row
as ``existing`` and writes nothing.

PRODUCTION GUARD
----------------
``classify_database_target()`` reads the DSN and the process environment and
returns one of three verdicts:

  ``production`` -- ``ENVIRONMENT``/``APP_ENV``/``ENV``/``TALKY_ENV`` is
      production/prod/live, OR the host is a known production host, OR the
      host or database name contains ``prod``/``production``/``live`` as a
      whole token.  **Refused unconditionally.**  No flag lifts it: seeding
      50 fake tenants into production is an incident, and a flag that can
      turn the check off is a flag someone will paste from a runbook.
  ``local``      -- host is loopback / a compose service name.  Allowed.
  ``remote``     -- anything else.  Refused **unless** ``--allow-remote-database``
      is passed, which is the explicit operator acknowledgement that this
      staging/scratch database may be filled with synthetic data.

The guard runs on dry runs too.  A dry run pointed at production is a
mistake worth surfacing before the ``--apply`` re-run.

MIGRATION 0035 MUST BE APPLIED FIRST
------------------------------------
``user_profiles.role`` carried a CHECK that allowed only the five legacy role
names.  ``0035_user_profiles_role_widen`` widens it to all eight.  Without it
every ``campaign_manager``, ``agent`` and ``billing_user`` INSERT raises a
check violation.  ``--apply`` therefore reads the constraint definition out of
``pg_constraint`` first and aborts with one actionable line if it is still
narrow, rather than emitting a wall of constraint violations.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import re
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

MANIFEST_VERSION = 1

# ---------------------------------------------------------------------------
# The role mix goals.md §12 names.  Spelled out here rather than imported from
# app.core.security.rbac so this module stays importable without the FastAPI
# stack; tests/unit/test_seed_validation_tenants.py compares both lists against
# UserRole so the two cannot drift -- the same guard migration 0035 uses.
# ---------------------------------------------------------------------------

# "Create at least one tenant administrator for every tenant" plus the
# additional role samples §12 lists.  Order is the order §12 lists them.
TENANT_ROLE_MIX: tuple[str, ...] = (
    "tenant_admin",
    "campaign_manager",
    "agent",          # §12 spells this "Agent/operator"; "operator" is an
                      # alias of AGENT in rbac.ROLE_ALIASES.
    "billing_user",
    "readonly",
)

# "Partner/reseller user, where supported" -- seeded on a subset of tenants
# because a partner is by nature cross-tenant, not one-per-tenant.
PARTNER_ROLE = "partner_admin"

# "Create a master-admin account that can manage all N tenants."  The only
# principal in the population with no tenant_id, by definition.
MASTER_ADMIN_ROLE = "platform_admin"

# Every role name this script writes -- what the 0035 CHECK must allow.
SEEDED_ROLES: tuple[str, ...] = TENANT_ROLE_MIX + (PARTNER_ROLE, MASTER_ADMIN_ROLE)

# ---------------------------------------------------------------------------
# Population shape
# ---------------------------------------------------------------------------

DEFAULT_TENANT_COUNT = 50
DEFAULT_SEED = "talky-goals-12-v1"
DEFAULT_EMAIL_DOMAIN = "validation.talky.invalid"  # .invalid never resolves
DEFAULT_NAME_PREFIX = "Talky Validation"

# Only plans present in BOTH database/complete_schema.sql's default rows and
# the production dump.  'free' exists only in seed_data.sql, so seeding it
# would 400 on a schema-bootstrapped database.  Override with --plans.
DEFAULT_PLANS: tuple[str, ...] = ("basic", "professional", "enterprise")

# Per-plan quota baselines, matching the plans rows in complete_schema.sql.
# Passed explicitly so the manifest does not depend on the target's plans
# table, and so §12's "seed different plans, balances, quotas" is satisfied.
_PLAN_BASELINES: dict[str, tuple[int, int]] = {
    "free": (30, 1),
    "basic": (300, 1),
    "professional": (1500, 3),
    "enterprise": (5000, 10),
}
_PLAN_FALLBACK = (500, 2)

# §12: "Seed active, trial, suspended, cancelled and overdue client states."
# Weighted towards active so most tenants are usable for the isolation matrix.
SUBSCRIPTION_STATUS_CYCLE: tuple[str, ...] = (
    "active",
    "active",
    "trialing",
    "past_due",     # "overdue"
    "suspended",
    "cancelled",
)

CAMPAIGN_STATUS_CYCLE: tuple[str, ...] = ("active", "draft", "paused", "completed")
LEAD_STATUS_CYCLE: tuple[str, ...] = ("pending", "contacted", "completed", "failed")
CALL_STATUS_CYCLE: tuple[str, ...] = ("completed", "completed", "failed", "no_answer")
CALL_OUTCOME_CYCLE: tuple[str, ...] = (
    "interested",
    "not_interested",
    "callback_requested",
    "voicemail",
)

# Every seeded timestamp is an offset from this constant.  Nothing reads the
# wall clock, so the plan is reproducible next month as well as next minute.
DATA_EPOCH = datetime(2026, 1, 5, 9, 0, 0, tzinfo=timezone.utc)

# UUIDv5 namespace for every id this script controls.  Fixed forever: change
# it and re-running stops being idempotent.
ID_NAMESPACE = uuid.UUID("6f5f6d2a-9f1a-5d3e-9c2b-1c0a7d4e5b60")

_COMPANY_WORDS: tuple[str, ...] = (
    "Northwind", "Cedarline", "Harbourgate", "Brightfold", "Ironvale",
    "Silverpath", "Redstone", "Blueharbor", "Greenmoor", "Oakridge",
)
_COMPANY_SUFFIXES: tuple[str, ...] = (
    "Logistics", "Dental", "Roofing", "Insurance", "Motors",
    "Clinic", "Solar", "Legal", "Fitness", "Realty",
)
_FIRST_NAMES: tuple[str, ...] = (
    "Ada", "Bram", "Cleo", "Dev", "Esme", "Finn", "Gita", "Hugo",
    "Ines", "Jae", "Kira", "Luca",
)
_LAST_NAMES: tuple[str, ...] = (
    "Abara", "Bell", "Costa", "Duval", "Ekwueme", "Fraser", "Gomez",
    "Haq", "Ibarra", "Jonsson", "Kaur", "Lindqvist",
)
# NANP fictional range: (XXX) 555-0100..555-0199 is reserved for fiction and
# is not assignable, so no synthetic lead can map to a real phone.
_AREA_CODES: tuple[str, ...] = (
    "201", "202", "203", "205", "206", "207", "208", "209",
    "212", "213", "214", "215", "216", "217", "218", "219",
)


# ---------------------------------------------------------------------------
# Production guard (pure)
# ---------------------------------------------------------------------------

_PRODUCTION_ENV_VARS = ("ENVIRONMENT", "APP_ENV", "ENV", "TALKY_ENV")
_PRODUCTION_ENV_VALUES = {"production", "prod", "live"}
_PRODUCTION_NAME_TOKENS = {"prod", "production", "live"}
_LOCAL_HOSTS = {
    "", "localhost", "127.0.0.1", "::1", "0.0.0.0",
    "host.docker.internal", "postgres", "db", "database", "timescaledb",
}
# The Hetzner production backend.  Recorded so a copy-pasted prod DSN is
# refused even if ENVIRONMENT is not set in the operator's shell.
_KNOWN_PRODUCTION_HOSTS = {"144.76.17.150"}


class ProductionGuardError(RuntimeError):
    """The seeder refuses to touch this database."""


@dataclass(frozen=True)
class TargetClassification:
    kind: str            # "production" | "remote" | "local" | "unset"
    host: str
    database: str
    reasons: tuple[str, ...] = ()


def _tokens(value: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (value or "").lower()) if t}


def split_dsn(dsn: Optional[str]) -> tuple[str, str]:
    """Host and database name out of a Postgres URL, without a URL parser
    choking on the exotic characters that turn up in real passwords."""
    if not dsn:
        return "", ""
    remainder = dsn.split("://", 1)[-1]
    remainder = remainder.split("?", 1)[0]
    if "@" in remainder:
        remainder = remainder.rsplit("@", 1)[1]
    hostport, _, database = remainder.partition("/")
    host = hostport
    if host.startswith("["):                       # bracketed IPv6
        host = host[1:].split("]", 1)[0]
    elif ":" in host:
        host = host.rsplit(":", 1)[0]
    return host.lower(), database.lower()


def classify_database_target(
    dsn: Optional[str], env: Optional[dict] = None
) -> TargetClassification:
    """Decide whether this DSN is production, a remote database, or local."""
    env = os.environ if env is None else env
    host, database = split_dsn(dsn)
    reasons: list[str] = []

    for name in _PRODUCTION_ENV_VARS:
        value = (env.get(name) or "").strip().lower()
        if value in _PRODUCTION_ENV_VALUES and value:
            reasons.append(f"{name}={value}")
    if host in _KNOWN_PRODUCTION_HOSTS:
        reasons.append(f"host {host} is a known production host")
    if _tokens(host) & _PRODUCTION_NAME_TOKENS:
        reasons.append(f"host {host!r} contains a production marker")
    if _tokens(database) & _PRODUCTION_NAME_TOKENS:
        reasons.append(f"database {database!r} contains a production marker")

    if reasons:
        return TargetClassification("production", host, database, tuple(reasons))
    if not dsn:
        return TargetClassification("unset", host, database, ("no DATABASE_URL",))
    if host in _LOCAL_HOSTS:
        return TargetClassification("local", host, database, (f"host {host or '(none)'}",))
    return TargetClassification(
        "remote", host, database, (f"host {host} is not local",)
    )


def assert_target_allowed(
    target: TargetClassification, *, allow_remote: bool, needs_database: bool
) -> None:
    """Raise ProductionGuardError unless this database may be seeded."""
    if target.kind == "production":
        raise ProductionGuardError(
            "REFUSING TO SEED: this looks like production ("
            + "; ".join(target.reasons)
            + ").\nThere is no override flag for this check. If this really is a "
            "scratch copy, point DATABASE_URL at a database whose name does not "
            "say 'prod'/'live' and unset ENVIRONMENT/APP_ENV."
        )
    if target.kind == "unset":
        if needs_database:
            raise ProductionGuardError(
                "DATABASE_URL is required for --apply. "
                "Try: set -a && . ./.env.local && set +a"
            )
        return
    if target.kind == "remote" and not allow_remote:
        raise ProductionGuardError(
            f"REFUSING TO SEED: {target.host or '(unknown host)'} is not a local "
            "database and this script fills it with 50 synthetic tenants.\n"
            "Re-run with --allow-remote-database if this staging/scratch database "
            "is meant to receive synthetic data."
        )


# ---------------------------------------------------------------------------
# Migration 0035 preflight (pure)
# ---------------------------------------------------------------------------

def roles_missing_from_check(
    constraint_definition: Optional[str], roles: Sequence[str] = SEEDED_ROLES
) -> list[str]:
    """Role names the ``user_profiles.role`` CHECK would reject.

    ``platform_admin`` and friends appear in the definition as quoted literals
    (``CHECK ((role)::text = ANY (ARRAY['platform_admin'::character varying, ...``),
    so a literal-substring test is both sufficient and robust to the exact
    rendering PostgreSQL chooses.  A missing constraint (``None``) means the
    column is unconstrained and nothing is missing.
    """
    if constraint_definition is None:
        return []
    return [role for role in roles if f"'{role}'" not in constraint_definition]


# ---------------------------------------------------------------------------
# Deterministic derivation helpers (pure)
# ---------------------------------------------------------------------------

def stable_int(*parts: str) -> int:
    """A stable integer from strings. blake2b, never Python's salted hash()."""
    digest = hashlib.blake2b("\x1f".join(parts).encode("utf-8"), digest_size=8)
    return int.from_bytes(digest.digest(), "big")


def derived_id(seed: str, *parts: str) -> str:
    """A deterministic UUIDv5 for an id this script controls."""
    return str(uuid.uuid5(ID_NAMESPACE, "\x1f".join((seed,) + parts)))


def _cycle(values: Sequence[str], index: int) -> str:
    return values[index % len(values)]


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# The plan (pure) -- unit-tested in tests/unit/test_seed_validation_tenants.py
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SeedParameters:
    seed: str = DEFAULT_SEED
    tenants: int = DEFAULT_TENANT_COUNT
    campaigns_per_tenant: int = 2
    leads_per_campaign: int = 5
    calls_per_campaign: int = 3
    partner_every: int = 5          # every Nth tenant also gets a partner_admin
    plans: tuple[str, ...] = DEFAULT_PLANS
    name_prefix: str = DEFAULT_NAME_PREFIX
    email_domain: str = DEFAULT_EMAIL_DOMAIN
    password: str = ""              # resolved in __post_init__

    def __post_init__(self) -> None:
        if self.tenants < 1:
            raise ValueError("--tenants must be >= 1")
        if self.campaigns_per_tenant < 1:
            raise ValueError("--campaigns-per-tenant must be >= 1")
        if not self.plans:
            raise ValueError("--plans must name at least one plan")
        if not self.password:
            # One shared synthetic password: hashing 300 distinct passwords
            # with Argon2id would dominate the run, and the manifest publishes
            # it anyway so the isolation matrix can sign in as every user.
            object.__setattr__(
                self, "password", f"Validation!{stable_int(self.seed) % 10**8:08d}"
            )


@dataclass(frozen=True)
class UserPlan:
    email: str
    name: str
    role: str
    is_active: bool


@dataclass(frozen=True)
class LeadPlan:
    key: str
    phone_number: str
    first_name: str
    last_name: str
    email: str
    priority: int
    status: str


@dataclass(frozen=True)
class CallPlan:
    key: str
    lead_key: str
    phone_number: str
    status: str
    outcome: str
    duration_seconds: int
    started_at: str
    ended_at: str


@dataclass(frozen=True)
class CampaignPlan:
    key: str
    name: str
    status: str
    leads: tuple[LeadPlan, ...]
    calls: tuple[CallPlan, ...]


@dataclass(frozen=True)
class TenantPlan:
    index: int
    slug: str
    business_name: str
    plan_id: str
    minutes_allocated: int
    max_concurrent_calls: int
    subscription_status: str
    users: tuple[UserPlan, ...]
    campaigns: tuple[CampaignPlan, ...]


@dataclass(frozen=True)
class Population:
    parameters: SeedParameters
    tenants: tuple[TenantPlan, ...]
    master_admin: UserPlan


def _tenant_slug(index: int) -> str:
    return f"tv-{index:04d}"


def plan_tenant(params: SeedParameters, index: int) -> TenantPlan:
    """Everything about tenant ``index``, derived from the seed and nothing else."""
    slug = _tenant_slug(index)
    rng = random.Random(stable_int(params.seed, slug))

    word = _cycle(_COMPANY_WORDS, index)
    suffix = _cycle(_COMPANY_SUFFIXES, index // len(_COMPANY_WORDS) + index)
    business_name = f"{params.name_prefix} {index:04d} {word} {suffix}"

    plan_id = _cycle(params.plans, index)
    base_minutes, base_concurrency = _PLAN_BASELINES.get(plan_id, _PLAN_FALLBACK)
    # §12 wants different balances and quotas, not one value per plan.
    minutes_allocated = base_minutes + (index % 5) * 50
    max_concurrent_calls = base_concurrency + (index % 3)
    subscription_status = _cycle(SUBSCRIPTION_STATUS_CYCLE, index)

    roles = list(TENANT_ROLE_MIX)
    if params.partner_every > 0 and index % params.partner_every == 0:
        roles.append(PARTNER_ROLE)

    users = tuple(
        UserPlan(
            email=f"{role}.{slug}@{params.email_domain}",
            name=f"{role.replace('_', ' ').title()} {index:04d}",
            role=role,
            # §12: "Confirm disabled or suspended users cannot create new
            # sessions" needs disabled users to exist. Deterministic subset.
            is_active=not (role == "agent" and index % 7 == 6),
        )
        for role in roles
    )

    area_code = _cycle(_AREA_CODES, index)
    campaigns: list[CampaignPlan] = []
    for c in range(params.campaigns_per_tenant):
        campaign_key = f"{slug}-c{c + 1}"
        leads: list[LeadPlan] = []
        for lead_index in range(params.leads_per_campaign):
            # Unique per TENANT (not just per campaign) so duplicate-detection
            # tests see a clean population.
            global_lead_index = c * params.leads_per_campaign + lead_index
            leads.append(
                LeadPlan(
                    key=f"{campaign_key}-l{lead_index + 1}",
                    phone_number=f"+1{area_code}555{100 + global_lead_index:04d}",
                    first_name=_cycle(_FIRST_NAMES, index + global_lead_index),
                    last_name=_cycle(_LAST_NAMES, index * 3 + global_lead_index),
                    email=(
                        f"lead{global_lead_index + 1}.{slug}@{params.email_domain}"
                    ),
                    priority=rng.randint(1, 10),
                    status=_cycle(LEAD_STATUS_CYCLE, global_lead_index),
                )
            )

        calls: list[CallPlan] = []
        for call_index in range(params.calls_per_campaign):
            lead = leads[call_index % len(leads)] if leads else None
            if lead is None:
                break
            duration = rng.randint(20, 240)
            started = DATA_EPOCH + timedelta(
                days=index % 21, hours=c, minutes=17 * call_index
            )
            calls.append(
                CallPlan(
                    key=f"{campaign_key}-call{call_index + 1}",
                    lead_key=lead.key,
                    phone_number=lead.phone_number,
                    status=_cycle(CALL_STATUS_CYCLE, call_index + index),
                    outcome=_cycle(CALL_OUTCOME_CYCLE, call_index + c),
                    duration_seconds=duration,
                    started_at=_iso(started),
                    ended_at=_iso(started + timedelta(seconds=duration)),
                )
            )

        campaigns.append(
            CampaignPlan(
                key=campaign_key,
                name=f"{business_name} Campaign {c + 1}",
                status=_cycle(CAMPAIGN_STATUS_CYCLE, index + c),
                leads=tuple(leads),
                calls=tuple(calls),
            )
        )

    return TenantPlan(
        index=index,
        slug=slug,
        business_name=business_name,
        plan_id=plan_id,
        minutes_allocated=minutes_allocated,
        max_concurrent_calls=max_concurrent_calls,
        subscription_status=subscription_status,
        users=users,
        campaigns=tuple(campaigns),
    )


def plan_population(params: SeedParameters) -> Population:
    """The whole synthetic population. Pure: no clock, no network, no database."""
    return Population(
        parameters=params,
        tenants=tuple(plan_tenant(params, i) for i in range(params.tenants)),
        master_admin=UserPlan(
            email=f"master.admin@{params.email_domain}",
            name="Validation Master Admin",
            role=MASTER_ADMIN_ROLE,
            is_active=True,
        ),
    )


def role_counts(population: Population) -> dict[str, int]:
    counts: dict[str, int] = {role: 0 for role in SEEDED_ROLES}
    for tenant in population.tenants:
        for user in tenant.users:
            counts[user.role] = counts.get(user.role, 0) + 1
    counts[population.master_admin.role] = (
        counts.get(population.master_admin.role, 0) + 1
    )
    return counts


def plan_digest(population: Population) -> str:
    """A stable fingerprint of the plan -- §12's "synthetic tenant seed/manifest
    version".  Resolved database ids are deliberately NOT part of it: the plan
    is what must be reproducible, the ids are what the target assigns."""
    payload = {
        "manifest_version": MANIFEST_VERSION,
        "parameters": asdict(population.parameters),
        "master_admin": asdict(population.master_admin),
        "tenants": [asdict(t) for t in population.tenants],
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Statements (pure)
# ---------------------------------------------------------------------------

# Columns cast in SQL rather than converted in Python, so every value in a
# PlannedInsert stays a JSON-native scalar and the manifest can hold it as-is.
_UUID_COLUMNS = frozenset({"id", "tenant_id", "campaign_id", "lead_id"})
_TIMESTAMP_COLUMNS = frozenset(
    {"created_at", "updated_at", "started_at", "ended_at", "answered_at",
     "last_called_at", "completed_at"}
)


class TenantScopeError(ValueError):
    """A planned INSERT without a correct, explicit tenant_id."""


@dataclass(frozen=True)
class PlannedInsert:
    """One row this seeder intends to write.

    ``tenant_id`` is checked here rather than at execution time: the prod app
    role is superuser+BYPASSRLS, so a row written under the wrong tenant would
    be accepted by the database and would silently corrupt the isolation
    matrix.  The only row allowed to carry no tenant is the platform admin,
    which must say so explicitly.
    """

    table: str
    tenant_id: Optional[str]
    values: dict
    platform_scoped: bool = False
    entity: str = ""

    def __post_init__(self) -> None:
        if self.platform_scoped:
            if self.values.get("tenant_id") is not None:
                raise TenantScopeError(
                    f"{self.table}: platform-scoped row must not carry a tenant_id"
                )
            return
        if not self.tenant_id:
            raise TenantScopeError(f"{self.table}: no tenant_id on a tenant-scoped row")
        if self.values.get("tenant_id") != self.tenant_id:
            raise TenantScopeError(
                f"{self.table}: tenant_id column "
                f"{self.values.get('tenant_id')!r} != owning tenant {self.tenant_id!r}"
            )


def insert_statement(table: str, columns: Sequence[str]) -> str:
    """``INSERT ... ON CONFLICT DO NOTHING`` with per-column casts.

    No ``ON CONFLICT`` *target*: the statement must be skipped on a conflict
    with any unique index, including the partial
    ``idx_leads_campaign_phone_unique`` whose predicate an inferred target
    would have to repeat verbatim.
    """
    placeholders = []
    for position, column in enumerate(columns, start=1):
        if column in _UUID_COLUMNS:
            placeholders.append(f"${position}::uuid")
        elif column in _TIMESTAMP_COLUMNS:
            placeholders.append(f"${position}::timestamptz")
        else:
            placeholders.append(f"${position}")
    return (
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({', '.join(placeholders)}) ON CONFLICT DO NOTHING"
    )


def master_admin_insert(population: Population, password_hash: str) -> PlannedInsert:
    params = population.parameters
    admin = population.master_admin
    return PlannedInsert(
        table="user_profiles",
        tenant_id=None,
        platform_scoped=True,
        entity="master_admin",
        values={
            "id": derived_id(params.seed, "master_admin", admin.email),
            "tenant_id": None,
            "email": admin.email,
            "name": admin.name,
            "role": admin.role,
            "password_hash": password_hash,
            "is_active": True,
            "created_at": _iso(DATA_EPOCH),
            "updated_at": _iso(DATA_EPOCH),
        },
    )


def inserts_for_tenant(
    params: SeedParameters,
    tenant: TenantPlan,
    tenant_id: str,
    password_hash: str,
) -> list[PlannedInsert]:
    """Every row for one tenant, in foreign-key order.

    ``tenant_id`` comes from ``POST /admin/tenants`` (or from a previous
    manifest); every child id is a UUIDv5 derived from it, so the ids are
    stable across re-runs exactly as long as the tenant itself is.
    """
    if not tenant_id:
        raise TenantScopeError(f"{tenant.slug}: no resolved tenant_id")
    seed = params.seed
    created = _iso(DATA_EPOCH)
    out: list[PlannedInsert] = []

    for user in tenant.users:
        out.append(
            PlannedInsert(
                table="user_profiles",
                tenant_id=tenant_id,
                entity="user",
                values={
                    "id": derived_id(seed, tenant_id, "user", user.role),
                    "tenant_id": tenant_id,
                    "email": user.email,
                    "name": user.name,
                    "role": user.role,
                    "password_hash": password_hash,
                    "is_active": user.is_active,
                    "created_at": created,
                    "updated_at": created,
                },
            )
        )

    for campaign in tenant.campaigns:
        campaign_id = derived_id(seed, tenant_id, "campaign", campaign.key)
        out.append(
            PlannedInsert(
                table="campaigns",
                tenant_id=tenant_id,
                entity="campaign",
                values={
                    "id": campaign_id,
                    "tenant_id": tenant_id,
                    "name": campaign.name,
                    "description": (
                        "Synthetic goals.md section 12 validation campaign "
                        f"({campaign.key})"
                    ),
                    "status": campaign.status,
                    "system_prompt": "Synthetic validation campaign. Never dialled.",
                    "voice_id": "default",
                    "max_concurrent_calls": tenant.max_concurrent_calls,
                    "total_leads": len(campaign.leads),
                    "created_at": created,
                    "updated_at": created,
                },
            )
        )

        lead_ids: dict[str, str] = {}
        for lead in campaign.leads:
            lead_id = derived_id(seed, tenant_id, "lead", lead.key)
            lead_ids[lead.key] = lead_id
            out.append(
                PlannedInsert(
                    table="leads",
                    tenant_id=tenant_id,
                    entity="lead",
                    values={
                        "id": lead_id,
                        "tenant_id": tenant_id,
                        "campaign_id": campaign_id,
                        "phone_number": lead.phone_number,
                        "first_name": lead.first_name,
                        "last_name": lead.last_name,
                        "email": lead.email,
                        "priority": lead.priority,
                        "status": lead.status,
                        "created_at": created,
                        "updated_at": created,
                    },
                )
            )

        for call in campaign.calls:
            out.append(
                PlannedInsert(
                    table="calls",
                    tenant_id=tenant_id,
                    entity="call",
                    values={
                        "id": derived_id(seed, tenant_id, "call", call.key),
                        "tenant_id": tenant_id,
                        "campaign_id": campaign_id,
                        "lead_id": lead_ids.get(call.lead_key),
                        "phone_number": call.phone_number,
                        "status": call.status,
                        "outcome": call.outcome,
                        "duration_seconds": call.duration_seconds,
                        "started_at": call.started_at,
                        "ended_at": call.ended_at,
                        "created_at": created,
                        "updated_at": created,
                    },
                )
            )

    return out


# ---------------------------------------------------------------------------
# Manifest (pure)
# ---------------------------------------------------------------------------

@dataclass
class TenantResult:
    """What actually happened to one tenant. Filled in by the apply path."""

    plan: TenantPlan
    tenant_id: Optional[str] = None
    created: Optional[bool] = None
    rows_created: dict = field(default_factory=dict)
    rows_existing: dict = field(default_factory=dict)
    error: Optional[str] = None


def _tenant_manifest_entry(params: SeedParameters, result: TenantResult) -> dict:
    tenant = result.plan
    tenant_id = result.tenant_id
    entry = {
        "index": tenant.index,
        "slug": tenant.slug,
        # ``id`` and ``name`` are aliases scripts/isolation_matrix.py reads
        # first (it accepts either spelling); kept so the two do not have to
        # agree on one name.
        "id": tenant_id,
        "tenant_id": tenant_id,
        "name": tenant.business_name,
        "business_name": tenant.business_name,
        # Provenance. isolation_matrix.py refuses --allow-mutations unless
        # every tenant is demonstrably synthetic, and a mutating probe against
        # a real tenant would be the incident this whole exercise exists to
        # prevent.
        "seeded_by": "seed_validation_tenants.py",
        "synthetic": True,
        "created": result.created,
        "plan_id": tenant.plan_id,
        "minutes_allocated": tenant.minutes_allocated,
        "max_concurrent_calls": tenant.max_concurrent_calls,
        "subscription_status": tenant.subscription_status,
        "users": [
            {
                "user_id": (
                    derived_id(params.seed, tenant_id, "user", user.role)
                    if tenant_id
                    else None
                ),
                "email": user.email,
                "name": user.name,
                "role": user.role,
                "is_active": user.is_active,
                # Repeated per user, not only under "credentials": a consumer
                # that drops users with no password would silently build a
                # matrix with nothing to authenticate as, and a cross-tenant
                # probe that cannot log in passes vacuously.
                "password": params.password,
            }
            for user in tenant.users
        ],
        "campaigns": [
            {
                "key": campaign.key,
                "campaign_id": (
                    derived_id(params.seed, tenant_id, "campaign", campaign.key)
                    if tenant_id
                    else None
                ),
                "name": campaign.name,
                "status": campaign.status,
                "leads": [
                    {
                        "key": lead.key,
                        "lead_id": (
                            derived_id(params.seed, tenant_id, "lead", lead.key)
                            if tenant_id
                            else None
                        ),
                        "phone_number": lead.phone_number,
                        "email": lead.email,
                        "status": lead.status,
                    }
                    for lead in campaign.leads
                ],
                "calls": [
                    {
                        "key": call.key,
                        "call_id": (
                            derived_id(params.seed, tenant_id, "call", call.key)
                            if tenant_id
                            else None
                        ),
                        "lead_key": call.lead_key,
                        "status": call.status,
                        "outcome": call.outcome,
                        "duration_seconds": call.duration_seconds,
                    }
                    for call in campaign.calls
                ],
            }
            for campaign in tenant.campaigns
        ],
        # Flat id lists per resource class, keyed the way
        # scripts/isolation_matrix.py names them. Every id here is OWNED BY
        # THIS TENANT and is what another tenant must be refused. Classes this
        # seeder does not create (recordings, invoices, api_keys, connectors,
        # audit_events) are deliberately absent rather than empty-but-present,
        # so the matrix reports them SKIPPED instead of silently passing.
        "resources": {
            "campaigns": [
                derived_id(params.seed, tenant_id, "campaign", campaign.key)
                for campaign in tenant.campaigns
            ] if tenant_id else [],
            "leads": [
                derived_id(params.seed, tenant_id, "lead", lead.key)
                for campaign in tenant.campaigns
                for lead in campaign.leads
            ] if tenant_id else [],
            "calls": [
                derived_id(params.seed, tenant_id, "call", call.key)
                for campaign in tenant.campaigns
                for call in campaign.calls
            ] if tenant_id else [],
        },
        "counts": {
            "users": len(tenant.users),
            "campaigns": len(tenant.campaigns),
            "leads": sum(len(c.leads) for c in tenant.campaigns),
            "calls": sum(len(c.calls) for c in tenant.campaigns),
        },
        "rows_created": result.rows_created,
        "rows_existing": result.rows_existing,
        "error": result.error,
    }
    return entry


def build_manifest(
    population: Population,
    results: Sequence[TenantResult],
    *,
    applied: bool,
    target: TargetClassification,
    api_base_url: Optional[str],
    generated_at: Optional[str] = None,
    master_admin_id: Optional[str] = None,
) -> dict:
    """The artefact §12's evidence pack requires.

    Shape (manifest_version 1) -- designed for the isolation matrix, which
    needs to pick any two tenants, sign in as a named role in each, and hold a
    resource id it may NOT reach:

        manifest_version   int
        generator          str            this script's path (provenance)
        run_id             str            seed + plan digest, stable per plan
        applied            bool           false for a dry run
        generated_at       str|None       wall clock; excluded from plan_digest
        created_at         str|None       alias of generated_at
        environment        str            target kind: local/remote/unset
        plan_digest        str            "sha256:..." over the plan only
        seed               str
        parameters         object         every knob, echoed back
        target             object         {kind, host, database, api_base_url}
        credentials        object         {password, note}  synthetic, shared
        role_mix           [str]          the §12 roles, in §12's order
        totals             object         tenants/users/campaigns/leads/calls
                                          plus rows_created / rows_existing
        role_counts        object         role -> user count
        master_admin       object         {email, role, user_id, tenant_id:null}
        tenants            [object]       one per tenant:
          index            int
          slug             str            stable join key across runs
          id/tenant_id     str|None       both spellings; null in a dry run
          name/business_name str          both spellings
          seeded_by        str            "seed_validation_tenants.py"
          synthetic        bool           always true
          created          bool|None      false = the endpoint replayed
          plan_id, minutes_allocated, max_concurrent_calls,
          subscription_status
          users            [object]       {user_id, email, name, role,
                                           is_active, password}
          campaigns        [object]       {key, campaign_id, name, status,
                                           leads[], calls[]}
          resources        object         FLAT id lists per class, the shape
                                          scripts/isolation_matrix.py probes:
                                          {campaigns[], leads[], calls[]}
          counts           object         users/campaigns/leads/calls
          rows_created / rows_existing / error

    The matrix picks two tenants, signs in as a named role in one, and asks
    for an id out of the OTHER's ``resources``; every such request must be
    refused.  So ``resources`` holds only ids this tenant owns, and a class
    this seeder does not create is absent rather than empty -- absent reports
    as SKIPPED, empty could read as "nothing leaked".

    Every id is null in a dry run (nothing has been assigned yet) and a real
    UUID after --apply.  ``slug`` is the stable join key between the two.
    """
    params = population.parameters
    tenants = [_tenant_manifest_entry(params, r) for r in results]
    totals = {
        "tenants": len(tenants),
        "tenants_created": sum(1 for r in results if r.created is True),
        "tenants_existing": sum(1 for r in results if r.created is False),
        "users": sum(t["counts"]["users"] for t in tenants) + 1,  # + master admin
        "campaigns": sum(t["counts"]["campaigns"] for t in tenants),
        "leads": sum(t["counts"]["leads"] for t in tenants),
        "calls": sum(t["counts"]["calls"] for t in tenants),
        "rows_created": _sum_counters(r.rows_created for r in results),
        "rows_existing": _sum_counters(r.rows_existing for r in results),
        "errors": sum(1 for r in results if r.error),
    }
    digest = plan_digest(population)
    return {
        "manifest_version": MANIFEST_VERSION,
        "generator": "backend/scripts/seed_validation_tenants.py",
        "run_id": f"{params.seed}:{digest.split(':', 1)[1][:16]}",
        "applied": applied,
        "generated_at": generated_at,
        # Aliases scripts/isolation_matrix.py reads for its report header.
        "created_at": generated_at,
        "environment": target.kind,
        "plan_digest": digest,
        "seed": params.seed,
        "parameters": parameters_dict(params),
        "target": {
            "kind": target.kind,
            "host": target.host,
            "database": target.database,
            "api_base_url": api_base_url,
        },
        "credentials": {
            "password": params.password,
            "note": (
                "Shared synthetic password for every seeded user. Synthetic "
                "data only -- these accounts exist on validation databases."
            ),
        },
        "role_mix": list(TENANT_ROLE_MIX) + [PARTNER_ROLE],
        "totals": totals,
        "role_counts": role_counts(population),
        "master_admin": {
            "email": population.master_admin.email,
            "role": population.master_admin.role,
            "user_id": master_admin_id,
            "tenant_id": None,
        },
        "tenants": tenants,
    }


def parameters_dict(params: SeedParameters) -> dict:
    """``asdict`` with tuples flattened to lists.

    A manifest must survive ``json.loads(json.dumps(m)) == m``: the isolation
    matrix reloads it, and a field that comes back as a list where it went out
    as a tuple would make any equality or digest check on a reloaded manifest
    fail for no real reason.
    """
    out = asdict(params)
    out["plans"] = list(params.plans)
    return out


def _sum_counters(counters: Iterable[dict]) -> dict:
    out: dict[str, int] = {}
    for counter in counters:
        for key, value in counter.items():
            out[key] = out.get(key, 0) + value
    return out


def write_manifest(manifest: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(manifest, indent=2, sort_keys=False) + "\n", encoding="utf-8"
    )
    return path


# ---------------------------------------------------------------------------
# The database / API half
# ---------------------------------------------------------------------------

def dsn() -> Optional[str]:
    """DATABASE_URL first so this can be pointed at a scratch database; the
    app settings are the fallback for running on a server."""
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        try:
            sys.path.insert(0, os.getcwd())
            from app.core.config import get_settings

            raw = get_settings().database_url
        except Exception:  # noqa: BLE001 - absence is handled by the caller
            raw = None
    if not raw:
        return None
    return raw.replace("postgresql+asyncpg", "postgresql")


async def _preflight(conn: Any, params: SeedParameters) -> None:
    """Fail with ONE actionable line instead of N constraint violations."""
    definition = await conn.fetchval(
        """
        SELECT pg_get_constraintdef(c.oid)
        FROM pg_constraint AS c
        JOIN pg_attribute AS a
          ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey)
        WHERE c.conrelid = 'public.user_profiles'::regclass
          AND c.contype = 'c'
          AND a.attname = 'role'
          AND array_length(c.conkey, 1) = 1
        LIMIT 1
        """
    )
    missing = roles_missing_from_check(definition)
    if missing:
        raise SystemExit(
            "PREFLIGHT FAILED: user_profiles.role still rejects "
            f"{missing}.\nMigration 0035_user_profiles_role_widen has not been "
            "applied to this database. Run it first:\n"
            "    backend/.venv/Scripts/python -m alembic upgrade "
            "0035_user_profiles_role_widen\n"
            f"Current constraint: {definition}"
        )

    known = {
        row["id"]
        for row in await conn.fetch("SELECT id FROM plans WHERE id = ANY($1::text[])",
                                    list(params.plans))
    }
    unknown = [plan for plan in params.plans if plan not in known]
    if unknown:
        raise SystemExit(
            f"PREFLIGHT FAILED: plans {unknown} do not exist in this database. "
            "Seed database/seed_data.sql, or pass --plans with ids that exist "
            f"(found: {sorted(known) or 'none'})."
        )


async def _create_tenant(client: Any, base_url: str, token: str, tenant: TenantPlan):
    """POST /api/v1/admin/tenants -- idempotent on business_name."""
    response = await client.post(
        f"{base_url.rstrip('/')}/api/v1/admin/tenants",
        json={
            "business_name": tenant.business_name,
            "plan_id": tenant.plan_id,
            "minutes_allocated": tenant.minutes_allocated,
            "max_concurrent_calls": tenant.max_concurrent_calls,
            "subscription_status": tenant.subscription_status,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(
            f"POST /admin/tenants -> {response.status_code}: {response.text[:300]}"
        )
    body = response.json()
    return str(body["id"]), bool(body.get("created", response.status_code == 201))


async def _execute(conn: Any, planned: PlannedInsert) -> bool:
    columns = list(planned.values.keys())
    statement = insert_statement(planned.table, columns)
    status = await conn.execute(statement, *[planned.values[c] for c in columns])
    # asyncpg returns 'INSERT 0 1' for a write and 'INSERT 0 0' when the
    # ON CONFLICT DO NOTHING suppressed it -- that is how a re-run is counted
    # as 'existing' rather than being claimed as newly created.
    return status.strip().endswith(" 1")


async def apply_population(
    population: Population,
    *,
    conn: Any,
    http_client: Any,
    api_base_url: str,
    admin_token: str,
    progress_every: int,
) -> tuple[list[TenantResult], Optional[str]]:
    params = population.parameters
    await _preflight(conn, params)

    from app.core.security.password import hash_password  # noqa: E402

    password_hash = hash_password(params.password)

    master = master_admin_insert(population, password_hash)
    async with conn.transaction():
        await _execute(conn, master)
    master_id = master.values["id"]
    print(f"master admin: {population.master_admin.email} ({master_id})")

    results: list[TenantResult] = []
    for position, tenant in enumerate(population.tenants, start=1):
        result = TenantResult(plan=tenant)
        try:
            tenant_id, created = await _create_tenant(
                http_client, api_base_url, admin_token, tenant
            )
            result.tenant_id = tenant_id
            result.created = created

            planned = inserts_for_tenant(params, tenant, tenant_id, password_hash)
            async with conn.transaction():
                for insert in planned:
                    wrote = await _execute(conn, insert)
                    bucket = result.rows_created if wrote else result.rows_existing
                    bucket[insert.entity] = bucket.get(insert.entity, 0) + 1
        except Exception as exc:  # noqa: BLE001 - one bad tenant must not abort
            result.error = f"{type(exc).__name__}: {exc}"
            print(f"  ! {tenant.slug} ({tenant.business_name}): {result.error}")
        results.append(result)

        if progress_every and position % progress_every == 0:
            print(f"  ... {position}/{len(population.tenants)} tenants processed")

    return results, master_id


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_plan_summary(population: Population, target: TargetClassification) -> None:
    params = population.parameters
    counts = role_counts(population)
    leads = sum(len(c.leads) for t in population.tenants for c in t.campaigns)
    calls = sum(len(c.calls) for t in population.tenants for c in t.campaigns)
    print("=== PLAN ===")
    print(f"  seed             {params.seed}")
    print(f"  plan digest      {plan_digest(population)}")
    print(f"  tenants          {len(population.tenants)}")
    print(f"  users            {sum(len(t.users) for t in population.tenants)} "
          f"(+1 master admin)")
    print(f"  campaigns        {sum(len(t.campaigns) for t in population.tenants)}")
    print(f"  leads            {leads}")
    print(f"  calls            {calls}")
    print(f"  plans            {', '.join(params.plans)}")
    print(f"  target           {target.kind} host={target.host or '(none)'} "
          f"db={target.database or '(none)'}")
    # ASCII only in printed output: a cp1252 Windows console raises
    # UnicodeEncodeError on the section sign.
    print("\n=== ROLE MIX (goals.md section 12) ===")
    for role in SEEDED_ROLES:
        print(f"  {role:<18} {counts.get(role, 0)}")
    print("\n=== SUBSCRIPTION STATES ===")
    states: dict[str, int] = {}
    for tenant in population.tenants:
        states[tenant.subscription_status] = (
            states.get(tenant.subscription_status, 0) + 1
        )
    for state in sorted(states):
        print(f"  {state:<18} {states[state]}")


def print_apply_summary(manifest: dict) -> None:
    totals = manifest["totals"]
    print("\n=== APPLIED ===")
    print(f"  tenants created  {totals['tenants_created']}")
    print(f"  tenants existing {totals['tenants_existing']}")
    for label, key in (("rows created ", "rows_created"), ("rows existing", "rows_existing")):
        detail = ", ".join(f"{k}={v}" for k, v in sorted(totals[key].items())) or "none"
        print(f"  {label}    {detail}")
    if totals["errors"]:
        print(f"  ERRORS           {totals['errors']} tenant(s) failed - see manifest")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _manifest_path(args) -> Path:
    path = Path(args.manifest)
    if not args.apply:
        path = path.with_name(path.stem + ".dry-run" + path.suffix)
    return path


async def run(args) -> int:
    params = SeedParameters(
        seed=args.seed,
        tenants=args.tenants,
        campaigns_per_tenant=args.campaigns_per_tenant,
        leads_per_campaign=args.leads_per_campaign,
        calls_per_campaign=args.calls_per_campaign,
        partner_every=args.partner_every,
        plans=tuple(p.strip() for p in args.plans.split(",") if p.strip()),
        name_prefix=args.name_prefix,
        email_domain=args.email_domain,
        password=args.password or "",
    )

    database_url = dsn()
    target = classify_database_target(database_url)
    try:
        assert_target_allowed(
            target, allow_remote=args.allow_remote_database, needs_database=args.apply
        )
    except ProductionGuardError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 2

    population = plan_population(params)
    print_plan_summary(population, target)

    path = _manifest_path(args)
    if not args.apply:
        manifest = build_manifest(
            population,
            [TenantResult(plan=t) for t in population.tenants],
            applied=False,
            target=target,
            api_base_url=args.api_base_url,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
        write_manifest(manifest, path)
        print("\nDRY RUN - nothing written to the database.")
        print(f"Planned manifest: {path}")
        print("Re-run with --apply (and --api-base-url/--admin-token) to write.")
        return 0

    token = args.admin_token or os.environ.get("TALKY_ADMIN_TOKEN") or ""
    if not token:
        print(
            "--admin-token (or TALKY_ADMIN_TOKEN) is required for --apply: tenants "
            "are created through POST /api/v1/admin/tenants, which is "
            "platform_admin only.",
            file=sys.stderr,
        )
        return 2

    import asyncpg
    import httpx

    conn = await asyncpg.connect(database_url, timeout=15)
    try:
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            results, master_id = await apply_population(
                population,
                conn=conn,
                http_client=http_client,
                api_base_url=args.api_base_url,
                admin_token=token,
                progress_every=args.progress_every,
            )
    finally:
        await conn.close()

    manifest = build_manifest(
        population,
        results,
        applied=True,
        target=target,
        api_base_url=args.api_base_url,
        generated_at=datetime.now(timezone.utc).isoformat(),
        master_admin_id=master_id,
    )
    write_manifest(manifest, path)
    print_apply_summary(manifest)
    print(f"\nManifest: {path}")
    return 1 if manifest["totals"]["errors"] else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Seed deterministic synthetic tenants for the goals.md section 12 "
            "validation run (dry run by default)."
        ),
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually write. Without this the script only plans and reports.",
    )
    parser.add_argument(
        "--tenants", type=int, default=DEFAULT_TENANT_COUNT,
        help=f"How many synthetic tenants (default {DEFAULT_TENANT_COUNT}).",
    )
    parser.add_argument(
        "--seed", default=DEFAULT_SEED,
        help="Determinism seed. Same seed + same knobs = same population.",
    )
    parser.add_argument("--campaigns-per-tenant", type=int, default=2)
    parser.add_argument("--leads-per-campaign", type=int, default=5)
    parser.add_argument("--calls-per-campaign", type=int, default=3)
    parser.add_argument(
        "--partner-every", type=int, default=5,
        help="Give every Nth tenant a partner_admin user (0 disables).",
    )
    parser.add_argument(
        "--plans", default=",".join(DEFAULT_PLANS),
        help="Comma-separated plan ids to cycle through.",
    )
    parser.add_argument("--name-prefix", default=DEFAULT_NAME_PREFIX)
    parser.add_argument("--email-domain", default=DEFAULT_EMAIL_DOMAIN)
    parser.add_argument(
        "--password", default=None,
        help="Shared password for every seeded user (default: derived from --seed).",
    )
    parser.add_argument(
        "--manifest", default="tmp/validation_seed/manifest.json",
        help="Where to write the manifest. A dry run writes <stem>.dry-run.json.",
    )
    parser.add_argument(
        "--api-base-url",
        default=os.environ.get("TALKY_API_BASE_URL", "http://localhost:8000"),
        help="Backend base URL for POST /api/v1/admin/tenants.",
    )
    parser.add_argument(
        "--admin-token", default=None,
        help="platform_admin bearer token (or set TALKY_ADMIN_TOKEN).",
    )
    parser.add_argument(
        "--allow-remote-database", action="store_true",
        help="Acknowledge that a non-local database may receive synthetic data. "
             "Never lifts the production refusal.",
    )
    parser.add_argument("--progress-every", type=int, default=10)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
