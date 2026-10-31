"""The device fingerprint must identify the browser install, not the request
type — otherwise it flaps per request and cannot detect hijacking."""
from __future__ import annotations

from starlette.requests import Request

from app.core.security.device_fingerprint import (
    FINGERPRINT_VERSION_PREFIX,
    generate_device_fingerprint,
    is_legacy_fingerprint,
)

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36"


def _request(headers: dict[str, str]) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    scope = {"type": "http", "method": "GET", "path": "/", "headers": raw, "query_string": b""}
    return Request(scope)


_BASE = {
    "User-Agent": _UA,
    "Accept-Language": "en-GB,en;q=0.9",
    "Sec-Ch-Ua": '"Chromium";v="128"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}


def test_navigation_fetch_audio_and_eventsource_share_one_fingerprint():
    navigation = _request({**_BASE, "Accept": "text/html,*/*", "Accept-Encoding": "gzip, deflate, br", "Sec-Fetch-Dest": "document", "Sec-Fetch-Mode": "navigate"})
    xhr = _request({**_BASE, "Accept": "application/json", "Accept-Encoding": "gzip", "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors"})
    audio = _request({**_BASE, "Accept": "*/*", "Accept-Encoding": "identity;q=1, *;q=0", "Sec-Fetch-Dest": "audio", "Sec-Fetch-Mode": "no-cors", "DNT": "1"})
    sse = _request({**_BASE, "Accept": "text/event-stream", "Sec-Fetch-Dest": "empty", "Sec-Ch-Ua-Platform-Version": '"15.0.0"'})
    fps = {generate_device_fingerprint(r) for r in (navigation, xhr, audio, sse)}
    assert len(fps) == 1, fps


def test_a_different_browser_or_platform_changes_the_fingerprint():
    base = generate_device_fingerprint(_request(_BASE))
    other_ua = generate_device_fingerprint(_request({**_BASE, "User-Agent": "Mozilla/5.0 (iPhone) Safari/605"}))
    other_platform = generate_device_fingerprint(_request({**_BASE, "Sec-Ch-Ua-Platform": '"Android"'}))
    assert base != other_ua and base != other_platform


def test_fingerprints_are_versioned_and_legacy_values_are_detected():
    fp = generate_device_fingerprint(_request(_BASE))
    assert fp.startswith(FINGERPRINT_VERSION_PREFIX) and len(fp) == len(FINGERPRINT_VERSION_PREFIX) + 64
    assert is_legacy_fingerprint("a" * 64) is True      # pre-2026-09-07 bare sha256
    assert is_legacy_fingerprint(fp) is False
    assert is_legacy_fingerprint(None) is False
    assert is_legacy_fingerprint("") is False


def test_session_validator_rebinds_legacy_fingerprints_instead_of_flagging():
    import inspect

    from app.core.security.sessions import lifecycle

    src = inspect.getsource(lifecycle)
    assert "is_legacy_fingerprint(session.get(\"device_fingerprint\"))" in src
    assert "session_fingerprint_rebound_to_v2" in src
