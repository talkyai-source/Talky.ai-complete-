# Current dependency audit — 28 October 2026

The release worktree at `ccd45f10` was audited before changing dependencies.
Only the dashboard lock entry for development dependency `qs` changes, from
`6.16.0` to `6.16.1`; the registry tarball and integrity value match the published
package metadata. Its existing parent `url@0.11.4` accepts `^6.12.3`, and its
dependencies, engines and license are unchanged. No package manifest, framework,
provider, audit exception or runtime dependency was changed.

## Findings and disposition

| Scope | Before patch | After patch |
|---|---|---|
| Admin full lock audit | Zero findings | Lock unchanged |
| Dashboard full lock audit | One moderate and four low paths; zero high/critical | Four low paths; zero moderate/high/critical |
| Dashboard runtime-only lock audit | Not separately rerun before patch | Zero findings |

Commands were `npm audit --package-lock-only --json` in each frontend and
`npm audit --package-lock-only --omit=dev --json` for the patched dashboard.
The full dashboard audit still exits nonzero because low-severity findings
remain. These are current feed results, not a guarantee against unknown issues.

The moderate `qs` finding is
[GHSA-6pq8-jhm2-2vcw](https://github.com/advisories/GHSA-6pq8-jhm2-2vcw): specially
shaped array values can throw during comma-format serialization. The advisory
identifies `6.16.1` as patched; the
[upstream release](https://github.com/ljharb/qs/releases/tag/v6.16.1) is published.
The lock marks this dependency and its `url` parent as development-only. The
patch removes this finding without changing the application dependency graph.

The four remaining low paths represent one underlying advisory,
[GHSA-gcqx-w48m-w2h4](https://github.com/advisories/GHSA-gcqx-w48m-w2h4), for
`deepmerge@4.3.1`, through `fork-ts-checker-webpack-plugin`,
`@storybook/builder-webpack5` and `@storybook/react-webpack5`. All are marked
development-only; the advisory publishes no patched version. Storybook is used
by the checked-in `.storybook/main.ts` configuration and package scripts, so it
was not deleted as an apparently unused dependency. This unresolved tooling
risk remains explicit. Do not expose its development server to untrusted users
or treat untrusted stories/configuration as safe input. No unsupported override,
downgrade or audit suppression was introduced.

The historical high-severity `braces` finding is superseded by the publisher's
23 October withdrawal of
[GHSA-vfj7-8cjw-p6xm](https://github.com/advisories/GHSA-vfj7-8cjw-p6xm), which says
it is not practically exploitable. Current audits no longer report it. Historical
October evidence is retained; its finding count is not today's dependency state.
The existing runtime-only dashboard CI policy remains unchanged.

## Backend evidence and the exact Linux lock

At `ccd45f10`, the Backend `pip-audit -r requirements.txt` step and Admin full
audit step succeeded in
[main CI run 40652309498](https://github.com/talkyai-source/Talky.ai-complete-/actions/runs/40652309498).
The Backend job subsequently failed outside its dependency audit; this is not
a claim that the complete run passed. Requirements resolution is distinct from
the installer's exact hashed Linux closure, whose provenance records 157
packages for Python 3.12/Linux x86_64.

The Ubuntu installer workflow now additionally audits that exact lock with
`pip-audit==2.10.1` in a separate temporary virtual environment. The command uses
`--require-hashes -r scripts/installation/requirements-linux-py312.lock` and
retains its JSON report. The existing locked runtime installation and `pip check`
remain unchanged. A vulnerability or tool/network failure fails the job; there
is no `--fix`, ignored vulnerability or success fallback. The official
[pip-audit usage documentation](https://github.com/pypa/pip-audit#usage) supports
requirements files, hash enforcement and JSON output. This new native audit
must pass on the final candidate before it supplies acceptance evidence.

## Validation boundary

Postpatch dashboard audits completed as recorded above. An isolated comparison
against the exact published `qs@6.16.0` reproduced three advisory failures:
own non-callable `toString` values with comma serialization, its
`encodeValuesOnly` branch, and a parsed query with `allowPrototypes`. The patched
`6.16.1` avoided all three exceptions. Three ordinary serialization cases
(default, comma and bracket arrays) retained identical output. This ran on
Windows with Node 26.8.1; it is package-level evidence, not native release proof.

An isolated temporary source export is used for locked installation checks
because the shared worktree's `node_modules` is a junction to the original
checkout. The first comparison refused its unexpected installed `qs@6.15.3`;
the successful comparison instead used a downloaded exact baseline in the
owned temporary directory. No shared module installation, production service
or existing database was modified. Workflow YAML parsing and diff whitespace
checks passed. The isolated `npm ci --include=dev --ignore-scripts --no-audit
--no-fund` attempt was interrupted during prolonged extraction to avoid adding
load to the shared machine; its partial installation is not claimed as a
successful clean install. The installed patched package was sufficient for the
bounded comparison above. A full Windows build was not started.
Final Linux CI must build the combined source and lock, including concurrent
white-label changes. This dependency repair does not close the remaining
operating or product acceptance gates.
