"""The pure half of ``scripts/seed_validation_tenants.py``.

The script needs a database and a running backend; the decisions it makes --
*which* tenants, *which* roles, *which* rows, and *whether this database may
be written to at all* -- do not, and those decisions are the whole risk.  A
population that cannot be reproduced is not evidence, a seeder that writes a
row under the wrong tenant silently invalidates the isolation matrix it feeds,
and a seeder that runs against production is an incident.  All three are
tested here.

``scripts/`` is not an importable package, so the module is loaded by path.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

# Load the dependency module first: rbac.py imports CurrentUser from it.
from app.api.v1 import dependencies as _dependencies  # noqa: F401
from app.core.security.rbac import ROLE_ALIASES, UserRole

_SCRIPT = (
    Path(__file__).resolve().parents[2] / "scripts" / "seed_validation_tenants.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("_seed_validation_tenants", _SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    # @dataclass resolves annotations through sys.modules[cls.__module__],
    # so the module has to be registered before it is executed.
    sys.modules["_seed_validation_tenants"] = module
    spec.loader.exec_module(module)
    return module


seeder = _load()


def _params(**overrides):
    base = dict(tenants=6, campaigns_per_tenant=2, leads_per_campaign=3,
                calls_per_campaign=2)
    base.update(overrides)
    return seeder.SeedParameters(**base)


# ---------------------------------------------------------------------------
# Determinism -- a validation run that cannot be reproduced is not evidence
# ---------------------------------------------------------------------------

def test_same_seed_produces_an_identical_plan_twice():
    first = seeder.plan_population(_params())
    second = seeder.plan_population(_params())
    assert first == second
    assert seeder.plan_digest(first) == seeder.plan_digest(second)


def test_plan_is_stable_across_a_fresh_module_load():
    """Guards against anything derived from PYTHONHASHSEED or the clock."""
    other = _load()
    assert seeder.plan_digest(seeder.plan_population(_params())) == other.plan_digest(
        other.plan_population(other.SeedParameters(**{
            "tenants": 6, "campaigns_per_tenant": 2,
            "leads_per_campaign": 3, "calls_per_campaign": 2,
        }))
    )


def test_a_different_seed_produces_a_different_plan():
    a = seeder.plan_population(_params(seed="seed-a"))
    b = seeder.plan_population(_params(seed="seed-b"))
    assert seeder.plan_digest(a) != seeder.plan_digest(b)


def test_no_seeded_timestamp_comes_from_the_clock():
    population = seeder.plan_population(_params())
    stamps = {
        call.started_at
        for tenant in population.tenants
        for campaign in tenant.campaigns
        for call in campaign.calls
    }
    assert stamps
    assert all(stamp.startswith("2026-01") for stamp in stamps)


def test_derived_ids_are_stable_and_distinct():
    assert seeder.derived_id("s", "a", "b") == seeder.derived_id("s", "a", "b")
    assert seeder.derived_id("s", "a", "b") != seeder.derived_id("s", "a", "c")
    assert seeder.derived_id("s", "a", "b") != seeder.derived_id("t", "a", "b")


def test_default_tenant_count_is_fifty():
    """The audit's scope decision: 50 with a real matrix, not 200 with none."""
    assert seeder.DEFAULT_TENANT_COUNT == 50
    assert seeder.SeedParameters().tenants == 50


# ---------------------------------------------------------------------------
# The role mix goals.md §12 names
# ---------------------------------------------------------------------------

def test_role_mix_is_exactly_what_goals_md_section_12_names():
    # §12 "Create additional role samples across the population":
    #   Tenant admin / Campaign manager / Agent-operator / Billing user /
    #   Read-only user / Partner-reseller user, where supported
    assert seeder.TENANT_ROLE_MIX == (
        "tenant_admin",
        "campaign_manager",
        "agent",
        "billing_user",
        "readonly",
    )
    assert seeder.PARTNER_ROLE == "partner_admin"
    # "Create a master-admin account that can manage all N tenants."
    assert seeder.MASTER_ADMIN_ROLE == "platform_admin"


def test_every_seeded_role_is_a_real_user_role():
    """A role name the enum does not know is a SILENT privilege downgrade:
    ``normalize_role`` resolves it to readonly and the tenant is then validated
    against the wrong permission set while appearing to pass."""
    known = {role.value for role in UserRole}
    assert set(seeder.SEEDED_ROLES) <= known


def test_agent_is_the_role_behind_the_operator_spelling():
    """§12 spells it "Agent/operator"; both spellings must land on one role."""
    assert ROLE_ALIASES["operator"] is UserRole.AGENT
    assert "agent" in seeder.TENANT_ROLE_MIX


