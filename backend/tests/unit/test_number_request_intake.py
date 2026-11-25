"""Intake of number-request uploads: caps, real type, names, exact pair."""
from __future__ import annotations

import logging
import os
import re

import pytest

from app.domain.services.number_requests import config
from app.domain.services.number_requests.errors import NumberRequestRefused
from app.domain.services.number_requests.intake import SubmissionReader
from app.domain.services.number_requests.store import DocumentStore

PDF = b"%PDF-1.7\n" + b"A" * 200
JPG = b"\xff\xd8\xff\xe0" + b"B" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"C" * 200
TENANT = "11111111-1111-1111-1111-111111111111"
CAP = 4096
KEY_RE = re.compile(r"^[0-9a-f-]{36}/[0-9a-f-]{36}[.]tnr$")


def build(parts, boundary="XBOUNDARYX"):
    out = b""
    for name, filename, data in parts:
        out += f"--{boundary}\r\n".encode()
        disp = f'Content-Disposition: form-data; name="{name}"'
        if filename is not None:
            disp += f'; filename="{filename}"'
        out += disp.encode() + b"\r\n\r\n" + data + b"\r\n"
    return out + f"--{boundary}--\r\n".encode()


def ctype(boundary="XBOUNDARYX"):
    return f"multipart/form-data; boundary={boundary}"


@pytest.fixture
def store(tmp_path):
    return DocumentStore(str(tmp_path / "docs"))


def run(store, body, chunk=1000, cap=CAP, boundary="XBOUNDARYX"):
    reader = SubmissionReader(ctype(boundary), tenant_id=TENANT, max_file_bytes=cap, store=store)
    for i in range(0, len(body), chunk):
        reader.feed(body[i:i + chunk])
    return reader.finish()


def refused(store, body, **kw):
    with pytest.raises(NumberRequestRefused) as info:
        run(store, body, **kw)
    return info.value


def temps(store):
    return list(store.iter_incoming())


BIZ = [("use_type", None, b"business"), ("business_registration", "reg.pdf", PDF),
       ("business_photo_id", "id.jpg", JPG)]
PER = [("use_type", None, b"personal"), ("utility_bill", "bill.pdf", PDF),
       ("personal_photo_id", "id.png", PNG)]


@pytest.mark.parametrize("parts,types", [(BIZ, ["application/pdf", "image/jpeg"]),
                                          (PER, ["application/pdf", "image/png"])])
def test_each_accepted_type_by_real_bytes(store, parts, types):
    sealed = run(store, build(parts))
    assert [f.content_type for f in sealed.files] == types
    assert sealed.use_type == parts[0][2].decode()
    assert len(temps(store)) == 2


def test_sha_and_size_are_right(store):
    import hashlib
    sealed = run(store, build(BIZ))
    first = sealed.files[0]
    assert first.size_bytes == len(PDF)
    assert first.sha256 == hashlib.sha256(PDF).hexdigest()


def test_pdf_name_with_executable_content_is_415(store):
    exe = b"MZ\x90\x00" + b"\x00" * 100
    e = refused(store, build([BIZ[0], ("business_registration", "x.pdf", exe), BIZ[2]]))
    assert e.status == 415 and e.code == "unsupported_file_type"
    assert temps(store) == []


def test_pdf_name_with_text_content_is_415(store):
    e = refused(store, build([BIZ[0], ("business_registration", "x.pdf", b"<html>hello there</html>"), BIZ[2]]))
    assert e.status == 415


def test_short_unknown_file_is_415(store):
    e = refused(store, build([BIZ[0], ("business_registration", "x.pdf", b"%PD"), BIZ[2]]))
    assert e.status == 415


def test_png_named_pdf_is_accepted_as_png_with_png_name(store):
    sealed = run(store, build([BIZ[0], ("business_registration", "scan.pdf", PNG), BIZ[2]]))
    assert sealed.files[0].content_type == "image/png"
    assert sealed.files[0].file_name == "scan.png"


