"""Secret-input and pre-write boundaries for the initial-admin operator CLI."""
import importlib.util
import os
from pathlib import Path
import sys
from dataclasses import replace

import pyotp
import pytest
from cryptography.fernet import Fernet

SPEC = importlib.util.spec_from_file_location("first_admin_cli", Path(__file__).resolve().parents[2] / "scripts/bootstrap_first_admin.py")
bootstrap = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = bootstrap
SPEC.loader.exec_module(bootstrap)


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", Fernet.generate_key().decode())
    secret = pyotp.random_base32()
    return bootstrap.Credentials("Operator@Example.com", "First Operator", "Synthetic first admin password", secret, pyotp.TOTP(secret).now(), True)


@pytest.mark.parametrize("changes", [
    {"owned_email_confirmed": False}, {"email": "invalid"}, {"name": " "},
    {"password": "short"}, {"password": "a" * 129},
    {"totp_secret": "A" * 16}, {"totp_secret": "A" * 33}, {"totp_secret": "not-base32"},
    {"totp_code": "not-a-code"},
])
def test_invalid_credentials_rejected_before_database_work(credentials, changes):
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.prepare_credentials(replace(credentials, **changes))


def test_normal_crypto_and_consumed_totp_step(credentials):
    from app.core.security.password import verify_password
    from app.core.security.totp import decrypt_totp_secret, verify_totp_step
    email, password_hash, ciphertext, matched = bootstrap.prepare_credentials(credentials)
    assert email == "operator@example.com"
    assert password_hash.startswith("$argon2id$")
    assert verify_password(credentials.password, password_hash)
    assert ciphertext != credentials.totp_secret
    assert decrypt_totp_secret(ciphertext) == credentials.totp_secret
    assert verify_totp_step(credentials.totp_secret, credentials.totp_code, last_used_at=matched) is None
    assert credentials.password not in repr(credentials)
    assert credentials.totp_secret not in repr(credentials)


def test_bad_encryption_key_never_in_error(credentials, monkeypatch):
    marker = "secret-key-marker-do-not-print"
    monkeypatch.setenv("TOTP_ENCRYPTION_KEY", marker)
    with pytest.raises(bootstrap.BootstrapError) as caught:
        bootstrap.prepare_credentials(credentials)
    assert marker not in str(caught.value)


@pytest.mark.parametrize("url", [
    "postgresql://owner@db:5432/talky",
    "postgresql://owner@db:5432/talky?sslmode=require&sslrootcert=/ca.pem",
    "postgresql://owner@db:5432/talky?sslmode=verify-full&sslrootcert=ca.pem",
    "postgresql://owner@db:5432/talky?sslmode=verify-full&sslmode=disable&sslrootcert=/ca.pem",
    "postgresql://owner@db:5432/talky?sslmode=verify-full&sslrootcert=/ca.pem&host=other",
    "postgresql://owner@db:5432/talky?sslmode=verify-full&sslrootcert=/ca.pem#other",
    "postgresql://owner@db/talky?sslmode=verify-full&sslrootcert=/ca.pem",
    "postgresql://owner@db:5432/?sslmode=verify-full&sslrootcert=/ca.pem",
    "postgresql://db:5432/talky?sslmode=verify-full&sslrootcert=/ca.pem",
])
def test_transport_downgrade_or_ambiguous_target_is_rejected(url):
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.validate_database_url(url)


def test_verified_tls_url_and_explicit_passfile_are_supported(tmp_path):
    from urllib.parse import urlencode
    query = urlencode({"sslmode": "verify-full", "sslrootcert": str(tmp_path / "ca.pem"), "passfile": str(tmp_path / "pgpass")})
    bootstrap.validate_database_url("postgresql://owner@db:5432/talky?" + query)


