# API versioning & deprecation policy

How we evolve the public HTTP/WS API without breaking integrators.

## Versioning scheme

- The API version lives in the URL: `/api/v{N}/...`. The current version is **v1**.
- A new major version (`v2`) is cut **only** when we need a breaking change.
  Additive changes (new fields, new endpoints, new optional params) ship into the
  current major.
- Server software (this repo) follows SemVer independently of the API version.

## What counts as breaking

Breaking (requires a new major version):
- Removing or renaming an endpoint, field, query param, or header
- Changing a field's type or required-ness
- Tightening validation (rejecting input previously accepted)
- Changing default behaviour or default values
- Changing an error `code` for a given condition

Non-breaking (ship in the current major):
- Adding new endpoints, fields, query params, headers
- Adding new error `code` values
- Loosening validation
- Performance / internal changes

## Deprecation lifecycle

When we need to retire something in the current major:

1. **Announce.** Add to `CHANGELOG.md` under `### Deprecated` with the targeted
   sunset date (minimum **6 months** out).
2. **Mark.** On every response from the deprecated endpoint:
   - `Deprecation: true`  (RFC 9745)
   - `Sunset: <RFC 1123 date>`  (RFC 8594)
   - `Link: <docs-url>; rel="deprecation"`
3. **Document.** Add a callout in the OpenAPI description for the route.
4. **Log.** Increment a `deprecated_endpoint_hits_total{path,tenant}` Prometheus
   counter so we can see who is still calling it and notify them.
5. **Remove.** Only after the sunset date AND after counter has been at zero for
   at least 30 days. Removal lands in the next major version.

## Communicating with integrators

- `CHANGELOG.md` is the canonical record.
- Major versions get a migration guide in `docs/migrations/v{N-1}-to-v{N}.md`.
- Security-driven removals can shorten the 6-month window — minimum 30 days
  with direct outreach to known integrators.

## Internal review

Any PR introducing an API change must answer in the description:

- [ ] Is this additive or breaking?
- [ ] If breaking: which version does it land in?
- [ ] If deprecating: sunset date set? Headers wired? Counter added?
- [ ] OpenAPI schema artefact in CI updated?
