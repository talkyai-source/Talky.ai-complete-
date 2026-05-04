# Changelog

All notable changes are recorded here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [SemVer](https://semver.org/) — `MAJOR.MINOR.PATCH`.

## [Unreleased]

### Added
- Production-readiness pass: security headers middleware, request-ID propagation,
  pre-commit hooks, Dependabot, dependency lock workflow, root `docker-compose.yml`,
  CI coverage threshold, OpenAPI schema export in CI, Alembic round-trip test in CI,
  k6 load test scaffold, Vector log shipper config, Grafana dashboard template,
  standardized error envelope (`{"error": {"code", "message", "details", "request_id"}}`),
  `Retry-After` header on 429 responses, per-tenant rate-limit key resolvers.
- Docs: `ARCHITECTURE.md`, `DEPLOYMENT.md`, `RUNBOOK.md`, `API_VERSIONING.md`.

### Changed
- CI security scans (Bandit, pip-audit) are now blocking — no more `--exit-zero` /
  `|| true`. Findings fail the build until reviewed and either fixed or
  explicitly ignored with documented justification.
- Logs now include `[req=<uuid>]` correlation tag on every line.

### Security
- Strict CSP, HSTS (production only), X-Frame-Options, X-Content-Type-Options,
  Referrer-Policy, and Permissions-Policy applied to every response.

## [1.0.0]

Initial production release of the Talky.ai backend.