def test_every_tenant_gets_an_admin_and_the_full_role_mix():
    population = seeder.plan_population(_params(tenants=12, partner_every=5))
    for tenant in population.tenants:
        roles = [user.role for user in tenant.users]
        assert roles[: len(seeder.TENANT_ROLE_MIX)] == list(seeder.TENANT_ROLE_MIX)
        assert "tenant_admin" in roles
        assert (seeder.PARTNER_ROLE in roles) == (tenant.index % 5 == 0)
    counts = seeder.role_counts(population)
    assert counts["platform_admin"] == 1
    for role in seeder.TENANT_ROLE_MIX:
        assert counts[role] == 12


def test_population_seeds_every_subscription_state_section_12_asks_for():
    population = seeder.plan_population(_params(tenants=24))
    states = {tenant.subscription_status for tenant in population.tenants}
    # active / trial / suspended / cancelled / overdue
    assert states == {"active", "trialing", "suspended", "cancelled", "past_due"}


def test_population_spreads_plans_and_quotas():
    population = seeder.plan_population(_params(tenants=12))
    assert len({t.plan_id for t in population.tenants}) == len(seeder.DEFAULT_PLANS)
    assert len({t.minutes_allocated for t in population.tenants}) > 1


def test_some_users_are_disabled_so_the_suspended_login_check_has_a_subject():
    population = seeder.plan_population(_params(tenants=14))
    assert any(
        not user.is_active for t in population.tenants for user in t.users
    )


def test_emails_are_unique_across_the_whole_population():
    population = seeder.plan_population(_params(tenants=20))
    emails = [u.email for t in population.tenants for u in t.users]
    emails.append(population.master_admin.email)
    assert len(emails) == len(set(emails))
    assert all(e.endswith(".invalid") for e in emails)


def test_lead_phone_numbers_are_unique_per_tenant_and_non_dialable():
    population = seeder.plan_population(_params(tenants=8))
    for tenant in population.tenants:
        numbers = [
            lead.phone_number for c in tenant.campaigns for lead in c.leads
        ]
        assert len(numbers) == len(set(numbers))
        # NANP fictional range (XXX) 555-0100..0199 -- unassignable.
        assert all(n[4:10].startswith("555") or "555" in n for n in numbers)
        assert all(n.startswith("+1") for n in numbers)


# ---------------------------------------------------------------------------
# Tenant scoping -- RLS will not save this seeder
# ---------------------------------------------------------------------------

def test_every_generated_insert_carries_its_owning_tenant_id():
    params = _params(tenants=4)
    population = seeder.plan_population(params)
    for offset, tenant in enumerate(population.tenants):
        tenant_id = seeder.derived_id("fake-tenants", str(offset))
        inserts = seeder.inserts_for_tenant(params, tenant, tenant_id, "hash")
        assert inserts
        for planned in inserts:
            assert planned.platform_scoped is False
            assert planned.tenant_id == tenant_id
            assert planned.values["tenant_id"] == tenant_id


def test_child_rows_only_reference_ids_belonging_to_the_same_tenant():
    params = _params(tenants=2)
    population = seeder.plan_population(params)
    tenant_id = seeder.derived_id("fake-tenants", "0")
    inserts = seeder.inserts_for_tenant(params, population.tenants[0], tenant_id, "h")
    campaign_ids = {i.values["id"] for i in inserts if i.table == "campaigns"}
    lead_ids = {i.values["id"] for i in inserts if i.table == "leads"}
    for planned in inserts:
        if planned.table in ("leads", "calls"):
            assert planned.values["campaign_id"] in campaign_ids
        if planned.table == "calls":
            assert planned.values["lead_id"] in lead_ids


def test_two_tenants_never_share_a_generated_row_id():
    params = _params(tenants=2)
    population = seeder.plan_population(params)
    ids = []
    for offset, tenant in enumerate(population.tenants):
        tenant_id = seeder.derived_id("fake-tenants", str(offset))
        ids += [
            i.values["id"]
            for i in seeder.inserts_for_tenant(params, tenant, tenant_id, "h")
        ]
    assert len(ids) == len(set(ids))


def test_a_tenant_scoped_insert_without_a_tenant_id_is_refused():
    with pytest.raises(seeder.TenantScopeError):
        seeder.PlannedInsert(table="leads", tenant_id=None, values={"id": "x"})


def test_a_mismatched_tenant_id_column_is_refused():
    with pytest.raises(seeder.TenantScopeError):
        seeder.PlannedInsert(
            table="leads", tenant_id="a", values={"id": "x", "tenant_id": "b"}
        )