def test_empty_file_is_422(store):
    e = refused(store, build([BIZ[0], ("business_registration", "x.pdf", b""), BIZ[2]]))
    assert (e.status, e.code) == (422, "empty_file")
    assert temps(store) == []


def test_exactly_at_cap_is_accepted_and_one_over_is_413(store):
    ok = b"%PDF-" + b"x" * (CAP - 5)
    sealed = run(store, build([BIZ[0], ("business_registration", "x.pdf", ok), BIZ[2]]))
    assert sealed.files[0].size_bytes == CAP
    for f in sealed.files:
        store.discard(f.temp_path)
    e = refused(store, build([BIZ[0], ("business_registration", "x.pdf", ok + b"y"), BIZ[2]]))
    assert (e.status, e.code) == (413, "file_too_large")
    assert e.extra["max_file_bytes"] == CAP
    assert temps(store) == []


def test_size_cap_stops_the_read(store):
    big = b"%PDF-" + b"x" * (CAP * 10)
    body = build([BIZ[0], ("business_registration", "x.pdf", big), BIZ[2]])
    reader = SubmissionReader(ctype(), tenant_id=TENANT, max_file_bytes=CAP, store=store)
    fed, chunk = 0, 512
    with pytest.raises(NumberRequestRefused) as info:
        for i in range(0, len(body), chunk):
            reader.feed(body[i:i + chunk])
            fed += chunk
    assert info.value.status == 413
    assert fed <= CAP + 512 * 2 + 400  # cap + chunk + part headers
    assert fed < len(body) // 2
    assert temps(store) == []


def test_missing_file_is_422(store):
    e = refused(store, build(BIZ[:2]))
    assert (e.status, e.code, e.field) == (422, "missing_document", "business_photo_id")


def test_missing_use_type_and_wrong_use_type(store):
    assert refused(store, build(BIZ[1:])).code == "invalid_use_type"
    assert refused(store, build([("use_type", None, b"enterprise")] + BIZ[1:])).code == "invalid_use_type"


def test_extra_file_is_422(store):
    e = refused(store, build(BIZ + [("utility_bill", "b.pdf", PDF)]))
    assert (e.status, e.code) == (422, "unexpected_file")
    assert temps(store) == []


def test_other_use_file_with_use_type_first_and_last(store):
    first = [BIZ[0], ("utility_bill", "b.pdf", PDF), BIZ[2]]
    last = [BIZ[1], ("utility_bill", "b.pdf", PDF), BIZ[0]]
    for parts in (first, last):
        e = refused(store, build(parts))
        assert (e.status, e.code) == (422, "unexpected_file")
        assert temps(store) == []


def test_unknown_field_is_422_with_clamped_name(store):
    e = refused(store, build(BIZ + [("x" * 200 + "<script>", "a.pdf", PDF)]))
    assert e.code == "unexpected_file"
    assert len(e.field) <= 64 and re.fullmatch(r"[A-Za-z0-9_]*", e.field)
    assert temps(store) == []


def test_duplicate_file_is_422(store):
    e = refused(store, build(BIZ + [("business_registration", "b.pdf", PDF)]))
    assert e.code == "duplicate_document"
    assert temps(store) == []


def test_not_multipart_is_422(store):
    with pytest.raises(NumberRequestRefused) as info:
        SubmissionReader("application/json", tenant_id=TENANT, store=store)
    assert info.value.code == "invalid_form"
    with pytest.raises(NumberRequestRefused):
        SubmissionReader("multipart/form-data", tenant_id=TENANT, store=store)


def test_truncated_body_is_invalid_form_and_leaves_no_temp(store):
    body = build(BIZ)
    reader = SubmissionReader(ctype(), tenant_id=TENANT, max_file_bytes=CAP, store=store)
    reader.feed(body[: len(body) - 150])
    with pytest.raises(NumberRequestRefused) as info:
        reader.finish()
    assert info.value.code == "invalid_form"
    assert temps(store) == []


