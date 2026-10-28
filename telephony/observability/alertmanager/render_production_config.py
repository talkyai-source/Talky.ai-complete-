"""Render the existing routing with approved delivery and durable incident ingress.

No network calls, deployment, credential generation, or alert evaluation. Requires
the repository's existing PyYAML dependency. Native validation is a separate gate.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
from urllib.parse import urlsplit

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASELINE = HERE / "alertmanager.yml"
NAMES = {"telephony-default", "telephony-critical", "telephony-warning"}
EXTERNAL_ROOT = PurePosixPath("/etc/alertmanager/external")
TOKEN_PATH = "/etc/alertmanager/secrets/ingest_token"
FILE_REQUIREMENTS = {
    "webhook_configs": ("url_file",),
    "slack_configs": ("api_url_file",),
    "pagerduty_configs": ("routing_key_file", "service_key_file"),
    "opsgenie_configs": ("api_key_file",),
    "email_configs": (),
}


class ConfigError(ValueError):
    """Messages must never include supplied values, URLs, or credential bytes."""


class UniqueKeyLoader(yaml.SafeLoader):
    """Refuse ambiguous native configuration instead of silently replacing keys."""


def _unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str) or key in result:
            raise ConfigError("Duplicate or invalid configuration key.")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping
)


def outside_checkout(path: Path) -> Path:
    if not path.is_absolute():
        raise ConfigError("Operator paths must be absolute.")
    resolved = path.resolve()
    if resolved.is_relative_to(ROOT):
        raise ConfigError(
            "Operator configuration, output and secrets must be outside the checkout."
        )
    # Linked worktrees have a .git file: reject any enclosing checkout too.
    if any((parent / ".git").exists() for parent in [resolved, *resolved.parents]):
        raise ConfigError(
            "Operator configuration, output and secrets must be outside version control."
        )
    return resolved


def valid_url(value: object, *, ingestion: bool = False) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(c.isspace() for c in value)
    ):
        raise ConfigError("A nonempty URL without whitespace is required.")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ConfigError("Malformed URL.") from None
    host = (parsed.hostname or "").lower()
    placeholders = ("example.com", "example.org", "example.net", "your-domain.com")
    if not host or any(
        host == item or host.endswith("." + item) for item in placeholders
    ):
        raise ConfigError(
            "An approved real destination is required; examples are not destinations."
        )
    if any(
        marker in value.lower() for marker in ("${", "<", ">", "changeme", "replace_me")
    ):
        raise ConfigError("Unresolved destination placeholder.")
    if parsed.username or parsed.password or parsed.fragment:
        raise ConfigError("URL userinfo and fragments are forbidden.")
    local_ingress = ingestion and host in {"127.0.0.1", "localhost", "::1"}
    if parsed.scheme != "https" and not (local_ingress and parsed.scheme == "http"):
        raise ConfigError("HTTPS is required except for same-host loopback ingestion.")
    if ingestion and (parsed.path != "/api/v1/webhooks/alertmanager" or parsed.query):
        raise ConfigError(
            "The ingestion URL must use the exact webhook path with no query."
        )
    if port == 0:
        raise ConfigError("Invalid URL port.")
    return value


def external_file(value: object, directory: Path, *, is_url: bool = False) -> None:
    if not isinstance(value, str) or ".." in PurePosixPath(value).parts:
        raise ConfigError(
            "External credential paths must stay inside their mounted directory."
        )
    path = PurePosixPath(value)
    if not path.is_relative_to(EXTERNAL_ROOT) or path == EXTERNAL_ROOT:
        raise ConfigError(
            "External credentials must use /etc/alertmanager/external/ files."
        )
    relative = path.relative_to(EXTERNAL_ROOT)
    local = (directory / str(relative)).resolve()
    if (
        not local.is_relative_to(directory)
        or not local.is_file()
        or local.stat().st_size == 0
    ):
        raise ConfigError(
            "A required nonempty mounted credential file is missing or escapes its directory."
        )
    if is_url:
        try:
            valid_url(local.read_text(encoding="utf-8").strip())
        except (OSError, UnicodeError):
            raise ConfigError("Could not validate a destination URL file.") from None


def validate_fields(value: object, directory: Path) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ConfigError("Configuration keys must be strings.")
            if key.endswith("_file"):
                external_file(
                    item,
                    directory,
                    is_url=key in {"url_file", "api_url_file", "webhook_url_file"},
                )
            elif key in {
                "credentials",
                "api_key",
                "routing_key",
                "service_key",
                "api_url",
                "webhook_url",
                "url",
            } or any(word in key.lower() for word in ("password", "secret", "token")):
                raise ConfigError(
                    "Inline credentials and secret URLs are forbidden; use native file fields."
                )
            elif key in {"http_headers", "proxy_connect_header", "proxy_url"}:
                raise ConfigError(
                    "Custom header or proxy credentials are outside this deployment contract."
                )
            elif key == "insecure_skip_verify" and item is not False:
                raise ConfigError("TLS certificate verification must remain enabled.")
            else:
                validate_fields(item, directory)
    elif isinstance(value, list):
        for item in value:
            validate_fields(item, directory)
    elif isinstance(value, str) and any(
        marker in value.lower()
        for marker in ("${", "changeme", "replace_me", "your-domain.com")
    ):
        raise ConfigError("Unresolved configuration placeholder.")


def build_config(
    approved: object, ingestion_url: str, external_directory: Path
) -> dict:
    valid_url(ingestion_url, ingestion=True)
    if not isinstance(approved, dict) or set(approved) != {"receivers"}:
        raise ConfigError("Approved input must contain only the native receivers list.")
    receivers = approved["receivers"]
    if not isinstance(receivers, list) or len(receivers) != len(NAMES):
        raise ConfigError(
            "All three named receivers must have approved external delivery."
        )
    if any(not isinstance(item, dict) for item in receivers):
        raise ConfigError("Invalid receiver definition.")
    if (
        any(not isinstance(item.get("name"), str) for item in receivers)
        or {item["name"] for item in receivers} != NAMES
    ):
        raise ConfigError(
            "Receiver names must exactly match the three baseline receivers."
        )
    result = yaml.safe_load(BASELINE.read_text(encoding="utf-8"))
    if {item["name"] for item in result["receivers"]} != NAMES:
        raise ConfigError(
            "Baseline receiver contract changed; review renderer before proceeding."
        )
    # Native YAML anchors may share integration lists across receivers. Each
    # receiver gets its own augmentation so adding ingress cannot alter the
    # next receiver's operator input or duplicate its deliveries.
    result["receivers"] = [copy.deepcopy(receiver) for receiver in receivers]
    for receiver in result["receivers"]:
        integrations = set(receiver) - {"name"}
        if not integrations or not integrations <= FILE_REQUIREMENTS.keys():
            raise ConfigError(
                "Every receiver needs a supported approved native integration."
            )
        for kind in integrations:
            configs = receiver[kind]
            if (
                not isinstance(configs, list)
                or not configs
                or any(not isinstance(item, dict) for item in configs)
            ):
                raise ConfigError(
                    "External integrations must be nonempty native configuration lists."
                )
            for config in configs:
                if FILE_REQUIREMENTS[kind] and not any(
                    config.get(key) for key in FILE_REQUIREMENTS[kind]
                ):
                    raise ConfigError(
                        "External destination credentials must use the integration's native file field."
                    )
                if kind == "email_configs":
                    if any(
                        not isinstance(config.get(key), str) or not config[key].strip()
                        for key in ("to", "from", "smarthost")
                    ):
                        raise ConfigError(
                            "Email requires explicit approved to, from and smarthost fields."
                        )
                    if any(
                        "@" not in config[key]
                        or re.search(
                            r"@(example\.(com|org|net)|your-domain\.com)\b",
                            config[key],
                            re.I,
                        )
                        for key in ("to", "from")
                    ):
                        raise ConfigError(
                            "Example email addresses are not approved recipients."
                        )
                    if config.get("require_tls") is False:
                        raise ConfigError("External email delivery must require TLS.")
                    config["require_tls"] = True
                validate_fields(config, external_directory)
                if kind != "email_configs":
                    http = config.setdefault("http_config", {})
                    if not isinstance(http, dict):
                        raise ConfigError("Invalid native HTTP configuration.")
                    http["follow_redirects"] = False
                    tls = http.get("tls_config", {})
                    if not isinstance(tls, dict) or tls.get("insecure_skip_verify"):
                        raise ConfigError(
                            "TLS certificate verification must remain enabled."
                        )
        receiver.setdefault("webhook_configs", []).append(
            {
                "url": ingestion_url,
                "send_resolved": True,
                # Truncation is data loss, not pagination. Backend rejects oversize
                # groups explicitly; tune trusted grouping if its bound is exceeded.
                "max_alerts": 0,
                "http_config": {
                    "follow_redirects": False,
                    "authorization": {"type": "Bearer", "credentials_file": TOKEN_PATH},
                },
            }
        )
    return result


def render(
    approved_path: Path,
    output: Path,
    ingestion_url: str,
    external_directory: Path,
    token_file: Path,
    amtool: str | None = None,
) -> str:
    approved_path, output, external_directory, token_file = map(
        outside_checkout, (approved_path, output, external_directory, token_file)
    )
    if output in {approved_path, token_file} or output.is_relative_to(
        external_directory
    ):
        raise ConfigError(
            "Output must not overwrite operator input or credential files."
        )
    try:
        token = token_file.read_text(encoding="utf-8").strip()
        if not 32 <= len(token) <= 256 or any(not 33 <= ord(c) <= 126 for c in token):
            raise ConfigError(
                "Ingestion credential must contain 32–256 printable ASCII nonwhitespace characters."
            )
        approved = yaml.load(
            approved_path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader
        )
    except (OSError, UnicodeError, yaml.YAMLError):
        raise ConfigError(
            "Could not read valid operator configuration or ingestion credential."
        ) from None
    config = build_config(approved, ingestion_url, external_directory)
    raw = yaml.safe_dump(config, sort_keys=False).encode("utf-8")
    if not output.parent.is_dir():
        raise ConfigError("Create the protected output directory before rendering.")
    temporary: str | None = None
    try:
        fd, temporary = tempfile.mkstemp(
            prefix=".alertmanager-", suffix=".yml", dir=output.parent
        )
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if amtool:
            version = subprocess.run(
                [amtool, "--version"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if version.returncode or not re.search(
                r"version\s+0\.27\.0(?:\s|$)", version.stdout + version.stderr
            ):
                raise ConfigError("Native validation requires amtool version 0.27.0.")
            checked = subprocess.run(
                [amtool, "check-config", temporary],
                capture_output=True,
                timeout=30,
                check=False,
            )
            if checked.returncode:
                raise ConfigError(
                    "Pinned native validation failed; inspect locally without publishing destination or secret output."
                )
        os.replace(temporary, output)
        temporary = None
    finally:
        if temporary:
            Path(temporary).unlink(missing_ok=True)
    return hashlib.sha256(raw).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-receivers", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--ingestion-url", required=True)
    parser.add_argument("--external-secrets-dir", required=True, type=Path)
    parser.add_argument("--ingest-token-file", required=True, type=Path)
    parser.add_argument(
        "--amtool",
        help="Optional already-installed amtool 0.27.0; no binary is downloaded",
    )
    args = parser.parse_args()
    try:
        digest = render(
            args.approved_receivers,
            args.output,
            args.ingestion_url,
            args.external_secrets_dir,
            args.ingest_token_file,
            args.amtool,
        )
    except ConfigError as exc:
        print(f"Render refused: {exc} Existing output was preserved.")
        return 1
    except (OSError, subprocess.SubprocessError):
        # No third-party parser/subprocess exception text: it can contain secrets.
        print(
            "Render refused: invalid/unavailable operator input, unsafe output, or failed native validation. Existing output was preserved."
        )
        return 1
    print(f"Rendered configuration SHA256: {digest}")
    print(
        "Native amtool 0.27.0 validation passed."
        if args.amtool
        else "NOT natively validated. Exact-image validation and recipient acceptance remain required before deployment."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
