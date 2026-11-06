"""F12-06: real DSAR boundaries without domain-to-API dependencies."""
from __future__ import annotations

import builtins
import importlib
import io
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from fastapi import HTTPException


TENANT = UUID('a0000000-0000-4000-8000-000000000001')
OTHER = UUID('b0000000-0000-4000-8000-000000000002')


def deny_api_imports(monkeypatch):
    original = builtins.__import__

    def checked(name, *args, **kwargs):
        assert name != 'app.api' and not name.startswith('app.api.'), 'DSAR operation imported the API layer'
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, '__import__', checked)


@pytest.mark.parametrize('kind', ['recordings_s3', 'recordings'])
async def test_dsar_recording_copy_has_no_api_dependency(tmp_path, monkeypatch, kind):
    dsar = importlib.import_module('app.domain.services.dsar_service')
    root = tmp_path / 'recordings'
    root.mkdir()
    audio = root / 'call.wav'
    audio.write_bytes(b'qa-audio')
    monkeypatch.setenv('LOCAL_RECORDINGS_DIR', str(root))
    row = {'s3_bucket': 'local', 's3_key': str(audio), 'storage_path': str(audio)}
    deny_api_imports(monkeypatch)
    output = io.BytesIO()
    assert await dsar.DsarArtifactReader().copy_into(kind, row, output, max_bytes=64) == 8
    assert output.getvalue() == b'qa-audio'


@pytest.mark.parametrize('held', [False, True])
async def test_dsar_hold_collection_has_no_api_dependency(monkeypatch, held):
    dsar = importlib.import_module('app.domain.services.dsar_service')
    calls = []

    async def fetchval(sql, *args):
        if sql == 'SELECT to_regclass($1)':
            return 'suspension_events' if args == ('public.suspension_events',) else None
        calls.append((sql, args))
        assert args == (TENANT,)
        return held

    conn = SimpleNamespace(fetchval=fetchval)
    deny_api_imports(monkeypatch)
    rows, manifest = await dsar.DsarService(None, audit=object())._collect(
        conn, dsar.DsarScope(tenant_id=TENANT, email='privacy@example.invalid'))
    assert manifest['legal_hold'] is held
    assert len(calls) == 1 and all(not value for value in rows.values())
    assert 'held_tenant.id = $1' in ' '.join(calls[0][0].split())


def test_api_recording_wrappers_preserve_error_and_logging_contracts(tmp_path, monkeypatch, caplog):
    from app.api.v1.endpoints import recordings
    from app.api.v1.endpoints.admin import media

    root = tmp_path / 'recordings'
    root.mkdir()
    monkeypatch.setenv('LOCAL_RECORDINGS_DIR', str(root))
    outside = tmp_path / 'recordings-neighbor' / 'private.wav'
    caplog.clear()
    with pytest.raises(HTTPException) as tenant_error:
        recordings._validated_recording_path(str(outside))
    assert tenant_error.value.status_code == 500
    assert tenant_error.value.detail == {'code': 'recording_storage_invalid'}
    assert [(r.name, r.getMessage()) for r in caplog.records] == [(recordings.logger.name, 'recording_path_outside_root')]
    caplog.clear()
    with pytest.raises(HTTPException) as admin_error:
        media._validated_recording_path(str(outside))
    assert admin_error.value.status_code == 500
    assert admin_error.value.detail == 'Recording path is outside storage root'
    assert caplog.records == []


def test_all_readers_share_relative_path_and_configured_root_semantics(tmp_path, monkeypatch):
    from app.api.v1.endpoints import recordings
    from app.api.v1.endpoints.admin import media
    from app.domain.services import media_privacy

    monkeypatch.chdir(tmp_path)
    root = tmp_path / 'recordings'
    root.mkdir()
    audio = root / 'call.wav'
    audio.write_bytes(b'audio')
    monkeypatch.setenv('LOCAL_RECORDINGS_DIR', './recordings')
    expected = os.path.realpath(str(audio))
    for validator in (media_privacy.validated_recording_path, recordings._validated_recording_path, media._validated_recording_path):
        assert validator('recordings/call.wav') == expected
        # Keep the existing read-validator behavior: later file checks decide
        # whether the root itself is a file. This extraction does not widen it.
        assert validator('recordings') == os.path.realpath(str(root))


