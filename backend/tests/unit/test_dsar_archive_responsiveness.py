"""Bounded exports must yield to other API work while writing/compressing ZIPs."""
import asyncio
from contextlib import suppress
import zipfile

import pytest

from tests.unit.test_round3_dsar_export import ACTOR, harness, scope  # noqa: F401


@pytest.mark.parametrize("prefix", ["rows/", "audio/"])
async def test_archive_write_yields_between_bounded_records_and_chunks(harness, tmp_path, monkeypatch, prefix):
    mod, service, *_ = harness
    ticks = 0
    observed = []

    async def heartbeat():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0)

    original_write = zipfile._ZipWriteFile.write

    def observe_write(member, data):
        if data and member._zinfo.filename.startswith(prefix):
            observed.append(ticks)
        return original_write(member, data)

    async def media(_kind, _row, target, *, max_bytes):
        payload = b"synthetic audio" * 20000
        assert len(payload) < max_bytes
        target.write(payload)
        return len(payload)

    monkeypatch.setattr(zipfile._ZipWriteFile, "write", observe_write)
    monkeypatch.setattr(service.artifacts, "copy_into", media)
    beat = asyncio.create_task(heartbeat())
    try:
        await asyncio.sleep(0)
        await service.export(scope(mod), actor_id=ACTOR, output=tmp_path / "responsive.zip")
        assert len(observed) > 1
        assert max(observed) > min(observed), "ZIP writing starved the API event loop"
    finally:
        beat.cancel()
        with suppress(asyncio.CancelledError):
            await beat


async def test_cancel_during_archive_write_removes_partial_private_output(harness, tmp_path, monkeypatch):
    mod, service, *_ = harness
    target = tmp_path / "cancelled.zip"
    original_write = zipfile._ZipWriteFile.write
    requested = False

    def cancel_after_write(member, data):
        nonlocal requested
        result = original_write(member, data)
        if data and not requested:
            requested = True
            asyncio.current_task().cancel()
        return result

    monkeypatch.setattr(zipfile._ZipWriteFile, "write", cancel_after_write)
    export = asyncio.create_task(service.export(scope(mod), actor_id=ACTOR, output=target))
    with pytest.raises(asyncio.CancelledError):
        await export
    assert requested
    assert not target.exists(), "Cancelled export retained a private archive"
