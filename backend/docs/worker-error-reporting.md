# Worker error reporting — F15-03

The dialer, voice, reminder, cleanup and support entry points call the existing
`app.core.sentry_init.init_sentry()` before their worker configuration or work
starts. They retain existing logging/redaction and shutdown behavior. The
shared helper supplies asyncio/logging integrations and disables default PII;
no second SDK or email/alert stack is added.

Owner-managed process settings:

| Variable | Default | Purpose |
|---|---|---|
| `SENTRY_DSN` | Empty/unset | Enables the existing SDK for API and workers; without it initialization is a no-op and sends nothing. |
| `ENVIRONMENT` | `development` | Labels events; use `production` only in the production service configuration. |
| `SENTRY_RELEASE` | Best-effort checkout identity | Set the deployed commit explicitly for useful release attribution. |
| `SENTRY_TRACES_SAMPLE_RATE` | `0.01` | Trace sampling (0 disables traces). |
| `SENTRY_PROFILES_SAMPLE_RATE` | `0.0` | Profile sampling, disabled by default. |

Provision the DSN privately through the existing service environment; do not
put it in source or command-line arguments. Verify the SDK is installed from
the existing requirements. The helper logs `sentry_skipped reason=no_dsn` when
unconfigured. No configured destination or delivered event is claimed here.

For an owner-approved staging delivery check, run this under the same service
identity and provisioned environment, from the staging backend directory:

```bash
venv/bin/python -c 'from app.core.sentry_init import init_sentry, capture_exception; import sentry_sdk; assert init_sentry(), "Sentry is not configured"; capture_exception(RuntimeError("Synthetic worker reporting check; no customer data")); sentry_sdk.flush(timeout=5)'
```

This checks the shared sender only. Then the owner must exercise one synthetic
error in each actual staging worker process, confirm its receipt/release and
PII-free contents, and record the timestamps. Do not trigger customer calls,
messages or destructive cleanup to create an error. No Sentry request, DSN
read or real worker/provider run was performed during this fix. The unit
tests execute the actual main-function bodies with infrastructure stopped at
their first work boundary and use a fake SDK for sender behavior.

This code enables reporting when configured; it does not establish alert
delivery, on-call routing or the Admin frontend integration. Those remain
separate owner/frontend work.