def test_inserts_for_tenant_refuses_an_unresolved_tenant():
    params = _params(tenants=1)
    tenant = seeder.plan_population(params).tenants[0]
    with pytest.raises(seeder.TenantScopeError):
        seeder.inserts_for_tenant(params, tenant, "", "hash")


def test_only_the_master_admin_is_platform_scoped():
    population = seeder.plan_population(_params(tenants=2))
    planned = seeder.master_admin_insert(population, "hash")
    assert planned.platform_scoped is True
    assert planned.tenant_id is None
    assert planned.values["tenant_id"] is None
    assert planned.values["role"] == "platform_admin"


# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------

def test_insert_statement_casts_uuids_and_timestamps_and_never_names_an_arbiter():
    sql = seeder.insert_statement("leads", ["id", "tenant_id", "status", "created_at"])
    assert "$1::uuid" in sql and "$2::uuid" in sql
    assert "$3," in sql or "$3)" in sql          # plain text column, no cast
    assert "$4::timestamptz" in sql
    # A partial unique index cannot be inferred unless the predicate is
    # repeated verbatim, so the statement must carry no ON CONFLICT target.
    assert sql.endswith("ON CONFLICT DO NOTHING")
    assert "ON CONFLICT (" not in sql


def test_generated_values_are_json_native():
    params = _params(tenants=2)
    population = seeder.plan_population(params)
    tenant_id = seeder.derived_id("fake-tenants", "0")
    for planned in seeder.inserts_for_tenant(params, population.tenants[0],
                                             tenant_id, "hash"):
        json.dumps(planned.values)   # would raise on a datetime or UUID object


# ---------------------------------------------------------------------------
# Migration 0035 preflight
# ---------------------------------------------------------------------------

_NARROW = (
    "CHECK (((role)::text = ANY ((ARRAY['platform_admin'::character varying, "
    "'partner_admin'::character varying, 'tenant_admin'::character varying, "
    "'user'::character varying, 'readonly'::character varying])::text[])))"
)
_WIDE = _NARROW.replace(
    "'tenant_admin'::character varying,",
    "'tenant_admin'::character varying, 'campaign_manager'::character varying, "
    "'agent'::character varying, 'billing_user'::character varying,",
)


def test_preflight_names_the_roles_a_pre_0035_database_would_reject():
    missing = seeder.roles_missing_from_check(_NARROW)
    assert missing == ["campaign_manager", "agent", "billing_user"]


def test_preflight_passes_once_0035_is_applied():
    assert seeder.roles_missing_from_check(_WIDE) == []


def test_preflight_treats_a_missing_constraint_as_permissive():
    assert seeder.roles_missing_from_check(None) == []


# ---------------------------------------------------------------------------
# Production guard
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url",
    [
        "postgresql://u:p@db.prod.talky.ai:5432/talky",
        "postgresql://u:p@10.0.0.4:5432/talky_production",
        "postgresql://u:p@144.76.17.150:5432/talky",
        "postgresql://u:p@live-db.internal:5432/talky",
    ],
)
def test_production_looking_urls_are_classified_as_production(url):
    target = seeder.classify_database_target(url, env={})
    assert target.kind == "production"
    assert target.reasons


def test_production_environment_variable_alone_is_enough():
    target = seeder.classify_database_target(
        "postgresql://u:p@localhost:5432/talky", env={"ENVIRONMENT": "production"}
    )
    assert target.kind == "production"


def test_the_production_refusal_cannot_be_overridden():
    target = seeder.classify_database_target(
        "postgresql://u:p@db.prod.talky.ai:5432/talky", env={}
    )
    with pytest.raises(seeder.ProductionGuardError) as excinfo:
        seeder.assert_target_allowed(
            target, allow_remote=True, needs_database=True
        )
    assert "REFUSING TO SEED" in str(excinfo.value)


def test_a_local_database_needs_no_override():
    target = seeder.classify_database_target(
        "postgresql://u:p@localhost:5432/talky_validation", env={}
    )
    assert target.kind == "local"
    seeder.assert_target_allowed(target, allow_remote=False, needs_database=True)


def test_a_remote_database_needs_the_explicit_override_flag():
    target = seeder.classify_database_target(
        "postgresql://u:p@staging.example.net:5432/talky_staging", env={}
    )
    assert target.kind == "remote"
    with pytest.raises(seeder.ProductionGuardError):
        seeder.assert_target_allowed(target, allow_remote=False, needs_database=True)
    seeder.assert_target_allowed(target, allow_remote=True, needs_database=True)


