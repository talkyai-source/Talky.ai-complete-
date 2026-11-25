"""Private encrypted store: round trip, tamper, swap, permissions, clean-up."""
from __future__ import annotations

import logging
import os
import stat
import uuid

import pytest

from app.core import kms
from app.domain.services.number_requests.errors import (
    DocumentFileMissing,
    DocumentUnreadable,
    StoragePathError,
)
from app.domain.services.number_requests.scan import scan_document
from app.domain.services.number_requests.store import (
    DocumentStore,
    StreamEncryptor,
    make_aad,
    unwrap_dek,
    wrap_dek,
)

MARKER = b"PLAINTEXT-MARKER-ABCDEFGHIJ"
PLAIN = b"%PDF-1.7 " + MARKER * 20


@pytest.fixture
def store(tmp_path):
    return DocumentStore(str(tmp_path / "docs"))


def seal(store, tenant=None, doc=None, kind="utility_bill", plaintext=PLAIN):
    tenant, doc = tenant or str(uuid.uuid4()), doc or str(uuid.uuid4())
    dek = os.urandom(32)
    enc = StreamEncryptor(dek, make_aad(tenant, doc, kind))
    path, fd = store.create_temp()
    os.write(fd, enc.begin() + enc.update(plaintext) + enc.finish())
    os.close(fd)
    return tenant, doc, kind, dek, path, f"{tenant}/{doc}.tnr"


def test_round_trip_and_disk_is_not_plaintext(store):
    tenant, doc, kind, dek, path, key = seal(store)
    store.publish(path, key)
    on_disk = open(store.path_for(key), "rb").read()
    assert MARKER not in on_disk and b"%PDF-" not in on_disk
    assert store.read_plaintext(key, tenant_id=tenant, document_id=doc, kind=kind, dek=dek) == PLAIN


def test_publish_moves_the_temp_file(store):
    tenant, doc, kind, dek, path, key = seal(store)
    store.publish(path, key)
    assert not os.path.exists(path)
    assert os.path.exists(store.path_for(key))
    assert [p.document_id for p in store.iter_published()] == [doc]
    assert list(store.iter_incoming()) == []


def test_flipped_byte_is_unreadable(store):
    tenant, doc, kind, dek, path, key = seal(store)
    store.publish(path, key)
    target = store.path_for(key)
    blob = bytearray(open(target, "rb").read())
    blob[40] ^= 0x01
    open(target, "wb").write(bytes(blob))
    with pytest.raises(DocumentUnreadable):
        store.read_plaintext(key, tenant_id=tenant, document_id=doc, kind=kind, dek=dek)


def test_bad_header_and_wrong_key_are_unreadable(store):
    tenant, doc, kind, dek, path, key = seal(store)
    store.publish(path, key)
    with pytest.raises(DocumentUnreadable):
        store.read_plaintext(key, tenant_id=tenant, document_id=doc, kind=kind, dek=os.urandom(32))
    open(store.path_for(key), "wb").write(b"XXXX" + b"\x00" * 60)
    with pytest.raises(DocumentUnreadable):
        store.read_plaintext(key, tenant_id=tenant, document_id=doc, kind=kind, dek=dek)


def test_aad_blocks_a_file_swap(store):
    tenant, doc, kind, dek, path, key = seal(store)
    store.publish(path, key)
    for other in ({"tenant_id": str(uuid.uuid4())}, {"document_id": str(uuid.uuid4())},
                  {"kind": "personal_photo_id"}):
        args = dict(tenant_id=tenant, document_id=doc, kind=kind, dek=dek) | other
        with pytest.raises(DocumentUnreadable):
            store.read_plaintext(key, **args)


def test_missing_file(store):
    key = f"{uuid.uuid4()}/{uuid.uuid4()}.tnr"
    with pytest.raises(DocumentFileMissing):
        store.read_plaintext(key, tenant_id="t", document_id="d", kind="k", dek=os.urandom(32))


def test_path_escape_and_bad_keys_are_refused(store):
    for key in ("../../etc/passwd", "a/b.tnr", f"{uuid.uuid4()}/../{uuid.uuid4()}.tnr", ""):
        with pytest.raises(StoragePathError):
            store.path_for(key)
    with pytest.raises(StoragePathError):
        store.publish(__file__, f"{uuid.uuid4()}/{uuid.uuid4()}.tnr")
    with pytest.raises(StoragePathError):
        store.discard(__file__)


def test_discard_is_idempotent_and_remove_reports(store):
    tenant, doc, kind, dek, path, key = seal(store)
    store.discard(path)
    store.discard(path)
    assert not os.path.exists(path)
    _, _, _, _, path2, key2 = seal(store)
    store.publish(path2, key2)
    assert store.remove(key2) is True
    assert store.remove(key2) is False


