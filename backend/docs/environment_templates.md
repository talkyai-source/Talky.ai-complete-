# Generated environment examples and timer verification

Both repository `.env.example` files are generated inventories. Every assignment
is deliberately empty. They are not ready-to-run production configurations:
the owner must select the relevant settings, set the actual environment mode,
and supply required values through protected deployment configuration.

From the repository root, using the project's Python environment:

```bash
python backend/scripts/generate_env_example.py
python backend/scripts/generate_env_example.py --inventory
python backend/scripts/generate_env_example.py --check
python backend/scripts/generate_env_example.py --write
```

The default prints the proposed template without writing. `--inventory` prints
only supplemental environment names and source locations, including dynamic
reads that could not be enumerated. `--check` returns nonzero if either template
is missing or stale. `--write` replaces only root `.env.example` and
`backend/.env.example`; it never writes a real `.env` or process configuration.
Regenerate after changing Settings, application environment consumers or root
Compose interpolation, then review and commit both examples with the change.

The first section comes from `Settings.model_fields`, including accepted field
aliases. The generator never constructs Settings, calls `get_settings`, reads a
real `.env`, or outputs model defaults, environment values or secret values.
The second section comes from literal `os.getenv`/`os.environ` reads in
`backend/app` (including import aliases) and root `docker-compose.yml`
interpolation. Supplemental names are not automatically validated by Settings.
The source inventory is conservative: dynamic keys, custom environment wrapper
functions, deployment shells and other language consumers need separate review.
It does not claim the historical QA count of 389 is today's complete count.

The identical root/backend inventories do not make their runtime consumers
interchangeable. Root Compose still configures its same-host PostgreSQL/Redis
profile; production backend processes use their existing protected configuration
and database topology. Do not replace an existing production configuration with
an empty example, or select the local Compose database by copying it blindly.

`tests/unit/test_env_example_generation.py` checks both generated files against
current model metadata and source names. It also introduces a synthetic field
and proves that an omitted setting fails the check. This is a maintenance gate,
not evidence that production secrets or provider configuration are valid.

## Timer check during deployment

`deploy_to_server.sh` invokes the following after application/timer starts and
before readiness checks and success:

```bash
sudo bash backend/deploy/verify-service-timers.sh
```

The helper discovers every current `backend/systemd/*.timer`, including the
model-catalog and recording-discrepancy timers, so adding a file requires no
separate verification allowlist. Ordinary timers must be loaded, persistently
enabled and active. A runtime-only enablement is insufficient for reboot
persistence. Empty inventory, missing units and unknown/failed states fail the
deployment check.

The sole explicit retirement exception is `talky-trunk-status.timer`: its
long-running service owns the loop. The old timer must be disabled/masked (or
absent) and inactive. The verifier never enables, starts, stops or reloads a
unit. If a required timer is inactive, the owner must inspect its unit and
install/start configuration; the deployment does not silently repair it.

The helper's scope is the repository backend timer directory. Separately
prepared PITR/base-backup schedules and the offhost health timer under
`scripts/database/systemd` retain their own owner gates and verification.
No live host state or reboot persistence is established by the local stub tests.
A timer failure after migration uses the existing code-forward/manual recovery
instructions; it does not trigger an unsafe old-code rollback.
