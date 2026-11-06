"""F12-07: source inventory for legal, without provider imports or credentials."""
from __future__ import annotations

import builtins
import importlib.util
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "backend/scripts/generate_subprocessor_inventory.py"
DOC = Path("backend/docs/legal_subprocessor_inventory.md")
FACTORIES = {
    "llm": Path("backend/app/infrastructure/llm/factory.py"),
    "stt": Path("backend/app/infrastructure/stt/factory.py"),
    "tts": Path("backend/app/infrastructure/tts/factory.py"),
}


@pytest.fixture
def generator():
    assert SCRIPT.exists(), "F12-07 is missing the source-only subprocessor inventory generator"
    spec = importlib.util.spec_from_file_location("round3_subprocessor_inventory", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write(root, path, value):
    destination = root / path
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(value, encoding="utf-8")


@pytest.fixture
def source_tree(tmp_path):
    write(tmp_path, FACTORIES["llm"], '''raise AssertionError("must not execute registry modules")
LLMFactory.register("openai", OpenAILLMProvider)
try:
    LLMFactory.register("groq", GroqLLMProvider)
except ImportError:
    pass
''')
    write(tmp_path, FACTORIES["stt"], '''STTFactory.register("deepgram-flux", DeepgramFluxSTTProvider)
STTFactory.register("flux", DeepgramFluxSTTProvider)
STTFactory.register("assemblyai", AssemblyAISTTProvider)
''')
    write(tmp_path, FACTORIES["tts"], 'TTSFactory.register("cartesia", CartesiaTTSProvider)\n')
    write(tmp_path, "backend/app/infrastructure/telephony/provider_factory.py", '''class TelephonyProviderFactory:
    @classmethod
    def list_providers(cls):
        return ["auto", "sip", "vonage", "twilio"]
''')
    write(tmp_path, "backend/app/infrastructure/connectors/email/gmail.py", '''class GmailConnector:
    @property
    def oauth_scopes(self):
        return ["https://www.googleapis.com/auth/gmail.send", "https://www.googleapis.com/auth/gmail.readonly"]
    async def get_email(self, message_id):
        response = await client.get("https://gmail.googleapis.com/message", params={"format": "full"})
        return self._parse_message(response.json())
    def _parse_message(self, data):
        return EmailMessage(body=data["payload"], body_html=data["payload"])
ConnectorFactory.register("gmail", GmailConnector)
''')
    write(tmp_path, "backend/app/infrastructure/connectors/drive/google_drive.py", '''class GoogleDriveConnector:
    @property
    def oauth_scopes(self):
        return ["https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive.readonly"]
ConnectorFactory.register("google_drive", GoogleDriveConnector)
''')
    write(tmp_path, "backend/app/infrastructure/connectors/email/smtp.py", '# ConnectorFactory.register("smtp", SMTPConnector)\n')
    write(tmp_path, "Talk-Leee/src/app/privacy/privacy-content.ts", '''export const statements = [
"Gmail metadata — sender, subject, and date only; never email body content",
"We do not access Google Drive files other than those created by Talk-Lee",
];
''')
    return tmp_path


def test_registration_names_aliases_and_conditional_source_are_preserved(generator, source_tree):
    report = generator.build_inventory(source_tree)
    records = {(row["factory"], row["identifier"]): row for row in report["registrations"]}
    assert ("LLMFactory", "openai") in records
    assert records[("LLMFactory", "groq")]["conditional"] is True
    assert records[("LLMFactory", "openai")]["conditional"] is False
    assert {"flux", "deepgram-flux"} <= {key[1] for key in records}
    assert all(row["source"] and isinstance(row["line"], int) for row in records.values())
    deepgram = next(row for row in report["suppliers"] if row["name"] == "Deepgram")
    assert {"flux", "deepgram-flux"} <= set(deepgram["identifiers"])
    assert report["unresolved"] == []


def test_comments_and_docstring_examples_are_not_real_registrations(generator, source_tree):
    with (source_tree / FACTORIES["llm"]).open("a", encoding="utf-8") as output:
        output.write('\n"""LLMFactory.register("invented-example", Example)"""\n')
    report = generator.build_inventory(source_tree)
    ids = {row["identifier"] for row in report["registrations"]}
    assert "smtp" not in ids and "invented-example" not in ids


def test_auto_and_self_hosted_sip_are_not_invented_legal_entities(generator, source_tree):
    report = generator.build_inventory(source_tree)
    ids = {row["identifier"] for row in report["registrations"]}
    assert {"auto", "sip", "vonage", "twilio"} <= ids
    names = {row["name"] for row in report["suppliers"]}
    assert {"Vonage", "Twilio"} <= names
    assert "auto" not in names and "SIP" not in names and "BlazeDigitel" not in names
    assert "carrier" in generator.render_markdown(report).lower()


@pytest.mark.parametrize("statement", [
    'LLMFactory.register("new-provider", NewProvider)',
    'LLMFactory.register(provider_from_runtime, RuntimeProvider)',
])
def test_unknown_or_dynamic_registration_cannot_silently_escape_review(generator, source_tree, statement):
    with (source_tree / FACTORIES["llm"]).open("a", encoding="utf-8") as output:
        output.write(statement + "\n")
    report = generator.build_inventory(source_tree)
    assert report["unresolved"]
    assert all(row["source"] and row["line"] for row in report["unresolved"])


def test_missing_registry_file_fails_closed(generator, source_tree):
    (source_tree / FACTORIES["tts"]).unlink()
    with pytest.raises(ValueError):
        generator.build_inventory(source_tree)


def test_read_full_gmail_and_drive_readonly_disagreements_have_exact_sources(generator, source_tree):
    report = generator.build_inventory(source_tree)
    differences = {row["id"]: row for row in report["policy_discrepancies"]}
    assert {"gmail_body_access", "drive_read_scope"} <= differences.keys()
    assert "gmail.py" in str(differences["gmail_body_access"]["code_evidence"])
    assert "privacy-content.ts" in str(differences["gmail_body_access"]["policy_evidence"])
    assert "drive.readonly" in str(differences["drive_read_scope"])
    text = generator.render_markdown(report).lower()
    assert "body retention" in text and "not established" in text


def test_corrected_policy_text_does_not_keep_a_false_disagreement(generator, source_tree):
    write(source_tree, "Talk-Leee/src/app/privacy/privacy-content.ts",
          'export const statements = ["Connected Gmail messages may include bodies; Drive access requires scope review."];\n')
    assert generator.build_inventory(source_tree)["policy_discrepancies"] == []


def test_rendering_is_deterministic_and_does_not_infer_contracts_or_active_use(generator, source_tree):
    first = generator.render_markdown(generator.build_inventory(source_tree))
    second = generator.render_markdown(generator.build_inventory(source_tree))
    assert first == second
    assert "not proof of deployment" in first.lower()
    assert "legal entity" in first.lower() and "owner/legal" in first.lower()
    assert "DPA" in first and "region" in first.lower()


def test_generator_never_imports_app_or_reads_environment_or_dotenv(generator, source_tree, monkeypatch):
    original_import = builtins.__import__
    original_open = Path.open

    def guarded_import(name, *args, **kwargs):
        assert name != "app" and not name.startswith("app.")
        return original_import(name, *args, **kwargs)

    def guarded_open(path, *args, **kwargs):
        assert not path.name.startswith(".env"), "Inventory must not read dotenv files"
        return original_open(path, *args, **kwargs)

    def no_getenv(*args, **kwargs):
        raise AssertionError("Inventory must not read environment configuration")

    with monkeypatch.context() as guarded:
        guarded.setattr(builtins, "__import__", guarded_import)
        guarded.setattr(Path, "open", guarded_open)
        guarded.setattr(os, "getenv", no_getenv)
        fresh_spec = importlib.util.spec_from_file_location("round3_subprocessor_no_app_import", SCRIPT)
        fresh_module = importlib.util.module_from_spec(fresh_spec)
        fresh_spec.loader.exec_module(fresh_module)
        assert fresh_module.build_inventory(source_tree)["registrations"]


def test_default_cli_is_read_only_and_write_targets_only_inventory_doc(generator, source_tree, capsys):
    policy = source_tree / "Talk-Leee/src/app/privacy/privacy-content.ts"
    before = policy.read_bytes()
    assert generator.main(["--root", str(source_tree)]) == 0
    assert "OpenAI" in capsys.readouterr().out
    assert not (source_tree / DOC).exists()
    assert generator.main(["--root", str(source_tree), "--write"]) == 0
    assert (source_tree / DOC).is_file()
    assert policy.read_bytes() == before


def test_check_detects_registry_growth_and_stale_document(generator, source_tree):
    assert generator.main(["--root", str(source_tree), "--write"]) == 0
    assert generator.main(["--root", str(source_tree), "--check"]) == 0
    with (source_tree / FACTORIES["llm"]).open("a", encoding="utf-8") as output:
        output.write('LLMFactory.register("deepseek", DeepSeekLLMProvider)\n')
    assert generator.main(["--root", str(source_tree), "--check"]) == 1


def test_unresolved_registration_refuses_write_without_overwriting_reviewed_document(generator, source_tree):
    write(source_tree, DOC, "reviewed existing inventory\n")
    with (source_tree / FACTORIES["llm"]).open("a", encoding="utf-8") as output:
        output.write('LLMFactory.register("new-vendor", FutureProvider)\n')
    assert generator.main(["--root", str(source_tree), "--write"]) != 0
    assert (source_tree / DOC).read_text(encoding="utf-8") == "reviewed existing inventory\n"


def test_real_registry_has_each_current_family_without_certifying_unregistered_smtp(generator):
    report = generator.build_inventory(ROOT)
    identities = {(row["factory"], row["identifier"]) for row in report["registrations"]}
    assert {("LLMFactory", "openai"), ("LLMFactory", "groq"), ("LLMFactory", "cerebras"),
            ("STTFactory", "assemblyai"), ("TTSFactory", "elevenlabs"),
            ("ConnectorFactory", "salesforce"), ("ConnectorFactory", "outlook_calendar"),
            ("TelephonyProviderFactory", "twilio")} <= identities
    assert ("ConnectorFactory", "smtp") not in identities
    assert report["unresolved"] == [], "New registry supplier/expression needs individual review"