def test_failed_publish_leaves_no_stray_published_file(store, monkeypatch):
    tenant, doc, kind, dek, path, key = seal(store)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        store.publish(path, key)
    monkeypatch.undo()
    assert list(store.iter_published()) == []
    store.discard(path)
    assert list(store.iter_incoming()) == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes are not enforced on Windows")
def test_modes_are_private(store):
    tenant, doc, kind, dek, path, key = seal(store)
    store.publish(path, key)
    assert stat.S_IMODE(os.stat(store.path_for(key)).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(os.path.dirname(store.path_for(key))).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(store.root).st_mode) == 0o700


def test_temp_file_is_created_exclusively_inside_incoming(store):
    path, fd = store.create_temp()
    os.close(fd)
    assert os.path.dirname(os.path.realpath(path)) == os.path.realpath(store.incoming_dir())
    assert path.endswith(".part")


async def test_dek_wrap_round_trip_through_kms(monkeypatch):
    backend = kms.LocalKMSBackend(master_key=os.urandom(32))
    monkeypatch.setattr(kms, "get_kms_backend", lambda: backend)
    dek = os.urandom(32)
    wrapped, provider = await wrap_dek(dek)
    assert provider == "local_aes_gcm" and wrapped != dek and dek not in wrapped
    assert await unwrap_dek(wrapped) == dek


def test_scan_seam_returns_not_scanned():
    assert scan_document(PLAIN) == "not_scanned"


def test_store_logs_nothing_sensitive(store, caplog):
    caplog.set_level(logging.DEBUG)
    tenant, doc, kind, dek, path, key = seal(store)
    store.publish(path, key)
    store.read_plaintext(key, tenant_id=tenant, document_id=doc, kind=kind, dek=dek)
    store.remove(key)
    assert "PLAINTEXT-MARKER" not in caplog.text


# -------------------- item 9a: what the intake writes, the staff read path reads back ----

PDF_BYTES = b"%PDF-1.7\n" + bytes(range(256)) * 9
JPG_BYTES = b"\xff\xd8\xff\xe0" + bytes(range(255, -1, -1)) * 9
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00\x01\x02\x03" * 600


class _KmsFake:
    provider_name = "fake_kms"

    async def wrap_key(self, dek):
        return b"W" + dek

    async def unwrap_key(self, wrapped):
        return wrapped[1:]


_FILLER = {"business_registration": b"%PDF-1.7\n" + b"r" * 40,
           "business_photo_id": b"\xff\xd8\xff\xe0" + b"j" * 40,
           "utility_bill": b"%PDF-1.7\n" + b"u" * 40,
           "personal_photo_id": b"\x89PNG\r\n\x1a\n" + b"p" * 40}
_KINDS = {"business": ("business_registration", "business_photo_id"),
          "personal": ("utility_bill", "personal_photo_id")}


def _multipart(use_type, name, data):
    out = b'--XB\r\nContent-Disposition: form-data; name="use_type"\r\n\r\n' + use_type.encode() + b"\r\n"
    for kind in _KINDS[use_type]:
        payload = data if kind == name else _FILLER[kind]
        out += f'--XB\r\nContent-Disposition: form-data; name="{kind}"; filename="f.bin"\r\n\r\n'.encode()
        out += payload + b"\r\n"
    return out + b"--XB--\r\n"


@pytest.mark.parametrize("chunk", [1, 7, 64 * 1024 + 3, None])
@pytest.mark.parametrize("use_type,kind,data", [
    ("business", "business_registration", PDF_BYTES),
    ("business", "business_photo_id", JPG_BYTES),
    ("personal", "personal_photo_id", PNG_BYTES),
])
async def test_intake_output_reads_back_byte_for_byte_like_the_staff_route(
        store, monkeypatch, use_type, kind, data, chunk):
    from app.domain.services.number_requests.intake import SubmissionReader

    monkeypatch.setattr(kms, "get_kms_backend", lambda: _KmsFake())
    tenant = str(uuid.uuid4())
    body = _multipart(use_type, kind, data)
    reader = SubmissionReader("multipart/form-data; boundary=XB", tenant_id=tenant, store=store)
    step = len(body) if chunk is None else chunk
    for i in range(0, len(body), step):
        reader.feed(body[i:i + step])
    sealed = reader.finish()
    sf = next(f for f in sealed.files if f.kind == kind)
    wrapped, _provider = await wrap_dek(sf.dek)
    for f in sealed.files:
        store.publish(f.temp_path, f.storage_key)
    dek = await unwrap_dek(wrapped)
    args = dict(tenant_id=tenant, document_id=sf.document_id, kind=sf.kind, dek=dek)
    assert store.read_plaintext(sf.storage_key, **args) == data
    for wrong in ({"tenant_id": str(uuid.uuid4())}, {"document_id": str(uuid.uuid4())},
                  {"kind": "utility_bill" if kind != "utility_bill" else "business_photo_id"}):
        with pytest.raises(DocumentUnreadable):
            store.read_plaintext(sf.storage_key, **(args | wrong))
