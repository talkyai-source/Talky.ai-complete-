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

## Release checklist: docs reviewed against enabled features

- [ ] The owner has compared the published help against the enabled release,
  including paid subscription/pay-as-you-go choices and actual carrier-service
  arrangements. Help text must not promise a free trial or an automated billing
  workflow that has not been verified.
- [ ] `SUPPORT_ENABLED`, tenant eligibility, staff readiness and ticket/mail
  delivery have been checked on the intended deployment before publishing a
  support workflow. The default remains disabled; no production flag was read
  or changed during this remediation.
- [ ] White-label workflows have been demonstrated in the deployed UI before
  publishing the partner guide. The current unavailable screens are not an
  enabled feature. The safe content correction replaces `support.md` and
  `partners.md` workflows with availability notices in both public help and the
  backend bundle; it does not withdraw those published routes.
- [ ] Resolve the remaining publication withdrawal with the owner/Hisham.
  `public-routes.ts` and its exact-sitemap test were changed by Hisham in
  `078a6ad3` on 4 Nov and remain protected. Hiding the guides requires a coordinated
  review of the shared public catalog, sitemap expectation, docs registry and
  any footer links. The prepared withdrawal patch and original tests are kept
  in `tmp/qa-round3-20261106/H-F17-01-blocked-publication/`; they are not shipped.
- [ ] Run the existing bundle parity tests and importer `--dry-run` on the exact
  reviewed release; record the section count and approve `--apply` separately.
  Verify that public links and imported search results match the current text.
- [ ] Review existing database articles independently. This source change has
  not removed, unpublished or deleted any deployed database row. A later approved
  full import marks missing `source='docs'` slugs unpublished; it does not delete
  them and preserves staff-owned articles. Check for old support/partner claims,
  staff-authored copies and cached results, and disable any remaining stale
  articles through the reviewed owner workflow before enabling support.

Replacing either availability notice with workflow instructions requires the
same owner review and fresh parity/import evidence. Publication withdrawal is
still unfinished. These content checks do not prove production support delivery
or commercial terms.
