"""Offline, fixed dependency-overlay runner for the bounded auth audit tests."""
import importlib.metadata
import os
from pathlib import Path
import socket
import sys
import threading

ROOT = Path(__file__).resolve().parents[4]
for key in list(os.environ):
    if key.startswith(("STRIPE_", "DEEPGRAM_", "GROQ_", "CARTESIA_", "VONAGE_", "SUPABASE_")) or key in {"DATABASE_URL", "TEST_DATABASE_URL", "REDIS_URL", "JWT_SECRET", "JWT_SECRET_KEY"}:
        os.environ.pop(key, None)
os.environ.update(
    ENVIRONMENT="test",
    JWT_SECRET="ci-test-secret-key-not-for-production-32chars",
    DEEPGRAM_API_KEY="test_key",
    GROQ_API_KEY="test_key",
    CARTESIA_API_KEY="test_key",
    VONAGE_API_KEY="test_key",
    VONAGE_API_SECRET="test_secret",
    STRIPE_BILLING_DISABLED="true",
)

def no_network(*args, **kwargs):
    raise RuntimeError("Network is disabled for the Admin receipt fixture run")

_connect = socket.socket.connect
_socketpair = socket.socketpair
_internal = threading.local()

def internal_socketpair(*args, **kwargs):
    # Windows asyncio's local wakeup pipe is implemented with socketpair.
    _internal.socketpair = True
    try:
        return _socketpair(*args, **kwargs)
    finally:
        _internal.socketpair = False

def guarded_connect(*args, **kwargs):
    if getattr(_internal, "socketpair", False):
        return _connect(*args, **kwargs)
    return no_network(*args, **kwargs)

socket.socketpair = internal_socketpair
socket.socket.connect = guarded_connect
socket.getaddrinfo = no_network
os.chdir(ROOT / "backend")
sys.path.insert(0, str(ROOT / "backend"))
print("Offline synthetic Admin receipt validation; no database or provider access", flush=True)
print("Overlay versions: " + ", ".join(f"{name}={importlib.metadata.version(name)}" for name in ("PyJWT", "urllib3", "fastapi", "pytest")), flush=True)
import pytest
raise SystemExit(pytest.main([
    "tests/unit/test_ag06_admin_receipt_projection.py",
    "tests/unit/test_ag06_admin_actions.py",
    "tests/unit/test_ag06_dashboard_actions.py",
    "-q", "-rs",
]))
