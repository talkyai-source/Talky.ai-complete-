"""Streaming intake of a number-request upload.

The body is fed in chunks. The size cap is enforced while reading, the real
type comes from the first bytes, SHA-256 is computed on the plaintext, and the
file is encrypted as it streams to a temp file: plaintext never touches disk.
Nothing from a file's content or its original name is logged.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
import uuid
from dataclasses import dataclass, field

from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import MultipartParser, parse_options_header

from app.domain.services.number_requests import config, errors
from app.domain.services.number_requests.store import DocumentStore, StreamEncryptor, make_aad
from app.domain.services.support.attachment_files import clean_filename

logger = logging.getLogger(__name__)

WINDOW = 64 * 1024
MAX_HEADER_VALUE = 8 * 1024
MAX_PARTS = 5
MAX_USE_TYPE_BYTES = 32
_HEAD_LEN = 8


@dataclass(frozen=True)
class SealedFile:
    kind: str
    document_id: str
    storage_key: str
    temp_path: str
    file_name: str
    content_type: str
    size_bytes: int
    sha256: str
    dek: bytes = field(repr=False)


@dataclass(frozen=True)
class SealedSubmission:
    use_type: str
    files: tuple[SealedFile, ...]
    fingerprint: str


def detect_type(head: bytes) -> config.AcceptedType | None:
    for accepted in config.ACCEPTED_TYPES:
        for magic in accepted.magic:
            if head[accepted.offset:accepted.offset + len(magic)] == magic:
                return accepted
    return None


def fingerprint_for(use_type: str, files: dict[str, tuple[str, int]]) -> str:
    """files maps kind -> (sha256, size). Independent of boundary and names."""
    order = config.USE_TYPES[use_type]
    body = "\n".join(f"{kind}:{files[kind][0]}:{files[kind][1]}" for kind in order)
    return hashlib.sha256(("v1\n" + use_type + "\n" + body).encode()).hexdigest()


class _Part:
    def __init__(self) -> None:
        self.name = ""
        self.is_file = False
        self.raw_filename = ""
        self.value = bytearray()
        # file state
        self.kind = ""
        self.count = 0
        self.head = bytearray()
        self.accepted: config.AcceptedType | None = None
        self.sha = hashlib.sha256()
        self.enc: StreamEncryptor | None = None
        self.fd: int | None = None
        self.temp_path = ""
        self.document_id = ""
        self.dek = b""


class SubmissionReader:
    """Feed request-body chunks with ``feed``; call ``finish`` once at the end.

    Any exception aborts the reader (temp files removed). ``abort`` is idempotent.
    """

    def __init__(self, content_type: str, *, tenant_id: str, max_file_bytes: int | None = None,
                 store: DocumentStore | None = None) -> None:
        self._tenant_id = str(tenant_id)
        self._max = config.max_file_bytes() if max_file_bytes is None else max_file_bytes
        self._store = store or DocumentStore()
        self._use_type: str | None = None
        self._sealed: dict[str, SealedFile] = {}
        self._parts_seen = 0
        self._part = _Part()
        self._hname = b""
        self._hvalue = b""
        self._disposition = b""
        self._temps: list[str] = []
        self._closed = False
        self._aborted = False
        # One lock for feed, finish and abort: feed runs in a worker thread while the
        # route may abort from the event loop; abort must not close a descriptor in use.
        self._lock = threading.Lock()
        self._parser = self._build_parser(content_type)

    # -- parser plumbing -------------------------------------------------
    def _build_parser(self, content_type: str) -> MultipartParser:
        try:
            media, params = parse_options_header(content_type or "")
            boundary = params[b"boundary"]
        except Exception:
            raise errors.invalid_form() from None
        if media.lower() != b"multipart/form-data" or not boundary:
            raise errors.invalid_form()
        callbacks = {
            "on_part_begin": self._on_part_begin,
            "on_part_data": self._on_part_data,
            "on_part_end": self._on_part_end,
            "on_header_field": self._on_header_field,
            "on_header_value": self._on_header_value,
            "on_header_end": self._on_header_end,
            "on_headers_finished": self._on_headers_finished,
        }
        try:
            return MultipartParser(boundary, callbacks)
        except Exception:  # e.g. a boundary over 256 characters
            raise errors.invalid_form() from None

    def feed(self, chunk: bytes) -> None:
        with self._lock:
            if self._aborted:
                return
            try:
                for start in range(0, len(chunk), WINDOW):
                    self._parser.write(chunk[start:start + WINDOW])
            except MultipartParseError:
                self._abort_locked()
                raise errors.invalid_form() from None
            except BaseException:
                self._abort_locked()
                raise

    def finish(self) -> SealedSubmission:
        with self._lock:
            if self._aborted:
                raise errors.invalid_form()
            return self._finish_locked()

    def _finish_locked(self) -> SealedSubmission:
        try:
            try:
                self._parser.finalize()
            except MultipartParseError:
                raise errors.invalid_form() from None
            if self._part.name and (self._part.fd is not None):
                raise errors.invalid_form()
            if self._use_type is None:
                raise errors.invalid_use_type()
            self._check_kinds()
            for kind in config.USE_TYPES[self._use_type]:
                if kind not in self._sealed:
                    raise errors.missing_document(kind)
            files = tuple(self._sealed[k] for k in config.USE_TYPES[self._use_type])
            fp = fingerprint_for(self._use_type, {f.kind: (f.sha256, f.size_bytes) for f in files})
            self._closed = True
            return SealedSubmission(self._use_type, files, fp)
        except BaseException:
            self._abort_locked()
            raise

    def abort(self) -> None:
        with self._lock:
            self._abort_locked()

    def _abort_locked(self) -> None:
        self._aborted = True
        part = self._part
        if part.fd is not None:
            try:
                os.close(part.fd)
            except OSError:
                pass
            part.fd = None
        for path in self._temps:
            try:
                self._store.discard(path)
            except OSError:
                logger.warning("number_request_temp_discard_failed")
        self._temps.clear()
        self._sealed.clear()
        self._closed = True

    # -- rules -------------------------------------------------------------
    def _check_kinds(self) -> None:
        if self._use_type is None:
            return
        allowed = config.USE_TYPES[self._use_type]
        for kind in self._sealed:
            if kind not in allowed:
                raise errors.unexpected_file(kind, self._use_type)

    # -- callbacks ---------------------------------------------------------
    def _on_part_begin(self) -> None:
        self._parts_seen += 1
        if self._parts_seen > MAX_PARTS:
            raise errors.invalid_form("Send only the documents for this request.")
        self._part = _Part()
        self._disposition = b""

    def _on_header_field(self, data: bytes, start: int, end: int) -> None:
        self._hname += data[start:end]
        if len(self._hname) > MAX_HEADER_VALUE:
            raise errors.invalid_form()

    def _on_header_value(self, data: bytes, start: int, end: int) -> None:
        self._hvalue += data[start:end]
        if len(self._hvalue) > MAX_HEADER_VALUE:
            raise errors.invalid_form()

    def _on_header_end(self) -> None:
        if self._hname.lower() == b"content-disposition":
            self._disposition = self._hvalue
        self._hname = b""
        self._hvalue = b""

    def _on_headers_finished(self) -> None:
        part = self._part
        _, options = parse_options_header(self._disposition)
        raw_name = options.get(b"name")
        if raw_name is None:
            raise errors.invalid_form()
        part.name = raw_name.decode("utf-8", "replace") if isinstance(raw_name, bytes) else str(raw_name)
        raw_file = options.get(b"filename")
        part.is_file = raw_file is not None
        if raw_file is not None:
            part.raw_filename = (raw_file.decode("utf-8", "replace")
                                 if isinstance(raw_file, bytes) else str(raw_file))
        if part.name == "use_type" and not part.is_file:
            if self._use_type is not None:
                raise errors.invalid_form()
            return
        all_kinds = config.KIND_LABELS
        if part.name in all_kinds and not part.is_file:
            raise errors.not_a_file(part.name)
        if not part.is_file or part.name not in all_kinds:
            raise errors.unexpected_file(errors.clamp_field_name(part.name), self._use_type)
        if self._use_type is not None and part.name not in config.USE_TYPES[self._use_type]:
            raise errors.unexpected_file(part.name, self._use_type)
        if part.name in self._sealed:
            raise errors.duplicate_document(part.name)
        part.kind = part.name
        part.document_id = str(uuid.uuid4())
        part.dek = os.urandom(32)
        part.enc = StreamEncryptor(part.dek, make_aad(self._tenant_id, part.document_id, part.kind))
        path, fd = self._store.create_temp()
        part.temp_path, part.fd = path, fd
        self._temps.append(path)

    def _on_part_data(self, data: bytes, start: int, end: int) -> None:
        part = self._part
        chunk = data[start:end]
        if not part.kind:
            part.value += chunk
            if len(part.value) > MAX_USE_TYPE_BYTES:
                raise errors.invalid_use_type()
            return
        part.count += len(chunk)
        if part.count > self._max:
            raise errors.file_too_large(part.kind, self._max)
        if part.accepted is None:
            part.head += chunk
            if len(part.head) < _HEAD_LEN:
                return
            self._classify(part)
            chunk, part.head = bytes(part.head), bytearray()
        self._write(part, chunk)

    def _classify(self, part: _Part) -> None:
        accepted = detect_type(bytes(part.head))
        if accepted is None:
            raise errors.unsupported_file_type(part.kind, config.accepted_content_types())
        part.accepted = accepted
        assert part.fd is not None and part.enc is not None
        os.write(part.fd, part.enc.begin())

    def _write(self, part: _Part, plaintext: bytes) -> None:
        assert part.fd is not None and part.enc is not None
        part.sha.update(plaintext)
        data = part.enc.update(plaintext)
        view = memoryview(data)
        while view:
            written = os.write(part.fd, view)
            view = view[written:]

    def _on_part_end(self) -> None:
        part = self._part
        if not part.kind:
            if part.name == "use_type":
                value = bytes(part.value).decode("utf-8", "replace")
                if value not in config.USE_TYPES:
                    raise errors.invalid_use_type()
                self._use_type = value
                self._check_kinds()
            return
        if part.count == 0:
            raise errors.empty_file(part.kind)
        if part.accepted is None:
            self._classify(part)
            head, part.head = bytes(part.head), bytearray()
            self._write(part, head)
        assert part.fd is not None and part.enc is not None and part.accepted is not None
        tag = part.enc.finish()
        os.write(part.fd, tag)
        os.fsync(part.fd)
        os.close(part.fd)
        part.fd = None
        sealed = SealedFile(
            kind=part.kind,
            document_id=part.document_id,
            storage_key=f"{self._tenant_id}/{part.document_id}.tnr",
            temp_path=part.temp_path,
            file_name=clean_filename(part.raw_filename, part.accepted.download_ext),
            content_type=part.accepted.content_type,
            size_bytes=part.count,
            sha256=part.sha.hexdigest(),
            dek=part.dek,
        )
        self._sealed[part.kind] = sealed
        logger.info("number_request_file_sealed kind=%s size=%d document=%s",
                    part.kind, part.count, part.document_id)
        self._part = _Part()
