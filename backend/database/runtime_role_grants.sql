-- Rights for the application's runtime database role (one copy; the clean-install
-- runbook and tests/integration/test_clean_install.py both use this exact text).
--
-- Run it as the OWNER role (the one that runs the migrations), connected to the
-- application database, AFTER `alembic upgrade head`. The runtime role must be
-- created first with: LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE
-- and must not be a member of the owner role. If your role is not called
-- talky_runtime, replace that name everywhere below before running.
--
-- Row-level security does the tenant isolation; these grants only let the role
-- reach the tables. The default-privilege lines cover tables, sequences and
-- functions the owner creates in later migrations.

GRANT USAGE ON SCHEMA public TO talky_runtime;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO talky_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO talky_runtime;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO talky_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO talky_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO talky_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO talky_runtime;
