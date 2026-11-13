"""Production help import must work with only the backend deployment files."""
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.domain.services.support import articles as kb

BACKEND = Path(__file__).resolve().parents[3]
FRONTEND = BACKEND.parent / "Talk-Leee/src/app/docs/_content"
SHIPPED = BACKEND / "support_articles"


@pytest.fixture
def importer():
    spec = importlib.util.spec_from_file_location("shipped_article_import", BACKEND / "scripts/import_support_articles.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_backend_bundle_matches_every_registered_frontend_article():
    registry = (FRONTEND / "registry.ts").read_bytes()
    assert (SHIPPED / "registry.ts").is_file(), "Backend support article bundle is missing"
    assert (SHIPPED / "registry.ts").read_bytes() == registry
    names = {page.file for page in kb.parse_registry(registry.decode("utf-8"))}
    assert {p.name for p in SHIPPED.glob("*.md")} == names
    for name in names:
        assert (SHIPPED / name).read_bytes() == (FRONTEND / name).read_bytes(), name


def test_default_import_uses_shipped_files_without_frontend_or_git(importer, monkeypatch, tmp_path):
    def no_git(*args, **kwargs):
        pytest.fail("Default deployed help import must not depend on git")
    monkeypatch.setattr(importer.subprocess, "run", no_git)
    monkeypatch.setattr(importer, "REPO_ROOT", tmp_path)
    source = importer.pick_source(None, None)
    assert source.docs_dir == SHIPPED
    assert len(importer.load_sections(source)) > 100


def test_import_defaults_to_parse_only_even_with_database_environment(importer, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@example.invalid/never-connect")
    run = AsyncMock(side_effect=AssertionError("default import must not write"))
    monkeypatch.setattr(importer, "run", run)
    assert importer.main([]) == 0
    run.assert_not_awaited()
    assert "database not touched" in capsys.readouterr().out


def test_explicit_apply_uses_existing_idempotent_upsert(importer, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@example.invalid/never-connect")
    run = AsyncMock(return_value=kb.ImportSummary(unchanged=123))
    monkeypatch.setattr(importer, "run", run)
    assert importer.main(["--apply"]) == 0
    run.assert_awaited_once()
    assert "unchanged=123" in capsys.readouterr().out


def test_apply_and_dry_run_are_mutually_exclusive(importer):
    with pytest.raises(SystemExit) as exc:
        importer.main(["--apply", "--dry-run"])
    assert exc.value.code == 2
