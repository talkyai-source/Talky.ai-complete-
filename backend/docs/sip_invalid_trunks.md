# Invalid SIP trunk isolation — F4-10

Create and update requests reject C0 controls, DEL and C1 controls in trunk name, SIP host, authentication username and password. Raw values are checked before host trimming/canonicalisation, so a leading or trailing newline cannot disappear before validation. Ordinary credential spaces, punctuation and Unicode text remain unchanged.

The reconciler also validates persisted trunk values before constructing the shared account/DID/carrier-host routing maps. A projection/renderer `ValueError` skips that trunk's candidate file and routes while healthy trunks continue. Invalid duplicate join rows produce one marker. This ordering prevents an invalid first row from taking a healthy tenant's verified shared-carrier account route.

Each check/apply JSON includes an `invalid_trunks` list, for example `trunk-<UUID>:invalid_configuration`. A warning logs the same UUID and fixed reason. It does not log the row's name, host, username, ciphertext, decrypted password or exception text. This is a visible per-tick configuration diagnostic; it does **not** persist a new database status or rewrite/deactivate the trunk row.

Decryption/key failures remain fatal even when they raise `ValueError`. Invalid identities, contradictory joined source rows, unsafe assignment proofs and unexpected infrastructure failures also retain their existing fail-closed behavior. The change does not bypass shared-carrier assignment proof, dynamic extension checks, candidate-digest approval, channel-idle proof, reload/runtime verification or rollback.

An invalid trunk is excluded from the complete desired candidate set. Consequently an approved apply can remove its prior managed file; it does not preserve stale credentials/configuration as an implicit fallback. Review the normal missing/changed/unexpected file summary and `invalid_trunks` together.

Owner workflow, from the deployed backend directory with its existing operator-provisioned environment:

```bash
cd /opt/talky/backend
venv/bin/python scripts/reconcile_pjsip_configs.py
```

Correct the named trunk through the authenticated SIP-trunk update API using clean values, then repeat the check. Do not paste credentials into logs or tickets. Once the candidate is reviewed and existing apply prerequisites hold, use the digest from that new check:

```bash
venv/bin/python scripts/reconcile_pjsip_configs.py --apply --expected-digest '<digest-from-reviewed-check>'
```

The command remains check-only by default. No production command, carrier request, Asterisk reload or existing trunk repair was performed by this remediation; local tests use synthetic rows, secrets and temporary candidate directories.
