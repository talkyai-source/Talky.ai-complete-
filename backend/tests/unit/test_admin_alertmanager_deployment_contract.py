"""AD08 deployment artifacts: real rendering, no notification/network calls."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "telephony/observability/alertmanager/render_production_config.py"
SPEC = importlib.util.spec_from_file_location("admin_alert_renderer", SCRIPT)
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)
INGRESS = "http://127.0.0.1:8000/api/v1/webhooks/alertmanager"


@pytest.fixture
def operator_files(tmp_path):
    secrets = tmp_path / "external"
    secrets.mkdir()
    (secrets / "receiver_url").write_text(
        "https://alerts.internal.test/receive", encoding="utf-8"
    )
    (secrets / "receiver_credential").write_text(
        "synthetic-external-credential", encoding="utf-8"
    )
    token = tmp_path / "ingest_token"
    token.write_text("synthetic-ingestion-credential-000001", encoding="utf-8")
    approved = {
        "receivers": [
            {
                "name": name,
                "webhook_configs": [
                    {
                        "url_file": "/etc/alertmanager/external/receiver_url",
                        "http_config": {
                            "authorization": {
                                "credentials_file": "/etc/alertmanager/external/receiver_credential"
                            }
                        },
                    }
                ],
            }
            for name in sorted(renderer.NAMES)
        ]
    }
    source = tmp_path / "approved.yml"
    source.write_text(yaml.safe_dump(approved), encoding="utf-8")
    return SimpleNamespace(
        secrets=secrets,
        token=token,
        approved=approved,
        source=source,
        output=tmp_path / "production.yml",
    )


def test_real_render_preserves_routing_and_all_three_independent_external_deliveries(
    operator_files,
):
    files = operator_files
    baseline = renderer.BASELINE.read_bytes()
    digest = renderer.render(
        files.source, files.output, INGRESS, files.secrets, files.token
    )
    output = yaml.safe_load(files.output.read_text(encoding="utf-8"))
    original = yaml.safe_load(baseline)
    for key in ("route", "global", "inhibit_rules"):
        assert output[key] == original[key]
    assert renderer.BASELINE.read_bytes() == baseline
    assert len(digest) == 64
    assert {item["name"] for item in output["receivers"]} == renderer.NAMES
    for receiver in output["receivers"]:
        external, ingestion = receiver["webhook_configs"]
        assert external["url_file"] == "/etc/alertmanager/external/receiver_url"
        assert ingestion["url"] == INGRESS
        assert ingestion["http_config"]["authorization"] == {
            "type": "Bearer",
            "credentials_file": renderer.TOKEN_PATH,
        }
        assert ingestion["send_resolved"] is True
        assert ingestion["max_alerts"] == 0
        assert external["http_config"]["follow_redirects"] is False
    raw = files.output.read_text(encoding="utf-8")
    assert "synthetic-ingestion-credential" not in raw
    assert "synthetic-external-credential" not in raw
    assert "https://alerts.internal.test" not in raw


def test_native_yaml_aliases_do_not_share_injected_ingress(operator_files):
    files = operator_files
    shared = files.approved["receivers"][0]["webhook_configs"]
    approved = {"receivers": [
        {"name": name, "webhook_configs": shared} for name in sorted(renderer.NAMES)
    ]}
    raw = yaml.safe_dump(approved)
    assert "&id" in raw and "*id" in raw
    files.source.write_text(raw, encoding="utf-8")
    renderer.render(files.source, files.output, INGRESS, files.secrets, files.token)
    output = yaml.safe_load(files.output.read_text(encoding="utf-8"))
    assert all(len(receiver["webhook_configs"]) == 2 for receiver in output["receivers"])
    assert all(sum(config.get("url") == INGRESS for config in receiver["webhook_configs"]) == 1
               for receiver in output["receivers"])
    assert files.source.read_text(encoding="utf-8") == raw


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com/api/v1/webhooks/alertmanager",
        "http://remote.internal/api/v1/webhooks/alertmanager",
        "https://user:secret@host.internal/api/v1/webhooks/alertmanager",
        "https://host.internal/api/v1/webhooks/alertmanager?token=secret",
        "https://host.internal/api/v1/webhooks/alertmanager/",
        "https://${HOST}/api/v1/webhooks/alertmanager",
        "https://host.internal:0/api/v1/webhooks/alertmanager",
    ],
)
def test_ingestion_rejects_unsafe_or_placeholder_destination(value):
    with pytest.raises(renderer.ConfigError):
        renderer.valid_url(value, ingestion=True)


@pytest.mark.parametrize(
    "change",
    [
        "missing_receiver",
        "duplicate_receiver",
        "empty_receiver",
        "empty_configs",
        "inline_url",
        "inline_secret",
        "missing_file",
        "path_escape",
        "insecure_tls",
        "route_override",
    ],
)
def test_invalid_external_config_cannot_replace_existing_approved_artifact(
    operator_files, change
):
    files = operator_files
    source = copy.deepcopy(files.approved)
    first = source["receivers"][0]
    config = first["webhook_configs"][0]
    if change == "missing_receiver":
        source["receivers"].pop()
    elif change == "duplicate_receiver":
        source["receivers"][1]["name"] = first["name"]
    elif change == "empty_receiver":
        del first["webhook_configs"]
    elif change == "empty_configs":
        first["webhook_configs"] = []
    elif change == "inline_url":
        config["url"] = "https://alerts.internal.test/secret"
    elif change == "inline_secret":
        config["http_config"]["authorization"]["credentials"] = "must-not-render"
    elif change == "missing_file":
        config["url_file"] = "/etc/alertmanager/external/missing"
    elif change == "path_escape":
        config["url_file"] = "/etc/alertmanager/external/../ingest_token"
    elif change == "insecure_tls":
        config["http_config"]["tls_config"] = {"insecure_skip_verify": True}
    else:
        source["route"] = {"receiver": "replacement"}
    files.source.write_text(yaml.safe_dump(source), encoding="utf-8")
    files.output.write_text("previous-approved-artifact", encoding="utf-8")
    with pytest.raises(renderer.ConfigError):
        renderer.render(files.source, files.output, INGRESS, files.secrets, files.token)
    assert files.output.read_text() == "previous-approved-artifact"
    assert list(files.output.parent.glob(".alertmanager-*")) == []


def test_placeholder_secret_url_is_rejected_without_disclosure(operator_files, capsys):
    files = operator_files
    (files.secrets / "receiver_url").write_text(
        "https://hooks.example.com/secret-value", encoding="utf-8"
    )
    with pytest.raises(renderer.ConfigError) as raised:
        renderer.render(files.source, files.output, INGRESS, files.secrets, files.token)
    assert "secret-value" not in str(raised.value)
    assert not files.output.exists()
    assert capsys.readouterr().out == ""


def test_duplicate_yaml_keys_are_rejected(operator_files):
    files = operator_files
    files.source.write_text("receivers: []\nreceivers: []\n", encoding="utf-8")
    with pytest.raises(renderer.ConfigError):
        renderer.render(files.source, files.output, INGRESS, files.secrets, files.token)


@pytest.mark.parametrize(
    "token", ["short", "x" * 257, "x" * 32 + " x", "é" * 32, "x" * 32 + "\x00"]
)
def test_invalid_ingestion_token_rejected_without_rendering(operator_files, token):
    files = operator_files
    files.token.write_text(token, encoding="utf-8")
    with pytest.raises(renderer.ConfigError):
        renderer.render(files.source, files.output, INGRESS, files.secrets, files.token)
    assert not files.output.exists()


def test_email_can_use_file_auth_but_cannot_disable_tls(operator_files):
    files = operator_files
    approved = {
        "receivers": [
            {
                "name": name,
                "email_configs": [
                    {
                        "to": "oncall@ops.internal",
                        "from": "alerts@ops.internal",
                        "smarthost": "smtp.ops.internal:587",
                        "auth_username": "alerts",
                        "auth_password_file": "/etc/alertmanager/external/receiver_credential",
                    }
                ],
            }
            for name in sorted(renderer.NAMES)
        ]
    }
    built = renderer.build_config(approved, INGRESS, files.secrets)
    assert all(
        receiver["email_configs"][0]["require_tls"] for receiver in built["receivers"]
    )
    approved["receivers"][0]["email_configs"][0]["tls_config"] = {
        "insecure_skip_verify": True
    }
    with pytest.raises(renderer.ConfigError):
        renderer.build_config(approved, INGRESS, files.secrets)


def test_renderer_refuses_output_inside_checkout(operator_files):
    files = operator_files
    with pytest.raises(renderer.ConfigError):
        renderer.render(
            files.source,
            ROOT / "production-alerts.yml",
            INGRESS,
            files.secrets,
            files.token,
        )


@pytest.mark.parametrize(
    "version,check_result",
    [("amtool, version 0.28.0", 0), ("amtool, version 0.27.0 (branch: HEAD)", 1)],
)
def test_wrong_version_or_native_rejection_preserves_previous_output(
    operator_files, monkeypatch, version, check_result
):
    files = operator_files
    files.output.write_text("previous-approved-artifact", encoding="utf-8")
    calls = []

    def native(args, **kwargs):
        calls.append(args)
        if "--version" in args:
            return SimpleNamespace(returncode=0, stdout=version, stderr="")
        return SimpleNamespace(
            returncode=check_result, stdout=b"secret-diagnostic", stderr=b""
        )

    monkeypatch.setattr(renderer.subprocess, "run", native)
    with pytest.raises(renderer.ConfigError) as raised:
        renderer.render(
            files.source,
            files.output,
            INGRESS,
            files.secrets,
            files.token,
            "installed-amtool",
        )
    assert "secret-diagnostic" not in str(raised.value)
    assert files.output.read_text() == "previous-approved-artifact"
    assert all("synthetic-ingestion-credential" not in str(call) for call in calls)
    assert list(files.output.parent.glob(".alertmanager-*")) == []


def test_production_override_targets_actual_config_and_never_creates_missing_secret_paths():
    directory = ROOT / "telephony/deploy/docker"
    baseline = yaml.safe_load(
        (directory / "docker-compose.observability.yml").read_text()
    )
    production = yaml.safe_load(
        (directory / "docker-compose.observability.production.yml").read_text()
    )
    assert baseline["services"]["alertmanager"]["image"] == "prom/alertmanager:v0.27.0"
    assert (
        (directory / "../../observability/alertmanager/alertmanager.yml")
        .resolve()
        .is_file()
    )
    overrides = production["services"]["alertmanager"]["volumes"]
    assert {item["target"] for item in overrides} == {
        "/etc/alertmanager/alertmanager.yml",
        renderer.TOKEN_PATH,
        "/etc/alertmanager/external",
    }
    for item in overrides:
        assert item["read_only"] is True
        assert item["bind"]["create_host_path"] is False
        assert ":?" in item["source"]
    assert ":?" in production["services"]["alertmanager"]["group_add"][0]


def test_cli_missing_input_does_not_print_credentials_or_claim_native_validation(
    operator_files,
):
    files = operator_files
    import sys

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--approved-receivers",
            str(files.source),
            "--output",
            str(files.output),
            "--ingestion-url",
            INGRESS,
            "--external-secrets-dir",
            str(files.secrets),
            "--ingest-token-file",
            str(files.token),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "NOT natively validated" in result.stdout
    assert "synthetic-ingestion-credential" not in result.stdout + result.stderr