def test_apply_without_a_database_url_is_refused_but_a_dry_run_is_not():
    target = seeder.classify_database_target(None, env={})
    assert target.kind == "unset"
    seeder.assert_target_allowed(target, allow_remote=False, needs_database=False)
    with pytest.raises(seeder.ProductionGuardError):
        seeder.assert_target_allowed(target, allow_remote=False, needs_database=True)


@pytest.mark.parametrize(
    "url,host,database",
    [
        ("postgresql://u:p@h:5432/db", "h", "db"),
        ("postgresql+asyncpg://u:p%40x@h.example:6543/db?sslmode=require",
         "h.example", "db"),
        ("postgresql://u:p@w@[::1]:5432/db", "::1", "db"),
    ],
)
def test_split_dsn_survives_real_passwords(url, host, database):
    assert seeder.split_dsn(url) == (host, database)


# ---------------------------------------------------------------------------
# Manifest -- the artefact the isolation matrix consumes
# ---------------------------------------------------------------------------

def _manifest(applied=False, tenants=4):
    params = _params(tenants=tenants)
    population = seeder.plan_population(params)
    results = []
    for offset, tenant in enumerate(population.tenants):
        result = seeder.TenantResult(plan=tenant)
        if applied:
            result.tenant_id = seeder.derived_id("fake-tenants", str(offset))
            result.created = True
            result.rows_created = {"user": len(tenant.users)}
        results.append(result)
    target = seeder.classify_database_target(
        "postgresql://u:p@localhost:5432/talky_validation", env={}
    )
    return population, seeder.build_manifest(
        population,
        results,
        applied=applied,
        target=target,
        api_base_url="http://localhost:8000",
        generated_at="2026-08-29T00:00:00+00:00",
        master_admin_id="00000000-0000-0000-0000-000000000001" if applied else None,
    )


def test_manifest_has_the_documented_shape():
    population, manifest = _manifest(applied=True)
    for key in (
        "manifest_version", "generator", "run_id", "applied", "generated_at",
        "plan_digest", "seed", "parameters", "target", "credentials",
        "role_mix", "totals", "role_counts", "master_admin", "tenants",
    ):
        assert key in manifest, key
    assert manifest["manifest_version"] == seeder.MANIFEST_VERSION
    assert manifest["plan_digest"] == seeder.plan_digest(population)
    assert manifest["role_mix"] == list(seeder.TENANT_ROLE_MIX) + ["partner_admin"]
    assert manifest["master_admin"]["tenant_id"] is None


def test_manifest_records_every_tenant_with_its_id_plan_roles_and_counts():
    _, manifest = _manifest(applied=True)
    assert len(manifest["tenants"]) == 4
    for entry in manifest["tenants"]:
        assert entry["tenant_id"]
        assert entry["slug"]
        assert entry["business_name"]
        assert entry["plan_id"]
        assert entry["subscription_status"]
        assert [u["role"] for u in entry["users"]][0] == "tenant_admin"
        assert all(u["user_id"] for u in entry["users"])
        assert entry["counts"]["campaigns"] == 2
        assert entry["counts"]["leads"] == 6
        assert entry["counts"]["calls"] == 4
        for campaign in entry["campaigns"]:
            assert campaign["campaign_id"]
            assert all(lead["lead_id"] for lead in campaign["leads"])
            assert all(call["call_id"] for call in campaign["calls"])


def test_manifest_ids_match_the_ids_the_seeder_would_insert():
    """The isolation matrix reads ids out of the manifest and then tries to
    reach them from the wrong tenant; if the manifest and the writer disagreed
    the matrix would be probing rows that do not exist and pass vacuously."""
    params = _params(tenants=2)
    population = seeder.plan_population(params)
    tenant_id = seeder.derived_id("fake-tenants", "0")
    result = seeder.TenantResult(plan=population.tenants[0], tenant_id=tenant_id,
                                 created=True)
    manifest = seeder.build_manifest(
        population, [result, seeder.TenantResult(plan=population.tenants[1])],
        applied=True,
        target=seeder.classify_database_target("postgresql://u@localhost/db", env={}),
        api_base_url=None,
    )
    written = {
        i.values["id"]
        for i in seeder.inserts_for_tenant(params, population.tenants[0],
                                           tenant_id, "hash")
    }
    entry = manifest["tenants"][0]
    recorded = {u["user_id"] for u in entry["users"]}
    for campaign in entry["campaigns"]:
        recorded.add(campaign["campaign_id"])
        recorded |= {lead["lead_id"] for lead in campaign["leads"]}
        recorded |= {call["call_id"] for call in campaign["calls"]}
    assert recorded == written
    # The flat "resources" lists must name the same rows as the nested view.
    flat = {i for key in entry["resources"] for i in entry["resources"][key]}
    assert flat <= written


