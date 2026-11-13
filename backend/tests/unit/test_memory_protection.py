"""F7-05 safe OOM priorities and a read-only, fail-closed budget audit."""
import configparser
import importlib.util
from pathlib import Path

import pytest
import yaml


BACKEND = Path(__file__).resolve().parents[2]
WORKERS = ("dialer-worker", "voice-worker", "reminder-worker", "cleanup", "support-jobs")


def config(path):
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.read(path, encoding="utf-8")
    return parser


def test_database_and_docker_are_protected_and_workers_are_sacrificed_first():
    compose = yaml.safe_load((BACKEND / "deploy/infra/compose.prod.yml").read_text())
    assert compose["services"]["postgres"].get("oom_score_adj") == -900
    docker = config(BACKEND / "deploy/infra/systemd/docker-oom.conf")
    assert docker.getint("Service", "OOMScoreAdjust", fallback=0) == -900
    for worker in WORKERS:
        unit = config(BACKEND / "systemd" / f"talky-{worker}.service")
        assert 0 < unit.getint("Service", "OOMScoreAdjust", fallback=0) <= 1000, worker
    installer = (BACKEND / "systemd/install-services.sh").read_text()
    assert '"$SCRIPT_DIR/../deploy/infra/systemd/docker-oom.conf"' in installer
    assert "/etc/systemd/system/docker.service.d/60-talky-oom.conf" in installer
    assert "restart docker" not in installer, "Deploy must not restart the database's container daemon"


@pytest.fixture
def audit():
    path = BACKEND / "scripts/check_memory_budget.py"
    assert path.is_file(), "F7-05 lacks a fail-closed memory budget audit"
    spec = importlib.util.spec_from_file_location("qa_memory_budget", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_tree(tmp_path, *, max_value="512M", high_value="384M", postgres="768m", redis="128m"):
    units = tmp_path / "units"
    units.mkdir()
    (units / "talky-test.service").write_text(f"[Service]\nMemoryMax={max_value}\nMemoryHigh={high_value}\n")
    compose = tmp_path / "compose.yml"
    compose.write_text(yaml.safe_dump({"services": {"postgres": {"mem_limit": postgres}, "redis": {"mem_limit": redis}}}))
    return units, compose


def test_audit_counts_units_and_containers_with_explicit_host_headroom(audit, tmp_path):
    units, compose = fixture_tree(tmp_path)
    report = audit.inspect_budget(units, compose, host_mib=3891, headroom_mib=768)
    assert report["configured_mib"] == 1408
    assert report["available_mib"] == 3123
    assert report["fits"] is True and report["unbounded"] == []


@pytest.mark.parametrize("max_value", ["infinity", "", "50%", "nonsense"])
def test_unbounded_or_ambiguous_unit_is_not_silently_counted_as_zero(audit, tmp_path, max_value):
    units, compose = fixture_tree(tmp_path, max_value=max_value)
    report = audit.inspect_budget(units, compose, host_mib=3891, headroom_mib=768)
    assert report["fits"] is False and report["unbounded"] == ["talky-test.service"]


def test_overcommit_and_high_above_max_are_refused_without_changing_files(audit, tmp_path):
    units, compose = fixture_tree(tmp_path, max_value="2G", high_value="3G", postgres="1536m", redis="384m")
    before = {path: path.read_bytes() for path in [*units.iterdir(), compose]}
    report = audit.inspect_budget(units, compose, host_mib=3891, headroom_mib=768)
    assert report["configured_mib"] == 3968 and report["fits"] is False
    assert "talky-test.service: MemoryHigh exceeds MemoryMax" in report["issues"]
    assert all(path.read_bytes() == content for path, content in before.items())


def test_missing_container_limit_and_invalid_headroom_fail_closed(audit, tmp_path):
    units, compose = fixture_tree(tmp_path, postgres=None)
    assert audit.inspect_budget(units, compose, host_mib=3891, headroom_mib=768)["fits"] is False
    with pytest.raises(ValueError):
        audit.inspect_budget(units, compose, host_mib=100, headroom_mib=100)
