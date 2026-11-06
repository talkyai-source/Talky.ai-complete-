"""F2-15: model metadata and static source inventory, never live settings."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings

from app.core import config

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "backend/scripts/generate_env_example.py"


@pytest.fixture
def generator(monkeypatch):
    assert SCRIPT.is_file(), "F2-15 is missing the metadata-only environment template generator"

    def refuse_live_settings(*args, **kwargs):
        raise AssertionError("Template generation must not construct Settings or read .env")

    monkeypatch.setattr(config.Settings, "__init__", refuse_live_settings)
    monkeypatch.setattr(config, "get_settings", refuse_live_settings)
    spec = importlib.util.spec_from_file_location("round3_env_example_generator", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _assignments(text):
    result = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        assert separator and name and name.upper() == name, line
        assert name not in result, f"duplicate environment name: {name}"
        result[name] = value
    return result


def _project(tmp_path):
    (tmp_path / "backend/app").mkdir(parents=True)
    (tmp_path / "backend/app/consumer.py").write_text(
        'import os\nos.getenv("LEGACY_TOKEN", "never-publish-this-default")\n', encoding="utf-8"
    )
    (tmp_path / "docker-compose.yml").write_text(
        'services:\n  db:\n    environment:\n      PASSWORD: ${POSTGRES_PASSWORD:?required}\n',
        encoding="utf-8",
    )
    return tmp_path


def test_model_metadata_uses_environment_aliases_and_never_defaults(generator, monkeypatch):
    class SyntheticSettings(BaseSettings):
        plain: str = "private-default-do-not-output"
        token: SecretStr = SecretStr("private-secret-default-do-not-output")
        renamed: str = Field("private-alias-default", validation_alias="PUBLIC_NAME")
        choices: str = Field("private-choice-default", validation_alias=AliasChoices("NEW_NAME", "OLD_NAME"))

    monkeypatch.setenv("PLAIN", "private-process-value-do-not-output")
    text = generator.render_template(SyntheticSettings, legacy_names={"LEGACY_TOKEN"}, compose_names={"POSTGRES_PASSWORD"})
    assert set(_assignments(text)) == {"PLAIN", "TOKEN", "PUBLIC_NAME", "NEW_NAME", "OLD_NAME", "LEGACY_TOKEN", "POSTGRES_PASSWORD"}
    assert set(_assignments(text).values()) == {""}
    assert "private-" not in text
    assert "Settings model" in text and "Supplemental static" in text


def test_render_is_deterministic_and_deduplicates_supplemental_names(generator):
    first = generator.render_template(config.Settings, legacy_names={"JWT_SECRET", "ZZZ_LEGACY", "AAA_LEGACY"}, compose_names={"ZZZ_LEGACY"})
    second = generator.render_template(config.Settings, legacy_names={"AAA_LEGACY", "ZZZ_LEGACY", "JWT_SECRET"}, compose_names={"ZZZ_LEGACY"})
    assert first == second
    names = _assignments(first)
    assert names["JWT_SECRET"] == ""
    assert names["PLATFORM_ADMIN_MFA_REQUIRED"] == ""
    assert names["TALKY_CALLER_ID_TRUSTED_ENDPOINTS"] == ""
    assert "INBOUND_CALLER_ID_TRUSTED_ENDPOINTS" not in names


def test_legacy_inventory_finds_static_reads_and_reports_dynamic_reads(generator, tmp_path):
    source = tmp_path / "consumer.py"
    source.write_text('''import os
import os as process_os
from os import getenv as read_environment, environ as environment
os.getenv("GETENV", "not-output")
process_os.getenv(key="ALIASED", default="not-output")
os.environ["SUBSCRIPT"]
os.environ.get("GET")
environment.get("ENV_ALIAS")
read_environment("FUNCTION_ALIAS")
os.getenv(prefix + "_TOKEN")
unrelated.getenv("NOT_AN_ENV_READ")
example = 'os.getenv("NOT_EXECUTED")'
''', encoding="utf-8")
    inventory = generator.legacy_inventory(tmp_path)
    assert set(inventory["names"]) == {"GETENV", "ALIASED", "SUBSCRIPT", "GET", "ENV_ALIAS", "FUNCTION_ALIAS"}
    assert inventory["dynamic_reads"] == ["consumer.py:10"]
    assert all(locations and all(location.startswith("consumer.py:") for location in locations)
               for locations in inventory["names"].values())
    assert "not-output" not in str(inventory)


def test_cli_default_prints_empty_template_without_touching_any_file(generator, tmp_path, capsys, monkeypatch):
    root = _project(tmp_path)
    sentinel = root / ".env"
    sentinel.write_text("NEVER_READ=this-is-synthetic-test-data\n", encoding="utf-8")
    original_read = Path.read_text

    def guarded_read(path, *args, **kwargs):
        assert path.name != ".env", "generator attempted to read private process configuration"
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", guarded_read)
    assert generator.main(["--root", str(root)]) == 0
    output = capsys.readouterr().out
    assert _assignments(output)["LEGACY_TOKEN"] == ""
    assert _assignments(output)["POSTGRES_PASSWORD"] == ""
    assert "this-is-synthetic" not in output and "never-publish" not in output
    assert not (root / ".env.example").exists()
    assert not (root / "backend/.env.example").exists()


def test_write_is_explicit_and_only_writes_two_templates(generator, tmp_path):
    root = _project(tmp_path)
    unrelated = root / "keep.txt"
    unrelated.write_text("keep", encoding="utf-8")
    assert generator.main(["--root", str(root), "--write"]) == 0
    assert unrelated.read_text(encoding="utf-8") == "keep"
    root_text = (root / ".env.example").read_text(encoding="utf-8")
    backend_text = (root / "backend/.env.example").read_text(encoding="utf-8")
    assert root_text == backend_text
    assert set(_assignments(root_text).values()) == {""}
    assert generator.main(["--root", str(root), "--check"]) == 0


@pytest.mark.parametrize("relative", [".env.example", "backend/.env.example"])
def test_missing_or_stale_template_fails_check_without_rewriting(generator, tmp_path, relative):
    root = _project(tmp_path)
    assert generator.main(["--root", str(root), "--write"]) == 0
    target = root / relative
    content = target.read_text(encoding="utf-8").replace("JWT_SECRET=\n", "")
    target.write_text(content, encoding="utf-8")
    assert generator.main(["--root", str(root), "--check"]) == 1
    assert target.read_text(encoding="utf-8") == content


def test_a_new_settings_field_makes_checked_in_templates_stale(generator, tmp_path, monkeypatch):
    root = _project(tmp_path)
    assert generator.main(["--root", str(root), "--write"]) == 0
    monkeypatch.setitem(config.Settings.model_fields, "newly_required_field", Field(default=None))
    assert generator.main(["--root", str(root), "--check"]) == 1


def test_repository_templates_are_current_and_every_assignment_is_empty(generator):
    assert generator.main(["--root", str(ROOT), "--check"]) == 0
    for relative in (".env.example", "backend/.env.example"):
        assert set(_assignments((ROOT / relative).read_text(encoding="utf-8")).values()) == {""}


def test_supplemental_inventory_is_separate_from_settings_coverage(generator, tmp_path, capsys):
    root = _project(tmp_path)
    assert generator.main(["--root", str(root), "--inventory"]) == 0
    import json
    report = json.loads(capsys.readouterr().out)
    assert "LEGACY_TOKEN" in report["names"]
    assert "LEGACY_TOKEN" not in generator.settings_env_names(config.Settings)
    assert "POSTGRES_PASSWORD" in report["compose_names"]
    assert "never-publish" not in str(report)


def test_conflicting_write_and_check_modes_are_refused(generator, tmp_path):
    with pytest.raises(SystemExit) as caught:
        generator.main(["--root", str(_project(tmp_path)), "--write", "--check"])
    assert caught.value.code == 2