def test_manifest_totals_add_up():
    _, manifest = _manifest(applied=True, tenants=6)
    totals = manifest["totals"]
    assert totals["tenants"] == 6
    assert totals["tenants_created"] == 6
    assert totals["users"] == sum(
        t["counts"]["users"] for t in manifest["tenants"]
    ) + 1
    assert totals["leads"] == 6 * 6
    assert totals["calls"] == 6 * 4
    assert totals["errors"] == 0


def test_a_dry_run_manifest_has_no_ids_but_the_same_plan_digest():
    population, dry = _manifest(applied=False)
    _, wet = _manifest(applied=True)
    assert dry["applied"] is False
    assert all(entry["tenant_id"] is None for entry in dry["tenants"])
    assert all(
        user["user_id"] is None
        for entry in dry["tenants"] for user in entry["users"]
    )
    assert dry["plan_digest"] == wet["plan_digest"] == seeder.plan_digest(population)


def test_manifest_satisfies_the_isolation_matrix_contract():
    """scripts/isolation_matrix.py is the consumer, and it drops anything it
    cannot use: a tenant with no ``id``, a user with no ``password``, a
    resource class with no ids.  Each of those failures is silent -- the run
    still "passes", having probed nothing -- so the contract is asserted here
    rather than discovered on the validation night."""
    _, manifest = _manifest(applied=True)
    assert "seed_validation_tenants" in manifest["generator"]
    for entry in manifest["tenants"]:
        assert entry["id"] == entry["tenant_id"]
        assert entry["name"] == entry["business_name"]
        assert entry["seeded_by"] == "seed_validation_tenants.py"
        assert entry["synthetic"] is True
        for user in entry["users"]:
            assert user["email"] and user["password"] and user["role"]
        resources = entry["resources"]
        assert set(resources) == {"campaigns", "leads", "calls"}
        assert all(resources[key] for key in resources)
        # Only ids this tenant owns -- the matrix asks another tenant for them
        # and every such request must be refused.
        assert len(resources["campaigns"]) == entry["counts"]["campaigns"]
        assert len(resources["leads"]) == entry["counts"]["leads"]
        assert len(resources["calls"]) == entry["counts"]["calls"]


def test_resource_ids_are_never_shared_between_tenants():
    _, manifest = _manifest(applied=True, tenants=6)
    seen: set[str] = set()
    for entry in manifest["tenants"]:
        ids = {i for key in entry["resources"] for i in entry["resources"][key]}
        assert not (ids & seen)
        seen |= ids


def test_manifest_round_trips_through_json():
    _, manifest = _manifest(applied=True)
    assert json.loads(json.dumps(manifest)) == manifest


def test_manifest_written_to_disk_round_trips(tmp_path):
    _, manifest = _manifest(applied=True)
    path = seeder.write_manifest(manifest, tmp_path / "nested" / "manifest.json")
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8")) == manifest


def test_run_id_and_digest_are_stable_but_the_timestamp_is_not_in_the_digest():
    _, first = _manifest(applied=True)
    population = seeder.plan_population(_params(tenants=4))
    later = seeder.build_manifest(
        population,
        [seeder.TenantResult(plan=t) for t in population.tenants],
        applied=False,
        target=seeder.classify_database_target(None, env={}),
        api_base_url=None,
        generated_at="2099-01-01T00:00:00+00:00",
    )
    assert later["plan_digest"] == first["plan_digest"]
    assert later["run_id"] == first["run_id"]


def test_manifest_publishes_the_shared_password_for_the_login_tests():
    _, manifest = _manifest(applied=True)
    assert manifest["credentials"]["password"]
    assert manifest["parameters"]["password"] == manifest["credentials"]["password"]


# ---------------------------------------------------------------------------
# CLI defaults
# ---------------------------------------------------------------------------

def test_dry_run_is_the_default_and_writes_a_separate_manifest_file():
    args = seeder.build_parser().parse_args([])
    assert args.apply is False
    assert args.tenants == 50
    assert args.allow_remote_database is False
    assert seeder._manifest_path(args).name.endswith(".dry-run.json")
    applied = seeder.build_parser().parse_args(["--apply"])
    assert seeder._manifest_path(applied).name == "manifest.json"


def test_parameters_reject_a_nonsense_population():
    with pytest.raises(ValueError):
        replace(seeder.SeedParameters(), tenants=0)
    with pytest.raises(ValueError):
        replace(seeder.SeedParameters(), plans=())
