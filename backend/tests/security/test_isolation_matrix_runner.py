"""Tests for the measuring instrument: ``scripts/isolation_matrix.py``.

No server exists in this environment, so the runner is built around an injected
client and everything below drives it with a fake API. That is the point — the
properties pinned here are the ones that would let a real isolation failure be
recorded as a pass:

  * every ordered pair is probed and no tenant is probed against itself
    (a self-pair always "passes" and would pad the denial count with nothing);
  * a leak is reported as a LEAK, with the resource named, and exits non-zero;
  * a run whose positive controls failed is INVALID — never PASS — because its
    404s prove nothing;
  * a run with nothing to probe is INVALID, not a clean sheet;
  * mutating probes never use themselves as their own control;
  * the production-URL guard refuses, and there is no flag to override it.

``scripts/`` is not an importable package, so the module is loaded by path.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "isolation_matrix.py"

if not _SCRIPT.exists():  # pragma: no cover — layout guard
    pytest.skip(f"runner not found at {_SCRIPT}", allow_module_level=True)

_spec = importlib.util.spec_from_file_location("isolation_matrix", _SCRIPT)
im = importlib.util.module_from_spec(_spec)
# Register BEFORE exec: @dataclass resolves annotations via
# sys.modules[cls.__module__].
sys.modules["isolation_matrix"] = im
assert _spec.loader is not None
_spec.loader.exec_module(im)


# ---------------------------------------------------------------------------
# Fixtures: a manifest and a fake API that behaves like a correct backend
# ---------------------------------------------------------------------------

T1 = "11111111-1111-1111-1111-111111111111"
T2 = "22222222-2222-2222-2222-222222222222"
T3 = "33333333-3333-3333-3333-333333333333"


def _tenant(tenant_id: str, n: int, *, seeded: bool = True) -> dict:
    return {
        "id": tenant_id,
        "name": f"Validation Tenant {n:03d}",
        "seeded_by": "seed_validation_tenants.py" if seeded else "",
        "users": [
            {
                "email": f"admin{n}@validation.test",
                "password": f"pw-{n}",
                "role": "tenant_admin",
                "user_id": f"user-{n}",
            }
        ],
        "resources": {
            "calls": [f"call-{n}"],
            "recordings": [f"rec-{n}"],
            "campaigns": [f"camp-{n}"],
            "invoices": [f"inv-{n}"],
            "api_keys": [f"key-{n}"],
            "audit_events": [f"audit-{n}"],
            "connectors": [f"conn-{n}"],
        },
    }


def manifest_dict(*tenant_ids: str, seeded: bool = True) -> dict:
    return {
        "manifest_version": 1,
        "generator": "seed_validation_tenants.py",
        "seed": 20260829,
        "created_at": "2026-08-29T00:00:00Z",
        "environment": "validation",
        "tenants": [
            _tenant(tid, i + 1, seeded=seeded) for i, tid in enumerate(tenant_ids)
        ],
    }


def make_manifest(*tenant_ids: str, seeded: bool = True):
    return im.parse_manifest(manifest_dict(*tenant_ids, seeded=seeded), path="fake.json")


class FakeAPI:
    """A backend that is correct by construction — until told otherwise.

    Ids are suffixed with the tenant's ordinal (``call-1`` belongs to tenant 1),
    so ownership is decidable from the URL alone.
    """

    def __init__(
        self,
        *,
        leaking_probes: set[str] | None = None,
        control_status: int = 200,
        deny_status: int = 404,
    ) -> None:
        self.leaking = leaking_probes or set()
        self.control_status = control_status
        self.deny_status = deny_status
        self.sent: list[tuple[str, str, str]] = []
        self.logins: list[str] = []

    async def login(self, email: str, password: str) -> str:
        self.logins.append(email)
        # admin3@validation.test -> token-3
        return "token-" + email.split("@")[0].replace("admin", "")

    @staticmethod
    def _owner(path: str) -> str | None:
        for part in path.split("/"):
            if "-" in part and part.rsplit("-", 1)[-1].isdigit():
                return part.rsplit("-", 1)[-1]
        return None

    async def request(self, method: str, path: str, token: str) -> "im.HttpResponse":
        self.sent.append((method, path, token))
        actor = token.split("-")[-1]
        owner = self._owner(path)

        if owner is None:
            # A listing. Returns only the actor's own rows — which is what makes
            # the list_contains positive control pass and its cross-tenant probe
            # fail.
            prefixes = ("call", "rec", "camp", "inv", "key", "audit", "conn")
            owners = (
                ("1", "2", "3")
                if any(marker in path for marker in self.leaking)
                else (actor,)
            )
            body = json.dumps(
                {
                    "items": [
                        {"id": f"{prefix}-{owner}"}
                        for owner in owners
                        for prefix in prefixes
                    ]
                }
            )
            return im.HttpResponse(200, body)

        if owner == actor:
            return im.HttpResponse(
                self.control_status, json.dumps({"id": f"x-{owner}", "tenant": owner})
            )

        # Cross-tenant.
        if any(marker in path for marker in self.leaking):
            return im.HttpResponse(
                200, json.dumps({"id": f"x-{owner}", "secret": "cross-tenant payload"})
            )
        return im.HttpResponse(self.deny_status, json.dumps({"detail": "Not found"}))


def read_probes(classes: tuple[str, ...] = ("calls_recordings",)):
    return [
        p
        for p in im.RESOURCE_CLASSES
        if p.resource_class in classes and not p.mutating and p.kind == "fetch_by_id"
    ]


async def run_matrix(manifest, api, probes=None, **kwargs):
    probes = probes if probes is not None else read_probes()
    runner = im.MatrixRunner(
        api, manifest, probes, base_url="http://localhost:8000", **kwargs
    )
    pairs = im.ordered_pairs([t.id for t in manifest.tenants])
    return await runner.run(pairs)


# ---------------------------------------------------------------------------
# Pair generation
# ---------------------------------------------------------------------------


def test_ordered_pairs_covers_every_ordered_pair_and_no_self_pairs():
    ids = ["a", "b", "c", "d"]
    pairs = im.ordered_pairs(ids)

    assert len(pairs) == len(ids) * (len(ids) - 1) == 12
    assert len(set(pairs)) == len(pairs)
    assert all(a != b for a, b in pairs)
    # Ordered: isolation is not symmetric, so both directions must be present.
    for a in ids:
        for b in ids:
            if a != b:
                assert (a, b) in pairs


def test_ordered_pairs_of_a_single_tenant_is_empty():
    assert im.ordered_pairs(["only"]) == []


def test_select_pairs_is_bounded_deterministic_and_covers_every_tenant_first():
    ids = [f"t{i}" for i in range(10)]
    sampled = im.select_pairs(ids, max_pairs=15, seed=7)

    assert len(sampled) == 15
    assert all(a != b for a, b in sampled)
    assert len(set(sampled)) == 15
    # The ring comes first, so every tenant acts and is targeted at least once.
    actors = {a for a, _ in sampled[:10]}
    targets = {b for _, b in sampled[:10]}
    assert actors == set(ids)
    assert targets == set(ids)
    assert im.select_pairs(ids, max_pairs=15, seed=7) == sampled

    assert im.select_pairs(ids) == im.ordered_pairs(ids)


# ---------------------------------------------------------------------------
# Production-URL guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "https://api.talky.ai",
        "https://talky.ai/api",
        "http://144.76.17.150:8000",
        "https://prod.internal.example.com",
        "https://production.example.com",
        "https://app.example.com",
    ],
)
def test_production_looking_urls_are_refused(url):
    assert im.is_production_url(url) is True
    with pytest.raises(im.IsolationMatrixError, match="REFUSING"):
        im.assert_safe_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "https://staging.talky.ai",
        "https://validation.example.com",
        "http://qa.internal:8000",
    ],
)
def test_non_production_urls_are_allowed(url):
    assert im.is_production_url(url) is False
    im.assert_safe_url(url)


def test_main_exits_error_code_on_a_production_url(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest_dict(T1, T2)), encoding="utf-8")

    code = im.main(["--base-url", "https://api.talky.ai", "--manifest", str(path)])

    assert code == im.EXIT_ERROR


def test_there_is_no_flag_that_overrides_the_production_guard():
    options = {a for action in im.build_parser()._actions for a in action.option_strings}
    for escape_hatch in ("--force", "--yes", "--i-know-what-im-doing", "--allow-production"):
        assert escape_hatch not in options


# ---------------------------------------------------------------------------
# Manifest contract
# ---------------------------------------------------------------------------


def test_manifest_accepts_the_documented_shape_and_its_spelling_variants():
    data = manifest_dict(T1, T2)
    data["tenants"][1]["tenant_id"] = data["tenants"][1].pop("id")
    data["tenants"][1]["accounts"] = data["tenants"][1].pop("users")
    data["tenants"][1]["resources"]["calls"] = [{"id": "call-2"}]

    manifest = im.parse_manifest(data)

    assert [t.id for t in manifest.tenants] == [T1, T2]
    assert manifest.tenants[1].users[0].email == "admin2@validation.test"
    assert manifest.tenants[1].ids_for("calls") == ("call-2",)
    assert manifest.unseeded_tenant_ids == ()


def test_manifest_with_fewer_than_two_usable_tenants_is_refused():
    data = manifest_dict(T1, T2)
    data["tenants"][1]["users"] = [{"email": "nopassword@validation.test"}]

    with pytest.raises(im.IsolationMatrixError, match="at least 2 tenants"):
        im.parse_manifest(data)


def test_load_manifest_reports_a_missing_file_rather_than_crashing(tmp_path):
    with pytest.raises(im.IsolationMatrixError, match="manifest not found"):
        im.load_manifest(str(tmp_path / "nope.json"))


def test_platform_admins_are_never_used_as_the_acting_user():
    data = manifest_dict(T1, T2)
    data["tenants"][0]["users"] = [
        {"email": "root@platform.test", "password": "pw", "role": "platform_admin"},
        {"email": "ops@t1.test", "password": "pw", "role": "agent"},
    ]
    manifest = im.parse_manifest(data)

    actor = manifest.tenants[0].actor()

    assert actor is not None
    assert actor.email == "ops@t1.test"


# ---------------------------------------------------------------------------
# Safety: mutations
# ---------------------------------------------------------------------------


def test_mutating_probes_are_excluded_unless_explicitly_allowed():
    read_only = im.select_probes(allow_mutations=False)
    with_mutations = im.select_probes(allow_mutations=True)

    assert read_only, "read-only probe set must not be empty"
    assert all(not p.mutating for p in read_only)
    assert any(p.mutating for p in with_mutations)
    assert all(p.method == "GET" for p in read_only)


def test_mutation_run_is_refused_when_a_tenant_lacks_seeder_provenance(tmp_path):
    data = manifest_dict(T1, T2, seeded=False)
    data["generator"] = "hand-written"
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    code = im.main(
        [
            "--base-url",
            "http://localhost:8000",
            "--manifest",
            str(path),
            "--allow-mutations",
        ]
    )

    assert code == im.EXIT_ERROR


def test_a_mutating_probe_never_uses_itself_as_its_own_control():
    mutating = [p for p in im.RESOURCE_CLASSES if p.mutating]
    assert mutating, "the matrix must ship at least one mutation probe"

    for probe in mutating:
        method, path, _kind = probe.control()
        assert method == "GET", f"{probe.name} control must be a read"
        assert not (method == probe.method and path == probe.path)


# ---------------------------------------------------------------------------
# Resource classes
# ---------------------------------------------------------------------------


def test_the_six_highest_risk_resource_classes_are_covered():
    expected = {
        "calls_recordings",
        "campaigns_leads",
        "billing_ledger",
        "api_keys_audit",
        "connectors_credentials",
        "reviews",
    }
    assert expected.issubset(set(im.class_names()))
    for name in expected:
        assert any(p.resource_class == name for p in im.select_probes(allow_mutations=False))


# ---------------------------------------------------------------------------
# Status triage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status,expected",
    [
        (200, im.OUTCOME_LEAKED),
        (201, im.OUTCOME_LEAKED),
        (302, im.OUTCOME_LEAKED),  # redirect to a signed asset URL is still a leak
        (403, im.OUTCOME_DENIED),
        (404, im.OUTCOME_DENIED),
        (410, im.OUTCOME_DENIED),
        (401, im.OUTCOME_INCONCLUSIVE),  # our token was rejected: proves nothing
        (422, im.OUTCOME_INCONCLUSIVE),
        (429, im.OUTCOME_INCONCLUSIVE),
        (500, im.OUTCOME_INCONCLUSIVE),
    ],
)
def test_cross_tenant_status_triage(status, expected):
    spec = read_probes()[0]
    outcome, reason = im.classify_cross_tenant(spec, status, "{}", "call-2")
    assert outcome == expected
    assert reason


def test_a_listing_that_contains_the_other_tenants_id_is_a_leak():
    spec = next(p for p in im.RESOURCE_CLASSES if p.kind == "list_contains")

    leak, _ = im.classify_cross_tenant(spec, 200, '{"items":[{"id":"camp-2"}]}', "camp-2")
    clean, _ = im.classify_cross_tenant(spec, 200, '{"items":[{"id":"camp-1"}]}', "camp-2")

    assert leak == im.OUTCOME_LEAKED
    assert clean == im.OUTCOME_DENIED


# ---------------------------------------------------------------------------
# End-to-end runs against the fake API
# ---------------------------------------------------------------------------


async def test_a_correct_backend_produces_a_pass_with_a_non_vacuity_count():
    manifest = make_manifest(T1, T2, T3)
    report = await run_matrix(manifest, FakeAPI())

    verdict, _ = report.verdict()
    totals = report.totals

    assert verdict == im.VERDICT_PASS
    assert report.exit_code() == im.EXIT_PASS
    assert totals["leaked"] == 0
    assert totals["denied"] == totals["attempted"] > 0
    assert totals["non_vacuous"] == totals["denied"]
    assert totals["positive_control_failed"] == 0
    assert len(report.pairs) == 6  # 3 tenants -> 6 ordered pairs
    assert "PASS" in im.render_summary(report)


async def test_a_simulated_leak_is_reported_named_and_exits_non_zero():
    manifest = make_manifest(T1, T2)
    api = FakeAPI(leaking_probes={"/transcript"})

    report = await run_matrix(manifest, api)

    verdict, reasons = report.verdict()
    assert verdict == im.VERDICT_LEAK
    assert report.exit_code() == im.EXIT_LEAK
    assert report.exit_code() != 0
    assert any("succeeded" in r for r in reasons)

    leaks = report.leaks
    assert leaks, "the leak must be listed individually"
    leak = leaks[0]
    # Reproducible: exact request and response status, and which resource.
    assert leak.resource_class == "calls_recordings"
    assert leak.probe == "call_transcript"
    assert leak.method == "GET"
    assert leak.url.endswith("/transcript")
    assert leak.resource_id in ("call-1", "call-2")
    assert leak.resource_id in leak.url
    assert leak.status == 200
    assert leak.actor_tenant != leak.target_tenant

    summary = im.render_summary(report)
    assert "LEAK" in summary
    assert leak.resource_id in summary
    assert leak.url in summary

    payload = report.to_dict()
    assert payload["verdict"] == im.VERDICT_LEAK
    assert payload["exit_code"] == im.EXIT_LEAK
    assert payload["leaks"][0]["status"] == 200
    assert payload["by_resource_class"]["calls_recordings"]["leaked"] >= 1


async def test_a_run_whose_positive_controls_fail_is_invalid_not_passed():
    manifest = make_manifest(T1, T2)
    # Every cross-tenant probe 404s — which looks exactly like a clean sheet —
    # but the tenants cannot read their OWN resources either, so the 404s carry
    # no information at all.
    api = FakeAPI(control_status=404)

    report = await run_matrix(manifest, api)

    verdict, reasons = report.verdict()
    totals = report.totals

    assert verdict == im.VERDICT_INVALID
    assert verdict != im.VERDICT_PASS
    assert report.exit_code() == im.EXIT_INVALID
    assert report.exit_code() != 0
    assert totals["positive_control_failed"] == totals["attempted"] > 0
    assert totals["positive_control_passed"] == 0
    assert totals["denied"] == 0, "a denial with a failed control is not a denial"
    assert totals["inconclusive"] == totals["attempted"]
    assert totals["non_vacuous"] == 0
    assert any("positive control" in r.lower() for r in reasons)

    summary = im.render_summary(report)
    assert "INVALID" in summary
    assert "PROVES NOTHING" in summary
    assert "VERDICT: PASS" not in summary


async def test_a_run_with_nothing_to_probe_is_invalid_not_a_clean_sheet():
    data = manifest_dict(T1, T2)
    for tenant in data["tenants"]:
        tenant["resources"] = {}
    manifest = im.parse_manifest(data)

    report = await run_matrix(manifest, FakeAPI())

    verdict, reasons = report.verdict()
    totals = report.totals

    assert verdict == im.VERDICT_INVALID
    assert report.exit_code() == im.EXIT_INVALID
    assert totals["attempted"] == 0
    assert totals["skipped"] > 0
    assert totals["non_vacuous"] == 0
    assert any("zero non-vacuous" in r for r in reasons)


async def test_an_inconclusive_status_never_counts_as_a_denial():
    manifest = make_manifest(T1, T2)
    # 401 on every cross-tenant request: the actor's token was not accepted.
    report = await run_matrix(manifest, FakeAPI(deny_status=401))

    verdict, _ = report.verdict()
    totals = report.totals

    assert verdict == im.VERDICT_INVALID
    assert totals["denied"] == 0
    assert totals["inconclusive"] == totals["attempted"] > 0
    # The controls DID pass here — the run is invalid on the status triage alone.
    assert totals["positive_control_passed"] == totals["attempted"]


async def test_every_ordered_pair_is_actually_probed_and_none_probes_itself():
    manifest = make_manifest(T1, T2, T3)
    report = await run_matrix(manifest, FakeAPI())

    seen = {(r.actor_tenant, r.target_tenant) for r in report.results}

    assert seen == set(im.ordered_pairs([T1, T2, T3]))
    assert all(a != b for a, b in seen)
    assert len(report.by_pair()) == 6


async def test_concurrency_does_not_change_the_verdict_or_the_counts():
    manifest = make_manifest(T1, T2, T3)

    serial = await run_matrix(manifest, FakeAPI(), concurrency=1)
    parallel = await run_matrix(manifest, FakeAPI(), concurrency=4)

    assert serial.verdict()[0] == parallel.verdict()[0] == im.VERDICT_PASS
    assert serial.totals == parallel.totals
    assert {(r.actor_tenant, r.target_tenant, r.probe) for r in serial.results} == {
        (r.actor_tenant, r.target_tenant, r.probe) for r in parallel.results
    }


async def test_each_tenant_logs_in_once_and_controls_are_not_reissued_per_pair():
    manifest = make_manifest(T1, T2, T3)
    api = FakeAPI()

    await run_matrix(manifest, api, concurrency=1)

    assert sorted(api.logins) == [
        "admin1@validation.test",
        "admin2@validation.test",
        "admin3@validation.test",
    ]


async def test_recheck_controls_reissues_the_control_for_every_pair():
    manifest = make_manifest(T1, T2, T3)
    cached, rechecked = FakeAPI(), FakeAPI()

    await run_matrix(manifest, cached, concurrency=1)
    await run_matrix(manifest, rechecked, concurrency=1, recheck_controls=True)

    assert len(rechecked.sent) > len(cached.sent)


async def test_existence_disclosure_is_a_warning_and_not_a_leak():
    manifest = make_manifest(T1, T2)

    # 403 for the other tenant's real row, 404 for an id that exists nowhere:
    # the difference tells the attacker the row exists.
    class Disclosing(FakeAPI):
        async def request(self, method, path, token):
            resp = await super().request(method, path, token)
            if resp.status == 404 and self._owner(path) is not None:
                return im.HttpResponse(403, resp.body)
            return resp

    report = await run_matrix(
        manifest, Disclosing(), check_existence_disclosure=True
    )

    verdict, _ = report.verdict()
    assert verdict == im.VERDICT_PASS  # denial is still a denial
    assert any(r.existence_disclosed for r in report.results)
    assert "WARNING" in im.render_summary(report)


async def test_a_transport_failure_is_inconclusive_and_never_a_silent_denial():
    manifest = make_manifest(T1, T2)

    class Broken(FakeAPI):
        async def request(self, method, path, token):
            owner = self._owner(path)
            if owner is not None and owner != token.split("-")[-1]:
                raise ConnectionError("connection reset")
            return await super().request(method, path, token)

    report = await run_matrix(manifest, Broken())

    assert report.verdict()[0] == im.VERDICT_INVALID
    assert report.totals["denied"] == 0
    assert report.totals["inconclusive"] > 0
    assert report.errors


# ---------------------------------------------------------------------------
# Report shape
# ---------------------------------------------------------------------------


async def test_the_report_round_trips_through_json():
    manifest = make_manifest(T1, T2, T3)
    api = FakeAPI(leaking_probes={"/transcript"})
    report = await run_matrix(manifest, api)

    payload = report.to_dict()
    restored = im.Report.from_dict(json.loads(json.dumps(payload)))

    assert restored.to_dict() == payload
    assert restored.verdict() == report.verdict()
    assert restored.exit_code() == report.exit_code()
    assert restored.totals == report.totals
    assert [r.to_dict() for r in restored.leaks] == [r.to_dict() for r in report.leaks]


async def test_the_report_records_per_pair_and_per_class_counters():
    manifest = make_manifest(T1, T2)
    report = await run_matrix(
        manifest,
        FakeAPI(),
        probes=im.select_probes(allow_mutations=False),
    )
    payload = report.to_dict()

    required = {
        "attempted",
        "denied",
        "leaked",
        "inconclusive",
        "skipped",
        "positive_control_passed",
        "positive_control_failed",
        "non_vacuous",
    }
    assert payload["schema"] == "talky.isolation_matrix/1"
    assert set(payload["totals"]) == required
    for counters in payload["by_resource_class"].values():
        assert set(counters) == required
    for row in payload["by_pair"]:
        assert row["actor_tenant"] != row["target_tenant"]
        for counters in row["classes"].values():
            assert set(counters) == required
    assert payload["mode"]["mutations_enabled"] is False
    assert payload["manifest"]["tenant_count"] == 2


def test_plan_only_prints_the_plan_without_touching_the_network(tmp_path, capsys):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest_dict(T1, T2, T3)), encoding="utf-8")

    code = im.main(
        [
            "--base-url",
            "http://localhost:8000",
            "--manifest",
            str(path),
            "--plan-only",
        ]
    )
    plan = json.loads(capsys.readouterr().out)

    assert code == im.EXIT_PASS
    assert plan["tenants"] == 3
    assert plan["pairs"] == 6
    assert plan["probes_per_pair"] > 0
    assert set(plan["classes"]) == set(im.class_names())


async def test_the_full_read_only_probe_set_passes_against_a_correct_backend():
    """Exercises every class, including the list-containment probes."""
    manifest = make_manifest(T1, T2)
    report = await run_matrix(
        manifest, FakeAPI(), probes=im.select_probes(allow_mutations=False)
    )
    totals = report.totals

    assert report.verdict()[0] == im.VERDICT_PASS
    assert totals["skipped"] == 0
    assert totals["positive_control_failed"] == 0
    assert set(report.by_resource_class()) == set(im.class_names())


async def test_a_listing_that_leaks_another_tenants_row_is_caught():
    manifest = make_manifest(T1, T2)
    listing = next(p for p in im.RESOURCE_CLASSES if p.kind == "list_contains")

    report = await run_matrix(
        manifest, FakeAPI(leaking_probes={listing.path}), probes=[listing]
    )

    assert report.verdict()[0] == im.VERDICT_LEAK
    assert report.leaks[0].probe == listing.name


def test_main_wires_a_leak_through_to_a_non_zero_exit_and_a_written_report(
    tmp_path, monkeypatch
):
    """The exit-code contract CI gates on, end to end through main()."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_dict(T1, T2)), encoding="utf-8")
    out = tmp_path / "report.json"

    class LeakyClient(FakeAPI):
        def __init__(self, base_url, **kwargs):
            super().__init__(leaking_probes={"/transcript"})

        async def aclose(self):
            return None

    monkeypatch.setattr(im, "HttpxProbeClient", LeakyClient)

    code = im.main(
        [
            "--base-url",
            "http://localhost:8000",
            "--manifest",
            str(manifest_path),
            "--out",
            str(out),
            "--classes",
            "calls_recordings",
        ]
    )
    payload = json.loads(out.read_text(encoding="utf-8"))

    assert code == im.EXIT_LEAK == 1
    assert payload["verdict"] == im.VERDICT_LEAK
    assert payload["leaks"], "the written report must list the leak"
    assert payload["leaks"][0]["probe"] == "call_transcript"