@pytest.mark.parametrize('escape', ['sibling-prefix', 'dot-dot', 'cross-drive'])
def test_dsar_rejects_outside_paths_before_open(tmp_path, monkeypatch, escape):
    dsar = importlib.import_module('app.domain.services.dsar_service')
    root = tmp_path / 'recordings'
    root.mkdir()
    outside = tmp_path / 'recordings-neighbor' / 'private.wav'
    outside.parent.mkdir()
    outside.write_bytes(b'private')
    monkeypatch.setenv('LOCAL_RECORDINGS_DIR', str(root))
    raw = str(root / '..' / 'recordings-neighbor' / 'private.wav') if escape == 'dot-dot' else str(outside)
    if escape == 'cross-drive':
        def different_drives(_paths):
            raise ValueError('Paths are on different drives')
        monkeypatch.setattr(os.path, 'commonpath', different_drives)
    opened = []
    original_open = os.open

    def observe_open(*args, **kwargs):
        opened.append(args[0])
        return original_open(*args, **kwargs)

    monkeypatch.setattr(os, 'open', observe_open)
    output = io.BytesIO()
    with pytest.raises(ValueError, match='outside storage root'):
        dsar.DsarArtifactReader()._copy_sync('recordings_s3', {'s3_bucket': 'local', 's3_key': raw}, output, 64)
    assert opened == [] and output.getvalue() == b''


def test_shared_boundary_resolves_symlink_target_before_comparing(tmp_path, monkeypatch):
    from app.domain.services import media_privacy

    root, outside = tmp_path / 'recordings', tmp_path / 'outside'
    root.mkdir()
    outside.mkdir()
    (outside / 'private.wav').write_bytes(b'private')
    owned = root / 'owned.wav'
    owned.write_bytes(b'owned')
    escaped, valid = root / 'escaped', root / 'owned-link.wav'
    try:
        escaped.symlink_to(outside, target_is_directory=True)
        valid.symlink_to(owned)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f'OS cannot create test symlinks/junctions: {exc}')
    monkeypatch.setenv('LOCAL_RECORDINGS_DIR', str(root))
    with pytest.raises(media_privacy.RecordingPathOutsideRoot):
        media_privacy.validated_recording_path(str(escaped / 'private.wav'))
    assert media_privacy.validated_recording_path(str(valid)) == os.path.realpath(str(owned))


@pytest.mark.parametrize('tenant,held', [(TENANT, True), (OTHER, False)])
async def test_hold_api_and_domain_use_identical_tenant_bound_semantics(tenant, held):
    from app.api.v1.endpoints.admin import media
    from app.domain.services import media_privacy

    expected = 'SELECT ' + media_privacy.ACTIVE_COMPLIANCE_HOLD_SQL.format(tenant_expression='$1')
    assert media._ACTIVE_COMPLIANCE_HOLD_SQL == media_privacy.ACTIVE_COMPLIANCE_HOLD_SQL
    flat = ' '.join(expected.split())
    for predicate in ("se.suspension_type = 'COMPLIANCE'", 'se.is_active = TRUE', 'se.restored_at IS NULL',
                      '(se.suspended_until IS NULL OR se.suspended_until > NOW())', "se.target_type = 'tenant'",
                      'se.target_id = $1', "se.target_type = 'partner'", 'held_tenant.id = $1',
                      'held_tenant.white_label_partner_id = se.target_id'):
        assert predicate in flat
    for reader in (media_privacy.has_active_compliance_hold, media._has_active_compliance_hold):
        port = SimpleNamespace(fetchval=AsyncMock(return_value=held))
        assert await reader(port, tenant) is held
        port.fetchval.assert_awaited_once_with(expected, tenant)


async def test_hold_query_failure_is_not_converted_to_no_hold():
    from app.api.v1.endpoints.admin import media
    from app.domain.services import media_privacy

    for reader in (media_privacy.has_active_compliance_hold, media._has_active_compliance_hold):
        unavailable = RuntimeError('synthetic database unavailable')
        port = SimpleNamespace(fetchval=AsyncMock(side_effect=unavailable))
        with pytest.raises(RuntimeError) as error:
            await reader(port, TENANT)
        assert error.value is unavailable