def test_oversize_use_type_value_and_too_many_parts(store):
    assert refused(store, build([("use_type", None, b"b" * 100)] + BIZ[1:])).code == "invalid_use_type"
    many = BIZ + [("extra1", None, b"1"), ("extra2", None, b"2"), ("extra3", None, b"3")]
    assert refused(store, build(many)).status == 422


def test_oversize_header_is_refused(store):
    e = refused(store, build([BIZ[0], ("business_registration", "a" * 9000 + ".pdf", PDF), BIZ[2]]))
    assert e.code == "invalid_form"


@pytest.mark.parametrize("raw,expected", [
    ("../../x.pdf", "x"), ("C:\\\\evil\\\\dir\\\\y.pdf", "y"),
    ("a\x01b\x7fc.pdf", None), ("n" * 500 + ".pdf", None), ("", "attachment")])
def test_file_name_cleaner(store, raw, expected):
    name = raw.replace('"', "")
    sealed = run(store, build([BIZ[0], ("business_registration", name, PDF), BIZ[2]]))
    cleaned = sealed.files[0].file_name
    assert cleaned.endswith(".pdf")
    assert "/" not in cleaned and "\\" not in cleaned and ".." not in cleaned
    assert not any(ord(c) < 32 or ord(c) == 127 for c in cleaned)
    assert 1 <= len(cleaned) <= 120
    if expected:
        assert cleaned == expected + ".pdf"


def test_storage_keys_are_random_and_do_not_contain_the_name(store):
    a = run(store, build([BIZ[0], ("business_registration", "passport-jane.pdf", PDF), BIZ[2]]))
    b = run(store, build([BIZ[0], ("business_registration", "passport-jane.pdf", PDF), BIZ[2]]))
    for f in a.files + b.files:
        assert KEY_RE.match(f.storage_key) and f.storage_key.startswith(TENANT + "/")
        assert "passport" not in f.storage_key and "jane" not in f.storage_key
    assert {f.storage_key for f in a.files}.isdisjoint({f.storage_key for f in b.files})


def test_fingerprint_ignores_boundary_names_and_order(store):
    a = run(store, build(BIZ, "AAAAAAAA"), boundary="AAAAAAAA")
    renamed = [BIZ[0], ("business_registration", "other.pdf", PDF), ("business_photo_id", "o.jpg", JPG)]
    b = run(store, build(list(reversed(renamed)), "BBBBBBBB"), boundary="BBBBBBBB")
    assert a.fingerprint == b.fingerprint
    c = run(store, build([BIZ[0], ("business_registration", "r.pdf", PDF + b"z"), BIZ[2]]))
    assert c.fingerprint != a.fingerprint


def test_use_type_rules_are_in_config():
    assert config.USE_TYPES == {"business": ("business_registration", "business_photo_id"),
                                "personal": ("utility_bill", "personal_photo_id")}
    assert set(config.KIND_LABELS) == {k for v in config.USE_TYPES.values() for k in v}


def test_nothing_sensitive_is_logged(store, caplog):
    caplog.set_level(logging.DEBUG)
    secret_name = "jane-doe-passport-9921.pdf"
    marker = b"SECRET-PLAINTEXT-MARKER"
    run(store, build([BIZ[0], ("business_registration", secret_name, PDF + marker), BIZ[2]]))
    refused(store, build([BIZ[0], ("business_registration", secret_name, b"MZ" + marker), BIZ[2]]))
    text = caplog.text
    assert "jane" not in text and "9921" not in text and "SECRET-PLAINTEXT" not in text


