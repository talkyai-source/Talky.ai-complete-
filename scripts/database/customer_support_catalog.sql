-- Read-only schema metadata. No application rows, credential values, or counts.
-- Run inside the existing approved read-only catalog observation transaction.
WITH reviewed(name) AS (VALUES
  ('audit_chain_state'), ('audit_event_types'), ('emergency_access_requests'),
  ('rate_limit_events'), ('suspension_propagation_queue'),
  ('tenant_ai_configs_backup_20260907'), ('tenant_ai_credentials'),
  ('tenant_recording_policy'), ('tenant_telephony_credentials'), ('worker_status')
), relations AS (
  SELECT c.*, pg_get_userbyid(c.relowner) AS owner_name
  FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  JOIN reviewed r ON r.name=c.relname WHERE n.nspname='public'
)
SELECT jsonb_build_object(
  'telephony_selection_column', (SELECT jsonb_build_object(
    'owner', pg_get_userbyid(c.relowner), 'name', a.attname,
    'type', format_type(a.atttypid,a.atttypmod), 'not_null', a.attnotnull,
    'default', pg_get_expr(d.adbin,d.adrelid),
    'constraints', (SELECT jsonb_agg(jsonb_build_object('name', x.conname,
      'validated', x.convalidated, 'definition', pg_get_constraintdef(x.oid,true)))
      FROM pg_constraint x WHERE x.conrelid=c.oid AND a.attnum=ANY(x.conkey)))
    FROM pg_class c JOIN pg_attribute a ON a.attrelid=c.oid
    LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
    WHERE c.oid='public.tenants'::regclass AND a.attname='active_telephony_provider'
      AND a.attnum>0 AND NOT a.attisdropped),
  'tables', COALESCE((SELECT jsonb_agg(jsonb_build_object(
    'name', c.relname, 'kind', c.relkind, 'owner', c.owner_name,
    'rls', c.relrowsecurity, 'force_rls', c.relforcerowsecurity,
    'acl', c.relacl::text,
    'columns', (SELECT jsonb_agg(jsonb_build_object(
      'name', a.attname, 'type', format_type(a.atttypid,a.atttypmod),
      'not_null', a.attnotnull, 'identity', a.attidentity, 'generated', a.attgenerated,
      'default', pg_get_expr(d.adbin,d.adrelid)) ORDER BY a.attnum)
      FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
      WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped),
    'constraints', (SELECT jsonb_agg(jsonb_build_object(
      'name', x.conname, 'type', x.contype, 'validated', x.convalidated,
      'definition', pg_get_constraintdef(x.oid,true)) ORDER BY x.conname)
      FROM pg_constraint x WHERE x.conrelid=c.oid),
    'indexes', (SELECT jsonb_agg(jsonb_build_object(
      'name', ic.relname, 'definition', pg_get_indexdef(i.indexrelid),
      'valid', i.indisvalid, 'ready', i.indisready) ORDER BY ic.relname)
      FROM pg_index i JOIN pg_class ic ON ic.oid=i.indexrelid WHERE i.indrelid=c.oid),
    'policies', (SELECT jsonb_agg(jsonb_build_object(
      'name', p.polname, 'command', p.polcmd, 'permissive', p.polpermissive,
      'roles', p.polroles::text, 'using', pg_get_expr(p.polqual,p.polrelid),
      'check', pg_get_expr(p.polwithcheck,p.polrelid)) ORDER BY p.polname)
      FROM pg_policy p WHERE p.polrelid=c.oid),
    'triggers', (SELECT jsonb_agg(jsonb_build_object(
      'name', t.tgname, 'enabled', t.tgenabled, 'definition', pg_get_triggerdef(t.oid,true),
      'function', t.tgfoid::regprocedure::text, 'function_owner', pg_get_userbyid(f.proowner),
      'security_definer', f.prosecdef, 'function_body', f.prosrc,
      'function_settings', f.proconfig) ORDER BY t.tgname)
      FROM pg_trigger t JOIN pg_proc f ON f.oid=t.tgfoid
      WHERE t.tgrelid=c.oid AND NOT t.tgisinternal),
    'incoming_foreign_keys', (SELECT jsonb_agg(jsonb_build_object(
      'table', x.conrelid::regclass::text, 'name', x.conname,
      'definition', pg_get_constraintdef(x.oid,true)) ORDER BY x.conrelid::regclass::text,x.conname)
      FROM pg_constraint x WHERE x.confrelid=c.oid AND x.contype='f')
  ) ORDER BY c.relname) FROM relations c),'[]'::jsonb),
  'missing', (SELECT jsonb_agg(r.name ORDER BY r.name) FROM reviewed r
    WHERE NOT EXISTS (SELECT 1 FROM relations c WHERE c.relname=r.name)),
  'referencing_routines', (SELECT jsonb_agg(jsonb_build_object(
    'name', f.oid::regprocedure::text, 'owner', pg_get_userbyid(f.proowner),
    'security_definer', f.prosecdef, 'body', f.prosrc,
    'attached_triggers', (SELECT jsonb_agg(jsonb_build_object(
      'table', t.tgrelid::regclass::text, 'name', t.tgname))
      FROM pg_trigger t WHERE t.tgfoid=f.oid AND NOT t.tgisinternal)) ORDER BY f.oid::regprocedure::text)
    FROM pg_proc f WHERE f.pronamespace='public'::regnamespace
    AND EXISTS (SELECT 1 FROM reviewed r WHERE f.prosrc LIKE '%' || r.name || '%')),
  'dependent_views', (SELECT jsonb_agg(DISTINCT jsonb_build_object(
    'table', c.relname, 'view', rw.ev_class::regclass::text))
    FROM relations c JOIN pg_depend d ON d.refobjid=c.oid AND d.refclassid='pg_class'::regclass
    JOIN pg_rewrite rw ON d.classid='pg_rewrite'::regclass AND d.objid=rw.oid
    WHERE rw.ev_class<>c.oid)
);
