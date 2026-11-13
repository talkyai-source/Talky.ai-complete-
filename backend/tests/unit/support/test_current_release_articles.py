"""F17-01: the deployed importer must not republish withdrawn help claims."""
import importlib.util
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[3]


@pytest.fixture
def shipped_sections():
    spec = importlib.util.spec_from_file_location("current_release_article_import", BACKEND / "scripts/import_support_articles.py")
    importer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(importer)
    # Actual default deployed source/parser, without main(), environment or DB.
    return importer.load_sections(importer.pick_source(None, None))


@pytest.mark.parametrize("page", ["support", "partners"])
def test_default_shipped_import_withholds_disabled_feature_workflows(shipped_sections, page):
    # QA F17-01: the support desk and partner administration are off in this
    # release; their help pages are withheld, so the support bot must not
    # republish them either.
    assert not [section for section in shipped_sections if section.url.split("#")[0] == f"/docs/{page}"], (
        f"Do not republish the withheld {page} workflow through the backend support bot"
    )


def test_default_shipped_import_does_not_reintroduce_trial_and_carrier_billing_promises(shipped_sections):
    import re

    text = "\n".join(section.body_md for section in shipped_sections)
    assert "Start Free Trial" not in text
    assert not re.search(r"carrier[^\n]*(?:are|remain|may be) billed separately", text, re.I)
    assert "Developer Hub" not in text
    assert re.search(r"pay.as.you.go", text, re.I)


def test_release_checklist_requires_enabled_feature_review_and_bundle_import_receipt():
    text = (BACKEND / "docs/support_article_import.md").read_text(encoding="utf-8")
    assert "docs reviewed against enabled features" in text.lower()
    assert "SUPPORT_ENABLED" in text
    assert "white-label" in text.lower()
    assert "--dry-run" in text and "--apply" in text
    assert "owner" in text.lower()