def test_what_is_on_disk_is_not_plaintext(store):
    marker = b"PLAINTEXT-MARKER-0123456789"
    sealed = run(store, build([BIZ[0], ("business_registration", "r.pdf", PDF + marker), BIZ[2]]))
    with open(sealed.files[0].temp_path, "rb") as handle:
        on_disk = handle.read()
    assert marker not in on_disk and b"%PDF-" not in on_disk
    assert on_disk[:4] == b"TNR1"


def test_config_clamps(monkeypatch):
    monkeypatch.setenv("NUMBER_REQUEST_MAX_FILE_BYTES", "1")
    assert config.max_file_bytes() == 1024
    monkeypatch.setenv("NUMBER_REQUEST_MAX_FILE_BYTES", str(10**12))
    assert config.max_file_bytes() == 25 * 1024 * 1024
    monkeypatch.setenv("NUMBER_REQUEST_MAX_FILE_BYTES", "junk")
    assert config.max_file_bytes() == 10485760
    monkeypatch.delenv("NUMBER_REQUEST_MAX_FILE_BYTES")
    assert config.submit_body_limit_bytes() == 2 * 10485760 + 262144
    monkeypatch.setenv("NUMBER_REQUEST_REJECTED_RETENTION_DAYS", "keep")
    assert config.rejected_retention_days() is None
    monkeypatch.setenv("NUMBER_REQUEST_REJECTED_RETENTION_DAYS", "30")
    assert config.rejected_retention_days() == 30
    assert os.path.isabs(config.documents_root())


# ---------------------------------------------- item 3: abort versus a running feed ----

def test_abort_waits_for_a_feed_that_is_writing_and_later_feeds_write_nothing(store, monkeypatch):
    import threading
    import time

    reader = SubmissionReader(ctype(), tenant_id=TENANT, max_file_bytes=CAP, store=store)
    entered, release = threading.Event(), threading.Event()
    writes, closed_at_write = [], []
    original = SubmissionReader._write

    def slow_write(self, part, plaintext):
        writes.append(1)
        entered.set()
        assert release.wait(5)
        closed_at_write.append(part.fd is None)
        return original(self, part, plaintext)

    monkeypatch.setattr(SubmissionReader, "_write", slow_write)
    body = build(BIZ)
    feeder = threading.Thread(target=reader.feed, args=(body,))
    feeder.start()
    assert entered.wait(5)
    aborter = threading.Thread(target=reader.abort)
    aborter.start()
    time.sleep(0.3)
    assert aborter.is_alive(), "abort must wait for the feed that is still writing"
    release.set()
    feeder.join(5)
    aborter.join(5)
    assert not feeder.is_alive() and not aborter.is_alive()
    assert closed_at_write and not any(closed_at_write), "a write ran on a closed descriptor"
    count_after_abort = len(writes)
    assert temps(store) == []
    reader.feed(build(BIZ))  # a late feed after abort: nothing is written, no temp file appears
    assert len(writes) == count_after_abort
    assert temps(store) == []
    with pytest.raises(NumberRequestRefused):
        reader.finish()
    assert temps(store) == []


# -------------------------------------------- item 4: a very long boundary ----

def test_boundary_over_256_characters_is_invalid_form(store):
    with pytest.raises(NumberRequestRefused) as info:
        SubmissionReader(ctype("B" * 5000), tenant_id=TENANT, max_file_bytes=CAP, store=store)
    assert info.value.status == 422 and info.value.code == "invalid_form"
    assert temps(store) == []


# ------------------------------------------ item 6: a document field with no file ----

def test_document_field_without_a_file_name_is_not_a_file(store):
    parts = [BIZ[0], ("business_registration", None, b"hello"), BIZ[2]]
    exc = refused(store, build(parts))
    assert (exc.status, exc.code, exc.field) == (422, "invalid_form", "business_registration")
    assert exc.message == "Send Business registration as a file."
    parts = [PER[0], PER[1], ("personal_photo_id", None, b"hello")]
    assert refused(store, build(parts)).message == "Send Photo ID matching the utility bill as a file."
    assert temps(store) == []
