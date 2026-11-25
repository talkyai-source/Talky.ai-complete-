"""Private, encrypted document store for number requests.

Layout: ``<root>/<tenant_id>/<document_id>.tnr`` (published) and
``<root>/.incoming/<uuid4hex>.part`` (temporary, same filesystem so
``os.replace`` is atomic). File format: ``b"TNR1" + nonce(12) + ciphertext +
tag(16)``, AES-256-GCM with a per-file key wrapped by the KMS service.

Nothing here logs a file name or content: ids and sizes only.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from dataclasses import dataclass
from typing import Iterator

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core import kms
from app.domain.services.number_requests import config
from app.domain.services.number_requests.errors import (
    DocumentFileMissing,
    DocumentUnreadable,
    StoragePathError,
)

logger = logging.getLogger(__name__)

MAGIC = b"TNR1"
NONCE_LEN = 12
TAG_LEN = 16
HEADER_LEN = len(MAGIC) + NONCE_LEN
INCOMING = ".incoming"
_KEY_RE = re.compile(r"^[0-9a-f-]{36}/[0-9a-f-]{36}\.tnr$")
_O_BINARY = getattr(os, "O_BINARY", 0)


def make_aad(tenant_id: str, document_id: str, kind: str) -> bytes:
    return b"talky-number-request-document-v1:%s:%s:%s" % (
        str(tenant_id).encode(), str(document_id).encode(), kind.encode())


class StreamEncryptor:
    """AES-256-GCM over a stream. ``begin()`` header, ``update()`` chunks, ``finish()`` tag."""

    def __init__(self, dek: bytes, aad: bytes) -> None:
        self._nonce = os.urandom(NONCE_LEN)
        self._enc = Cipher(algorithms.AES(dek), modes.GCM(self._nonce)).encryptor()
        self._enc.authenticate_additional_data(aad)

    def begin(self) -> bytes:
        return MAGIC + self._nonce

    def update(self, plaintext: bytes) -> bytes:
        return self._enc.update(plaintext)

    def finish(self) -> bytes:
        self._enc.finalize()
        return self._enc.tag


async def wrap_dek(dek: bytes) -> tuple[bytes, str]:
    """Wrap a per-file key with the KMS service. Returns (wrapped, provider_name)."""
    backend = kms.get_kms_backend()
    return await backend.wrap_key(dek), backend.provider_name


async def unwrap_dek(wrapped: bytes) -> bytes:
    return await kms.get_kms_backend().unwrap_key(wrapped)


def _mkdirs_private(directory: str) -> None:
    """Create each missing level with mode 0o700."""
    missing = []
    current = os.path.abspath(directory)
    while not os.path.exists(current):
        parent = os.path.dirname(current)
        if parent == current:
            raise FileNotFoundError("storage root is unavailable")
        missing.append(current)
        current = parent
    for level in reversed(missing):
        try:
            os.mkdir(level, mode=0o700)
        except FileExistsError:
            if not os.path.isdir(level):
                raise


@dataclass(frozen=True)
class PublishedFile:
    tenant_id: str
    document_id: str
    path: str
    mtime: float


class DocumentStore:
    def __init__(self, root: str | None = None) -> None:
        self._root_arg = root

    @property
    def root(self) -> str:
        return os.path.realpath(self._root_arg or config.documents_root())

    # -- paths -----------------------------------------------------------
    def _inside(self, path: str) -> str:
        root = self.root
        resolved = os.path.realpath(path)
        try:
            ok = os.path.commonpath((root, resolved)) == root and resolved != root
        except ValueError:
            ok = False
        if not ok:
            raise StoragePathError("path is outside the documents root")
        return resolved

    def path_for(self, key: str) -> str:
        if not _KEY_RE.match(key or ""):
            raise StoragePathError("malformed storage key")
        return self._inside(os.path.join(self.root, *key.split("/")))

    def incoming_dir(self) -> str:
        return os.path.join(self.root, INCOMING)

    # -- write -----------------------------------------------------------
    def create_temp(self) -> tuple[str, int]:
        """Create an empty temp file (0o600, exclusive). Returns (path, open fd)."""
        directory = self.incoming_dir()
        _mkdirs_private(directory)
        path = os.path.join(directory, uuid.uuid4().hex + ".part")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_BINARY, 0o600)
        return path, fd

    def publish(self, temp_path: str, key: str) -> None:
        """Atomically move a finished temp file to its final private place."""
        source = self._inside(temp_path)
        if os.path.dirname(source) != os.path.realpath(self.incoming_dir()):
            raise StoragePathError("temp file is not in the incoming folder")
        target = self.path_for(key)
        folder = os.path.dirname(target)
        _mkdirs_private(folder)
        os.replace(source, target)
        if os.name == "posix":
            fd = os.open(folder, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)

    def discard(self, temp_path: str) -> None:
        """Remove a temp file. Idempotent."""
        try:
            os.unlink(self._inside(temp_path))
        except FileNotFoundError:
            pass

    def remove(self, key: str) -> bool:
        try:
            os.unlink(self.path_for(key))
        except FileNotFoundError:
            return False
        return True

    # -- read ------------------------------------------------------------
    def read_plaintext(self, key: str, *, tenant_id: str, document_id: str, kind: str, dek: bytes) -> bytes:
        path = self.path_for(key)
        limit = config.MAX_MAX_FILE_BYTES + HEADER_LEN + TAG_LEN
        try:
            with open(path, "rb") as handle:
                blob = handle.read(limit + 1)
        except FileNotFoundError as exc:
            raise DocumentFileMissing("document file is missing") from exc
        if len(blob) > limit or len(blob) < HEADER_LEN + TAG_LEN or blob[:4] != MAGIC:
            raise DocumentUnreadable("document file header is invalid")
        nonce = blob[4:HEADER_LEN]
        try:
            return AESGCM(dek).decrypt(nonce, blob[HEADER_LEN:], make_aad(tenant_id, document_id, kind))
        except Exception as exc:
            raise DocumentUnreadable("document file failed authentication") from exc

    # -- sweeps ----------------------------------------------------------
    def iter_incoming(self) -> Iterator[tuple[str, float]]:
        directory = self.incoming_dir()
        if not os.path.isdir(directory):
            return
        for entry in os.scandir(directory):
            if entry.is_file(follow_symlinks=False) and entry.name.endswith(".part"):
                yield entry.path, entry.stat().st_mtime

    def iter_published(self) -> Iterator[PublishedFile]:
        root = self.root
        if not os.path.isdir(root):
            return
        for folder in os.scandir(root):
            if folder.name == INCOMING or not folder.is_dir(follow_symlinks=False):
                continue
            if not re.fullmatch(r"[0-9a-f-]{36}", folder.name):
                continue
            for entry in os.scandir(folder.path):
                name = entry.name
                if (entry.is_file(follow_symlinks=False)
                        and re.fullmatch(r"[0-9a-f-]{36}\.tnr", name)):
                    yield PublishedFile(folder.name, name[:-4], entry.path, entry.stat().st_mtime)


def get_store() -> DocumentStore:
    return DocumentStore()
