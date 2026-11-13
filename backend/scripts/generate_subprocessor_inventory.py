"""Generate legal review input from source without importing application code.

Default: Markdown to stdout. --write updates only the fixed inventory document;
--check refuses missing/stale documentation or unresolved registration names.
No settings, environment, provider imports or network access are used.
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path
import sys


DOCUMENT = Path("backend/docs/legal_subprocessor_inventory.md")
_FACTORIES = {
    "LLMFactory": "backend/app/infrastructure/llm/factory.py",
    "STTFactory": "backend/app/infrastructure/stt/factory.py",
    "TTSFactory": "backend/app/infrastructure/tts/factory.py",
}
_TELEPHONY = "backend/app/infrastructure/telephony/provider_factory.py"
_CONNECTORS = "backend/app/infrastructure/connectors"
_POLICY = "Talk-Leee/src/app/privacy/privacy-content.ts"
_GMAIL = f"{_CONNECTORS}/email/gmail.py"
_DRIVE = f"{_CONNECTORS}/drive/google_drive.py"
_PROBES = "backend/app/domain/services/provider_key_validation.py"
_NAMES = {
    "openai": "OpenAI", "groq": "Groq", "gemini": "Google",
    "cerebras": "Cerebras", "deepseek": "DeepSeek", "xai": "xAI",
    "anthropic": "Anthropic", "assemblyai": "AssemblyAI", "cartesia": "Cartesia",
    "elevenlabs": "ElevenLabs", "google": "Google", "google-streaming": "Google",
    "hubspot": "HubSpot", "salesforce": "Salesforce", "gmail": "Google",
    "google_calendar": "Google", "google_drive": "Google",
    "outlook_calendar": "Microsoft (Graph)", "vonage": "Vonage", "twilio": "Twilio",
    **{alias: "Deepgram" for alias in (
        "deepgram", "deepgram-flux", "deepgram_flux", "flux", "nova-2",
        "deepgram-nova", "deepgram_nova", "nova-3", "nova",
    )},
}
_DATA = {
    "LLMFactory": "Conversation text, prompts and requested model output",
    "STTFactory": "Call audio and transcription results",
    "TTSFactory": "Synthesis text and resulting audio",
    "TelephonyProviderFactory": "Call signalling, telephone identifiers and media",
    "ConnectorFactory": "Authorized connector account and selected CRM/calendar/mail/file data",
}
# Supplemental observations are deliberately separate from parsed registrations.
# Presence of a source seam is not evidence that its configuration is enabled.
_SUPPLEMENTAL = (
    ("Stripe", "backend/app/domain/services/billing_checkout.py", "import stripe",
     "Billing API integration; owner must establish the active merchant account and data/contract scope."),
    ("Configured SMTP provider", "backend/app/domain/services/email_service.py", 'SMTP_HOST =',
     "Account email sender; source default is Microsoft 365. Actual host/account and message retention require owner confirmation."),
    ("Slack", "backend/app/domain/services/notification_service.py", "async def send_slack",
     "Optional webhook notification seam; enabled use and payload/retention require owner confirmation."),
    ("Configured S3-compatible storage", "backend/app/domain/services/recording_service.py", '"service_name": "s3"',
     "Recording object storage supports a custom endpoint. Do not infer AWS from the S3 protocol."),
    ("AWS KMS", "backend/app/core/kms.py", 'boto3.client("kms"',
     "Optional key-management backend; deployment and region are not inspected."),
    ("Sentry", "backend/app/core/sentry_init.py", "sentry_sdk.init(",
     "Optional telemetry initialization; enabled deployment, redaction and retention require separate verification."),
    ("Cloudflare Turnstile", "backend/app/core/security/auth_abuse.py", "challenges.cloudflare.com/turnstile",
     "Optional CAPTCHA validation; deployment and transferred data require owner review."),
    ("Supabase compatibility", "backend/app/domain/services/recording_service.py", 'storage.from_("recordings")',
     "Compatibility storage path remains in source; neither active nor unused is established."),
    ("Legacy SendGrid helper", "backend/app/domain/services/notification_service.py", "async def _send_via_sendgrid",
     "Private legacy helper only. Current account-mail dispatch delegates to EmailService; this is not an active-supplier assertion."),
    ("Legacy AWS SES helper", "backend/app/domain/services/notification_service.py", "async def _send_via_ses",
     "Private legacy helper only; active account-mail routing must be verified independently."),
    ("Vercel deployment descriptor", "Admin/frontend/vercel.json", '"',
     "Repository hosting descriptor only; actual deployment, contracting entity and hosting scope are unverified."),
    ("Hetzner deployment documentation", "CLAUDE.md", "Production = Hetzner",
     "Repository environment claim only; confirm current host, legal entity, region and subcontractors with the owner."),
)


def _read(root: Path, relative: str) -> str:
    path = root / relative
    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError(f"Missing or out-of-root source: {relative}")
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError(f"Source exceeds inventory limit: {relative}")
    return path.read_text(encoding="utf-8-sig")


def _tree(root: Path, relative: str) -> ast.Module:
    try:
        return ast.parse(_read(root, relative), filename=relative)
    except SyntaxError:
        raise ValueError(f"Cannot parse source: {relative}") from None


def _evidence(source: str, line: int) -> dict:
    return {"source": source, "line": line}


def _record(records, unresolved, factory, identifier, source, line, conditional=False):
    row = {"factory": factory, "identifier": identifier, "conditional": conditional,
           **_evidence(source, line)}
    records.append(row)
    if identifier not in _NAMES and not (factory == "TelephonyProviderFactory" and identifier in {"auto", "sip"}):
        unresolved.append({**row, "reason": "Unknown or dynamic registration; review supplier mapping"})


def _registrations(tree, source, factories, records, unresolved):
    def visit(node, conditional=False):
        conditional = conditional or isinstance(node, (
            ast.Try, ast.TryStar, ast.If, ast.For, ast.While, ast.With,
            ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda,
        ))
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "register" and isinstance(node.func.value, ast.Name)
                and node.func.value.id in factories):
            name = node.args[0] if node.args else next((kw.value for kw in node.keywords if kw.arg == "name"), None)
            identifier = name.value if isinstance(name, ast.Constant) and isinstance(name.value, str) else "<dynamic>"
            _record(records, unresolved, node.func.value.id, identifier, source, node.lineno, conditional)
        for child in ast.iter_child_nodes(node):
            visit(child, conditional)
    visit(tree)


def _literal_lines(tree, value):
    return sorted({node.lineno for node in ast.walk(tree)
                   if isinstance(node, ast.Constant) and node.value == value})


def _policy_discrepancies(root):
    policy = _read(root, _POLICY).splitlines()
    gmail, drive = _tree(root, _GMAIL), _tree(root, _DRIVE)
    full = sorted({node.lineno for node in ast.walk(gmail) if isinstance(node, ast.Dict)
                   for key, value in zip(node.keys, node.values)
                   if isinstance(key, ast.Constant) and key.value == "format"
                   and isinstance(value, ast.Constant) and value.value == "full"})
    body = sorted({node.lineno for node in ast.walk(gmail)
                   if isinstance(node, ast.keyword) and node.arg in {"body", "body_html"}})
    readonly = _literal_lines(drive, "https://www.googleapis.com/auth/drive.readonly")
    gmail_policy = [i for i, line in enumerate(policy, 1) if "never email body content" in line.lower()]
    drive_policy = [i for i, line in enumerate(policy, 1)
                    if "drive" in line.lower() and ("other than those created by talk-lee" in line.lower()
                    or "files created by talk-lee only" in line.lower())]
    result = []
    if full and body and gmail_policy:
        result.append({"id": "gmail_body_access",
                       "description": "Policy says never email body content; get_email requests format=full and parses body/body_html. Metadata-only list previews do not remove this path.",
                       "code_evidence": [_evidence(_GMAIL, line) for line in sorted(set(full + body))],
                       "policy_evidence": [_evidence(_POLICY, line) for line in gmail_policy]})
    if readonly and drive_policy:
        result.append({"id": "drive_read_scope",
                       "description": "Policy limits access to app-created files; the requested drive.readonly scope permits reading beyond that set (alongside drive.file). Actual granted scopes are unverified.",
                       "code_evidence": [_evidence(_DRIVE, line) for line in readonly],
                       "policy_evidence": [_evidence(_POLICY, line) for line in drive_policy]})
    return result


def build_inventory(root: Path) -> dict:
    root = Path(root).resolve()
    records, unresolved = [], []
    for factory, source in _FACTORIES.items():
        before = len(records)
        _registrations(_tree(root, source), source, {factory}, records, unresolved)
        if len(records) == before:
            raise ValueError(f"No supported registration syntax in {source}")
    connectors = root / _CONNECTORS
    if not connectors.is_dir() or not connectors.resolve().is_relative_to(root):
        raise ValueError("Missing connector registry sources")
    sources = sorted(connectors.rglob("*.py"))
    if len(sources) > 200:
        raise ValueError("Connector source count exceeds inventory limit")
    before = len(records)
    for path in sources:
        source = path.relative_to(root).as_posix()
        _registrations(_tree(root, source), source, {"ConnectorFactory"}, records, unresolved)
    if len(records) == before:
        raise ValueError("No supported connector registrations")
    telephony = _tree(root, _TELEPHONY)
    lists = [node for cls in telephony.body if isinstance(cls, ast.ClassDef) and cls.name == "TelephonyProviderFactory"
             for method in cls.body if isinstance(method, ast.FunctionDef) and method.name == "list_providers"
             for node in ast.walk(method) if isinstance(node, ast.Return)]
    if not lists:
        raise ValueError("Missing telephony provider list")
    for statement in lists:
        elements = statement.value.elts if isinstance(statement.value, (ast.List, ast.Tuple)) else [statement.value]
        for value in elements:
            identifier = value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else "<dynamic>"
            _record(records, unresolved, "TelephonyProviderFactory", identifier, _TELEPHONY, statement.lineno)
    suppliers = {}
    for row in records:
        name = _NAMES.get(row["identifier"])
        if name:
            item = suppliers.setdefault(name, {"name": name, "identifiers": set(), "families": set()})
            item["identifiers"].add(row["identifier"])
            item["families"].add(row["factory"])
    supplemental = []
    for name, source, marker, note in _SUPPLEMENTAL:
        if (root / source).exists():
            lines = [i for i, text in enumerate(_read(root, source).splitlines(), 1) if marker in text]
            if lines:
                supplemental.append({"name": name, "note": note, **_evidence(source, lines[0])})
    # Credential validation is a separate fixed integration registry, not an
    # inference factory. Include it without importing or invoking any probe.
    if (root / _PROBES).exists():
        dictionaries = [node.value for node in _tree(root, _PROBES).body
                        if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == "_ENDPOINTS" for target in node.targets)]
        if len(dictionaries) != 1 or not isinstance(dictionaries[0], ast.Dict):
            unresolved.append({"source": _PROBES, "line": 1, "reason": "Unsupported credential-probe registry syntax"})
        else:
            for key in dictionaries[0].keys:
                identifier = key.value if isinstance(key, ast.Constant) and isinstance(key.value, str) else "<dynamic>"
                if identifier not in _NAMES:
                    unresolved.append({"source": _PROBES, "line": getattr(key, "lineno", 1),
                                       "reason": "Unknown credential-probe supplier"})
                else:
                    supplemental.append({"name": f"{_NAMES[identifier]} credential check ({identifier})",
                                         "note": "Fixed authenticated read probe; not evidence of registered inference, active deployment or call readiness.",
                                         **_evidence(_PROBES, key.lineno)})
    return {"registrations": sorted(records, key=lambda row: (row["factory"], row["identifier"], row["source"], row["line"])),
            "suppliers": [{"name": name, "identifiers": sorted(item["identifiers"]), "families": sorted(item["families"])}
                          for name, item in sorted(suppliers.items())],
            "supplemental": sorted(supplemental, key=lambda row: (row["name"], row["source"])),
            "unresolved": unresolved, "policy_discrepancies": _policy_discrepancies(root)}


def _link(row):
    source, line = row["source"], row["line"]
    return f"[{source}:{line}](../../{source}#L{line})"


def _cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict) -> str:
    lines = ["# Subprocessor inventory — source evidence for legal", "",
             "Generated from source registrations; **not proof of deployment** or contractual compliance. Supplier labels are product/vendor names, not verified legal entity names. Owner/legal review must establish active use, legal entity, DPA/SCC terms, subprocessors, data categories, region, retention and deletion arrangements before publication.", "",
             "## Registered supplier candidates", "",
             "| Supplier label | Registration identifiers | Families |", "|---|---|---|"]
    for row in report["suppliers"]:
        lines.append(f"| {_cell(row['name'])} | {', '.join(row['identifiers'])} | {', '.join(row['families'])} |")
    lines += ["", "## Registration evidence", "",
              "These are source registration sites, not a runtime registry dump. Conditional sites depend on imports/control flow; unconditional source registration also does not prove a provider is selected. `auto` is selection logic; `sip` is transport and requires an owner inventory of every actual carrier.", "",
              "| Factory / identifier | Conditional source site | Potential processing category | Evidence |", "|---|---|---|---|"]
    for row in report["registrations"]:
        category = "Selection mode / customer-configured carrier; no supplier inferred" if row["identifier"] in {"auto", "sip"} else _DATA[row["factory"]]
        lines.append(f"| {_cell(row['factory'] + ' / ' + row['identifier'])} | {'yes' if row['conditional'] else 'no'} | {category} | {_link(row)} |")
    lines += ["", "## Supplemental integrations outside those factories", "",
              "This is an explicitly bounded source review, not an exhaustive supplier registry. Presence below does not establish enablement. Hosting, customer-selected carriers, SMTP/storage endpoints, extensions and downstream sub-subprocessors require owner confirmation. A commented SMTP ConnectorFactory registration is not a registered connector.", "",
              "| Candidate / source seam | Review limitation | Evidence |", "|---|---|---|"]
    for row in report["supplemental"]:
        lines.append(f"| {_cell(row['name'])} | {_cell(row['note'])} | {_link(row)} |")
    lines += ["", "## Code and privacy-policy discrepancies", ""]
    if not report["policy_discrepancies"]:
        lines.append("No matching literal disagreement detected by these two narrow checks; this is not approval of the policy or evidence that all policy statements match the product.")
    for row in report["policy_discrepancies"]:
        lines += [f"- **{row['id']}**: {row['description']} Code: {', '.join(_link(ref) for ref in row['code_evidence'])}. Policy: {', '.join(_link(ref) for ref in row['policy_evidence'])}."]
    lines += ["", "Gmail body retention and downstream copies are **not established** by this inventory. Legal must review tool/conversation storage and actual granted OAuth scopes separately. The policy text is unchanged; neither scope reduction nor legal publication is performed here.", "",
              "## Unresolved registrations", ""]
    lines += ([f"- {_cell(row['reason'])}: {_link(row)}" for row in report["unresolved"]]
              or ["None within the parsed registration forms. New unknown/dynamic identities refuse document write/check until reviewed."])
    lines += ["", "## Reproduce and review", "", "From the repository root:", "", "```text",
              "python backend/scripts/generate_subprocessor_inventory.py",
              "python backend/scripts/generate_subprocessor_inventory.py --check",
              "python backend/scripts/generate_subprocessor_inventory.py --write", "```", "",
              "Default prints to stdout. --write changes only this document; --check detects drift. The generator uses Python AST and fixed source reads, never application imports, settings, dotenv, provider requests or subprocesses. No credentials are read or printed.", "",
              "Coverage limits: named register calls in the three factory files and connector Python modules, the literal telephony list, the optional fixed credential-check map, and the listed supplemental source seams. Aliased factories, runtime monkey patches, plugin discovery and arbitrary external services are not proven covered. Unknown values in recognized registrations fail closed; new integration mechanisms need a separate review. Data categories describe potential code functionality, not verified deployed flows.", "",
              "Owner/legal must reconcile this with the current privacy policy and Terms, including the QA12 questions about calling hours, recording defaults, billing timing, deletion/backups, DPA availability, trials and uptime. No DPA, SCC, residency, supplier contract, retention promise or production configuration has been verified by this tool."]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        report = build_inventory(root)
        rendered = render_markdown(report)
        if report["unresolved"]:
            for row in report["unresolved"]:
                print(f"Unresolved input: {row['source']}:{row['line']} ({row['reason']})", file=sys.stderr)
            if not (args.write or args.check):
                print(rendered, end="")
            return 1
        target = root / DOCUMENT
        if not target.resolve().is_relative_to(root) or target.is_symlink():
            raise ValueError("Inventory document target must remain inside repository")
        if args.check:
            if not target.is_file() or target.read_text(encoding="utf-8") != rendered:
                print("Inventory is missing or stale; review sources and run --write", file=sys.stderr)
                return 1
        elif args.write:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rendered, encoding="utf-8", newline="\n")
        else:
            print(rendered, end="")
        return 0
    except (OSError, ValueError) as exc:
        # ValueError messages are our source-path diagnostics, never source data.
        print(f"Inventory refused: {exc if isinstance(exc, ValueError) else type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