def test_redirected_input_never_falls_back_to_echo(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(bootstrap.getpass, "getpass", lambda _: pytest.fail("must not read secret"))
    with pytest.raises(bootstrap.BootstrapError, match="private terminal"):
        bootstrap.hidden_input("Password: ")


def test_terminal_echo_warning_is_fatal(monkeypatch):
    import warnings
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
    def echo_unavailable(_):
        warnings.warn("Cannot control echo", bootstrap.getpass.GetPassWarning)
        pytest.fail("must not continue to fallback input")
    monkeypatch.setattr(bootstrap.getpass, "getpass", echo_unavailable)
    with pytest.raises(bootstrap.BootstrapError, match="non-echoing"):
        bootstrap.hidden_input("Password: ")


def test_driver_error_and_url_are_never_printed(monkeypatch, capsys, tmp_path):
    from urllib.parse import urlencode
    marker = "secret-database-password-do-not-print"
    dsn = f"postgresql://owner:{marker}@db:5432/talky?" + urlencode({
        "sslmode": "verify-full", "sslrootcert": str(tmp_path / "ca.pem")})
    monkeypatch.setattr(bootstrap, "read_private_file", lambda path, **kwargs:
        "JWT_SECRET=selected-profile-key\nTOTP_ENCRYPTION_KEY=selected-profile-key\n"
        if str(path) == "application.env" else dsn)
    monkeypatch.setattr(bootstrap, "configure_environment", lambda _: None)
    async def fail(*_):
        raise RuntimeError("Connection rejected password/hash: " + dsn)
    monkeypatch.setattr(bootstrap, "run", fail)
    assert bootstrap.main(["--env-file", "application.env", "--database-url-file", "private-url",
        "--expected-database", "talky", "--expected-owner", "owner",
        "--email", "operator@example.com", "--name", "Operator"]) == 1
    output = capsys.readouterr()
    assert marker not in output.out + output.err
    assert dsn not in output.out + output.err
    assert "sensitive error detail withheld" in output.err


def test_selected_profile_excludes_ambient_audit_and_pg_keys(monkeypatch, tmp_path):
    from app.core.config import Settings, get_settings
    from app.domain.services.audit_logger import AuditLogger
    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.setenv("AUDIT_LOG_SIGNING_KEY", "unrelated-ambient-audit-key")
    monkeypatch.setenv("APP_AUDIT_LOG_SIGNING_KEY", "unrelated-ambient-fallback")
    monkeypatch.setenv("PGPASSWORD", "unrelated-ambient-database-key")
    implicit = tmp_path / ".env"
    implicit.write_text("AUDIT_LOG_SIGNING_KEY=unrelated-implicit-file-key\n")
    monkeypatch.setitem(Settings.model_config, "env_file", str(implicit))
    bootstrap.configure_environment({"JWT_SECRET": "selected-application-jwt-signing-key",
        "TOTP_ENCRYPTION_KEY": Fernet.generate_key().decode(), "ENVIRONMENT": "test",
        "PYTHON_DOTENV_DISABLED": "0", "PGOPTIONS": "unwanted-connection-options"})
    try:
        assert AuditLogger(None).signing_key == "selected-application-jwt-signing-key"
        assert not any(key.startswith("PG") for key in os.environ)
        assert os.environ["PYTHON_DOTENV_DISABLED"] == "1"
        assert Settings.model_config["env_file"] is None
    finally:
        get_settings.cache_clear()


@pytest.mark.skipif(os.name != "posix", reason="Unix permissions are enforced by the Linux-only CLI")
def test_private_input_refuses_world_readable_files_and_symlinks(tmp_path):
    secret = tmp_path / "input"
    secret.write_text("private marker")
    secret.chmod(0o644)
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.read_private_file(secret)
    secret.chmod(0o600)
    assert bootstrap.read_private_file(secret) == "private marker"
    link = tmp_path / "link"
    link.symlink_to(secret)
    with pytest.raises(bootstrap.BootstrapError):
        bootstrap.read_private_file(link)