def test_main_rejects_an_unknown_resource_class(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_dict(T1, T2)), encoding="utf-8")

    code = im.main(
        [
            "--base-url",
            "http://localhost:8000",
            "--manifest",
            str(manifest_path),
            "--classes",
            "not_a_class",
        ]
    )

    assert code == im.EXIT_ERROR


# ---------------------------------------------------------------------------
# Reconciliation with the real seeder
#
# The two scripts were written in parallel against a documented contract. This
# is the test that keeps them honest: it feeds the runner a manifest built by
# scripts/seed_validation_tenants.py itself, so a divergence fails here instead
# of producing an empty matrix on the night of the validation run.
# ---------------------------------------------------------------------------

_SEEDER = Path(__file__).resolve().parents[2] / "scripts" / "seed_validation_tenants.py"


def _seeder_manifest(*, applied: bool):
    import uuid as _uuid

    if not _SEEDER.exists():  # pragma: no cover — seeder is a sibling deliverable
        pytest.skip("scripts/seed_validation_tenants.py not present")
    spec = importlib.util.spec_from_file_location("seed_validation_tenants", _SEEDER)
    seeder = importlib.util.module_from_spec(spec)
    sys.modules["seed_validation_tenants"] = seeder
    assert spec.loader is not None
    spec.loader.exec_module(seeder)

    params = seeder.SeedParameters(
        seed="20260829",
        tenants=3,
        campaigns_per_tenant=1,
        leads_per_campaign=2,
        calls_per_campaign=2,
    )
    population = seeder.plan_population(params)
    target = seeder.TargetClassification(kind="local", host="localhost", database="talky")
    results = [
        seeder.TenantResult(
            plan=t,
            tenant_id=str(_uuid.uuid5(_uuid.NAMESPACE_DNS, t.slug)) if applied else None,
            created=True if applied else None,
        )
        for t in population.tenants
    ]
    return seeder.build_manifest(
        population,
        results,
        applied=applied,
        target=target,
        api_base_url="http://localhost:8000",
        generated_at="2026-08-29T00:00:00Z",
        master_admin_id=str(_uuid.uuid4()) if applied else None,
    )


