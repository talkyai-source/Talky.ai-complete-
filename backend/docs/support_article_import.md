# Backend-shipped help articles (F11-03 / F18-01)

`backend/support_articles/` contains the registered public help source shipped
with this backend release. Production needs neither the frontend directory nor
Git objects. `test_shipped_articles.py` compares the registry, registered file
set and every byte against `Talk-Leee/src/app/docs/_content/`. When changing
those help pages, update the backend copy in the same reviewed change. This is
a packaging/synchronization guarantee, not a certification of every help claim.

The importer defaults to parsing and printing the section count, without opening
a database. `--dry-run` remains an explicit spelling of that default. `--apply`
uses the existing transactional content-hash upsert: unchanged rows remain
untouched, staff-owned articles are preserved, and removed docs sections become
unpublished, never deleted. A missing/incomplete source fails before writes.
`--docs-dir` and `--git-ref` remain explicit operator overrides; the default
does not silently fall back to a possibly different frontend or Git revision.

Owner procedure, after installing the reviewed backend on the intended host:

```sh
cd /opt/talky/backend
.venv/bin/python scripts/import_support_articles.py --dry-run
# Supply DATABASE_URL through the owner's existing protected environment.
# This command writes public help rows; approve the parsed count/source first.
.venv/bin/python scripts/import_support_articles.py --apply
```

Check the persisted published count using the owner's preconfigured libpq
service `talky-owner` (a local credential profile, not a password argument):

```sh
psql 'service=talky-owner' -X -v ON_ERROR_STOP=1 -c "SELECT source, count(*) FROM public.support_kb_articles WHERE published GROUP BY source ORDER BY source;"
.venv/bin/python scripts/import_support_articles.py --apply
```

The second import should report zero inserted/updated/unpublished and the
expected unchanged count. Verify a support search returns a known shipped
article. Inspect current failed units before clearing only the historical import
attempts; do not erase evidence of unrelated failures:

```sh
systemctl --failed --no-pager
sudo systemctl reset-failed <confirmed-old-import-run.service>
systemctl --failed --no-pager
```

Substitute each actual historical import unit after inspecting its journal.
Do not reset a fresh failure to make a release gate appear green. The retired
trunk-status timer is handled by its installer policy, not re-enabled here.
Import, production count, support search and failed-unit clearance are owner
steps; none has been performed against production by this work.
