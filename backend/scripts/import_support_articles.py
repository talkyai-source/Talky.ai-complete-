#!/usr/bin/env python3
"""Import the public docs (Talk-Leee docs markdown) as support help articles.

    DATABASE_URL=postgresql://... python scripts/import_support_articles.py
    python scripts/import_support_articles.py --dry-run     # parse only, no database
    python scripts/import_support_articles.py --git-ref HEAD # read the docs from git objects

Idempotent and safe on every deploy: unchanged sections are not touched, staff
articles are never overwritten, and docs sections that disappeared are
unpublished (never deleted).

The production checkout is sparse and has no Talk-Leee/ working tree, so when
the docs folder is absent the docs are read from the repository's git objects
at HEAD (the deployed commit). ``--docs-dir`` and ``--git-ref`` force a source.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.services.support import articles as kb  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_PATH = "Talk-Leee/src/app/docs/_content"
DEFAULT_DOCS_DIR = REPO_ROOT / "Talk-Leee" / "src" / "app" / "docs" / "_content"
_PAGE_FILE = re.compile(r"^[a-z0-9][a-z0-9-]*\.md$")
_GIT_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")


def safe_page_file(name: str) -> str:
    """Registry entries are plain file names in the docs folder, nothing else."""
    if not _PAGE_FILE.match(name or ""):
        raise SystemExit(f"registry lists an unsafe or non-markdown file name: {name!r}")
    return name


class DirSource:
    def __init__(self, docs_dir: Path) -> None:
        self.docs_dir = docs_dir

    def describe(self) -> str:
        return str(self.docs_dir)

    def read(self, name: str) -> str | None:
        path = self.docs_dir / name
        return path.read_text(encoding="utf-8") if path.is_file() else None


class GitSource:
    def __init__(self, repo_root: Path, ref: str) -> None:
        if not _GIT_REF.match(ref):
            raise SystemExit(f"invalid git ref: {ref!r}")
        self.repo_root, self.ref = repo_root, ref

    def describe(self) -> str:
        return f"git {self.ref}:{DOCS_PATH}"

    def read(self, name: str) -> str | None:
        result = subprocess.run(["git", "-C", str(self.repo_root), "show", f"{self.ref}:{DOCS_PATH}/{name}"],
                                capture_output=True, timeout=60)
        if result.returncode != 0:
            return None
        return result.stdout.decode("utf-8")


def load_sections(source) -> list[kb.ArticleSection]:
    registry = source.read("registry.ts")
    if registry is None:
        raise SystemExit(f"registry.ts not found in {source.describe()}")
    try:
        pages = kb.parse_registry(registry)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    sections: list[kb.ArticleSection] = []
    for page in pages:
        text = source.read(safe_page_file(page.file))
        if text is None:
            raise SystemExit(f"registry lists {page.file} but it does not exist in {source.describe()}")
        sections.extend(kb.split_sections(text, page_slug=page.slug, page_title=page.title))
    if not sections:
        raise SystemExit("no sections were produced from the docs")
    return sections


def pick_source(docs_dir: Path | None, git_ref: str | None):
    if docs_dir is not None and git_ref is not None:
        raise SystemExit("use either --docs-dir or --git-ref, not both")
    if git_ref is not None:
        return GitSource(REPO_ROOT, git_ref)
    if docs_dir is not None:
        return DirSource(docs_dir)
    if (DEFAULT_DOCS_DIR / "registry.ts").is_file():
        return DirSource(DEFAULT_DOCS_DIR)
    return GitSource(REPO_ROOT, "HEAD")


async def run(sections: list[kb.ArticleSection], dsn: str) -> kb.ImportSummary:
    import asyncpg
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2, timeout=10)
    try:
        return await kb.upsert_docs_articles(pool, sections)
    finally:
        await pool.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--docs-dir", type=Path, default=None, help="read the docs from this folder")
    parser.add_argument("--git-ref", default=None, help="read the docs from git objects at this ref")
    parser.add_argument("--dry-run", action="store_true", help="parse and print counts only; no database")
    args = parser.parse_args(argv)

    source = pick_source(args.docs_dir, args.git_ref)
    sections = load_sections(source)
    if args.dry_run:
        print(f"support articles dry-run: {len(sections)} sections parsed from {source.describe()}, "
              "database not touched")
        return 0
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is required (or use --dry-run)", file=sys.stderr)
        return 2
    summary = asyncio.run(run(sections, dsn))
    print(f"support articles: source={source.describe()} parsed={len(sections)} inserted={summary.inserted} "
          f"updated={summary.updated} unchanged={summary.unchanged} unpublished={summary.unpublished} "
          f"skipped_staff={len(summary.skipped_staff_slugs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
