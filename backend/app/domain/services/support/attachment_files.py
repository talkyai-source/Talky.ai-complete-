"""Attachment checks that need no I/O: type from content, safe names, signed links.

The type is decided by the file's bytes. The extension must agree with it, so
an HTML page renamed ``.png`` or a binary renamed ``.txt`` is refused.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import time
import unicodedata
from dataclasses import dataclass

from app.domain.services.support.errors import SupportUnavailable, SupportValidationError

_EXTENSIONS = {
    "image/png": (".png",),
    "image/jpeg": (".jpg", ".jpeg"),
    "image/webp": (".webp",),
    "application/pdf": (".pdf",),
    "text/plain": (".txt", ".log"),
    "text/csv": (".csv",),
}
_TEXT_EXTENSIONS = {".txt": "text/plain", ".log": "text/plain", ".csv": "text/csv"}
_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._ ()\-]+")
SIGNED_URL_TTL_SECONDS = 300


@dataclass(frozen=True)
class CheckedFile:
    filename: str
    content_type: str
    extension: str


class UnsupportedAttachment(SupportValidationError):
    status = 415
    code = "unsupported_attachment"
    message = "Attach a PNG, JPG, WEBP, PDF, TXT, LOG or CSV file."


def _sniff(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if _looks_like_text(data):
        return "text"
    return None


def _looks_like_text(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return False
    controls = sum(1 for char in text if ord(char) < 32 and char not in "\n\r\t\f")
    return controls <= max(2, len(text) // 1000)


def clean_filename(raw: str | None, extension: str) -> str:
    """Keep a readable base name; force the extension that matches the content."""
    name = os.path.basename((raw or "").replace("\\", "/"))
    name = unicodedata.normalize("NFKC", name)
    stem = os.path.splitext(name)[0]
    stem = _SAFE_CHARS.sub("_", stem).strip(" ._-")
    stem = re.sub(r"[ _]{2,}", "_", stem)[:100].rstrip(" ._-")
    return f"{stem or 'attachment'}{extension}"


def check_file(raw_name: str | None, data: bytes, *, max_bytes: int) -> CheckedFile:
    if not data:
        raise SupportValidationError("The file is empty.", code="empty_attachment")
    if len(data) > max_bytes:
        raise SupportValidationError(f"Files can be at most {max_bytes // (1024 * 1024)} MB.",
                                     code="attachment_too_large")
    extension = os.path.splitext((raw_name or "").lower())[1]
    detected = _sniff(data)
    if detected == "text":
        content_type = _TEXT_EXTENSIONS.get(extension)
    elif detected is not None and extension in _EXTENSIONS[detected]:
        content_type = detected
    else:
        content_type = None
    if content_type is None:
        raise UnsupportedAttachment()
    if extension == ".jpeg":
        extension = ".jpg"
    return CheckedFile(filename=clean_filename(raw_name, extension), content_type=content_type, extension=extension)


def _signing_key() -> bytes:
    from app.core.config import get_settings

    secret = get_settings().effective_jwt_secret
    if not secret:
        raise SupportUnavailable("Attachment links are not configured.", code="attachment_links_unavailable")
    return hmac.new(secret.encode(), b"talky-support-attachment-download-v1", hashlib.sha256).digest()


def sign_download(attachment_id: str, *, now: float | None = None) -> tuple[int, str]:
    expires = int((now if now is not None else time.time()) + SIGNED_URL_TTL_SECONDS)
    signature = hmac.new(_signing_key(), f"{attachment_id}:{expires}".encode(), hashlib.sha256).hexdigest()
    return expires, signature


def verify_download(attachment_id: str, expires: int, signature: str, *, now: float | None = None) -> bool:
    current = now if now is not None else time.time()
    if expires < current or expires > current + SIGNED_URL_TTL_SECONDS + 5:
        return False
    expected = hmac.new(_signing_key(), f"{attachment_id}:{expires}".encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")
