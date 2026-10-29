#!/usr/bin/env python3
"""Import the public docs (Talk-Leee docs markdown) as support help articles.

    DATABASE_URL=postgresql://... python scripts/import_support_articles.py
    python scripts/import_support_articles.py --dry-run     # parse only, no database

Idempotent and safe on every deploy: unchanged sections are not touched, staff
articles are never overwritten, and docs sections that disappeared are
unpublished (never deleted).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.getcwd())
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.services.support import articles as kb  # noqa: E402

DEFAULT_DOCS_DIR = Path(__file__).resolve().parents[2] / "Talk-Leee" / "src" / "app" / "docs" / "_content"


def load_sections(docs_dir: Path) -> list[kb.ArticleSection]:
    registry = docs_dir / "registry.ts"
    if not registry.is_file():
        raise SystemExit(f"registry.ts not found in {docs_dir}")
    try:
        pages = kb.parse_registry(registry.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    sections: list[kb.ArticleSection] = []
    for page in pages:
        path = docs_dir / page.file
        if not path.is_file():
            raise SystemExit(f"registry lists {page.file} but it does not exist in {docs_dir}")
        sections.extend(kb.split_sections(path.read_text(encoding="utf-8"),
                                          page_slug=page.slug, page_title=page.title))
    if not sections:
        raise SystemExit("no sections were produced from the docs")
    return sections


async def run(sections: list[kb.ArticleSection], dsn: str) -> kb.ImportSummary:
    import asyncpg
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2, timeout=10)
    try:
        return await kb.upsert_docs_articles(pool, sections)
    finally:
        await pool.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--docs-dir", type=Path, default=DEFAULT_DOCS_DIR)
    parser.add_argument("--dry-run", action="store_true", help="parse and print counts only; no database")
    args = parser.parse_args(argv)

    sections = load_sections(args.docs_dir)
    if args.dry_run:
        print(f"support articles dry-run: {len(sections)} sections parsed, database not touched")
        return 0
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL is required (or use --dry-run)", file=sys.stderr)
        return 2
    summary = asyncio.run(run(sections, dsn))
    print(f"support articles: parsed={len(sections)} inserted={summary.inserted} updated={summary.updated} "
          f"unchanged={summary.unchanged} unpublished={summary.unpublished} "
          f"skipped_staff={len(summary.skipped_staff_slugs)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