def test_the_runner_consumes_a_manifest_built_by_the_real_seeder():
    manifest = im.parse_manifest(json.loads(json.dumps(_seeder_manifest(applied=True))))

    assert len(manifest.tenants) == 3
    assert manifest.unseeded_tenant_ids == (), "seeder output must carry provenance"
    assert manifest.seed == "20260829"

    tenant = manifest.tenants[0]
    actor = tenant.actor()
    assert actor is not None and actor.password, "every tenant needs a usable login"
    assert actor.role == "tenant_admin"
    # The classes the seeder actually creates must be probeable...
    assert tenant.ids_for("calls") and tenant.ids_for("campaigns")
    # ...and the ones it does not create must be absent, i.e. SKIPPED, not a pass.
    assert tenant.ids_for("recordings") == ()

    pairs = im.ordered_pairs([t.id for t in manifest.tenants])
    assert len(pairs) == 6


async def test_classes_the_seeder_does_not_create_are_skipped_not_passed():
    manifest = im.parse_manifest(json.loads(json.dumps(_seeder_manifest(applied=True))))
    probes = im.select_probes(allow_mutations=False)

    runner = im.MatrixRunner(FakeAPI(), manifest, probes, base_url="http://localhost:8000")
    report = await runner.run(im.ordered_pairs([t.id for t in manifest.tenants]))

    by_class = report.by_resource_class()
    assert by_class["billing_ledger"]["skipped"] > 0
    assert by_class["billing_ledger"]["attempted"] == 0
    assert by_class["billing_ledger"]["denied"] == 0
    assert by_class["billing_ledger"]["non_vacuous"] == 0
    assert report.totals["skipped"] > 0


def test_a_dry_run_manifest_is_rejected_because_there_is_nothing_to_probe():
    dry = json.loads(json.dumps(_seeder_manifest(applied=False)))

    with pytest.raises(im.IsolationMatrixError, match="DRY-RUN"):
        im.parse_manifest(dry)


def test_the_shared_credentials_password_is_honoured():
    data = manifest_dict(T1, T2)
    data["credentials"] = {"password": "shared-synthetic-pw"}
    for tenant in data["tenants"]:
        for user in tenant["users"]:
            user.pop("password")

    manifest = im.parse_manifest(data)

    actor = manifest.tenants[0].actor()
    assert actor is not None
    assert actor.password == "shared-synthetic-pw"
