# Encrypted database and recording copies

The production producer remains `backend/deploy/db-backup.sh`: its protected
principal check and exported snapshot are unchanged. After local archive/list/
SHA-256 verification, an explicitly configured destination triggers
`scripts/database/offhost.py publish --dump <published dump>`. A failed copy or
readback fails the backup unit and skips local retention. It preserves the new
verified local dump for investigation/retry. An unset `TALKY_OFFHOST_ROOT`
performs no upload and preserves existing local-only behavior.

`payload.tar.gpg` contains the database dump, every captured regular file beneath
the recordings directory, and a manifest of file sizes/hashes. Media names and
contents stay inside the encrypted payload. The public receipt has only counts,
times and hashes. No recording file is deleted. GPG uses the AES256, iterated
SHA512 S2K primitive selected from `a0bd0353`; there is no clear-copy option.

This is a symmetric passphrase design. The source host needs the passphrase to
write and verify copies. Owner escrow must exist independently of that host; it
does not provide the separate private-decryption-key property of public-key
encryption. Never put the key value in a command, receipt, repository or report.

## Owner configuration before upload

Nothing below has been executed against a server by this change.

1. Provision a separate-host NFS/NFS4, CIFS or SSHFS mount with authenticated
   access and an owner-reviewed transport policy. Use a private destination
   directory outside `/home` and `/root`. Do not substitute a second directory
   or disk on the application host. Mount source/type are pinned and rechecked
   with `findmnt`; loopback/local filesystems are refused. A remote mount proves
   a filesystem/source boundary, not provider/region/account independence.
2. Escrow a strong passphrase off the application host. Store the upload copy
   in a root-owned mode0600 file, at least24 characters and at most4096 bytes.
   GPG must already be installed; this patch installs no package.
3. Add these **paths/identifiers, not key material**, to the existing root-only
   `/etc/talky/db-backup.env`. Keep its existing `TALKY_BACKUP_PRINCIPAL_ID`.

```text
TALKY_OFFHOST_ROOT=/mnt/owner-backup/talky
TALKY_OFFHOST_SOURCE=backup.example.invalid:/approved-export
TALKY_OFFHOST_FSTYPE=nfs4
TALKY_OFFHOST_PASSPHRASE_FILE=/etc/talky/offhost.passphrase
TALKY_RECORDINGS_DIR=/opt/talky/backend/recordings
```

The `.invalid` source is an example and must be replaced by the owner, exactly as
`findmnt` reports it. `TALKY_RECORDINGS_DIR` must equal the live service's
`LOCAL_RECORDINGS_DIR` resolution. The default is `/opt/talky/backend/recordings`
for the installed layout; the tool never loads the application `.env` file.

4. Confirm sufficient private local staging space for dump+media+manifest and
   remote capacity for a complete encrypted copy. Inventory is capped at100,000
   files and a16MiB manifest; exceeding either fails rather than silently omits
   files. Symlinks, inaccessible directories, unsafe names and observed input
   changes also fail. The copy uses a per-destination exclusive publication
   lock. After an interrupted run, an owner must prove no writer is running
   before removing `.talky-offhost-publish.lock`; no automatic stale-lock guess
   or remote backup deletion occurs.
5. Arrange a consistent source point: quiesce writers or use a reviewed
   filesystem snapshot. The DB snapshot and filesystem walk are separate; an
   unchanged file does not prove a cross-resource atomic snapshot.

The installer links/enables only the read-only
`talklee-db-backup-health.timer`. Its root service reads the actual flat
`/var/backups/talky` format and new encrypted remote format, verifies the newest
checksums and uses original snapshot age (26h maximum). Missing/unconfigured
off-host protection is a failure, not a green local-only result. Item16's
`OnFailure=talky-notify@%n.service` handles delivery. The old generic
`talklee-db-backup.service`/`scheduled_backup.py` bundle producer remains a
separate manual tool and is not installed as a replacement producer.

## Verification and safe recovery

After loading the owner-reviewed root environment through the unit, use its
normal schedule or explicitly start `talky-db-backup.service`; inspect the
exit status and the protected journal. Then start
`talklee-db-backup-health.service` and verify the alert receiver independently.
No success claim follows merely from a timer being installed.

In a disposable recovery workspace with a fresh output directory:

```bash
umask 077
python3 scripts/database/offhost.py restore-payload \
  --bundle /mnt/owner-backup/talky/talky-YYYYMMDDTHHMMSSZ \
  --destination /srv/disposable-recovery/payload \
  --passphrase-file /secure/recovery-escrow/passphrase
```

The command verifies ciphertext, decrypts privately, checks every payload path
and member hash, and refuses an existing output directory. It neither connects
to a database nor overwrites live media. The recovered dump must then pass an
isolated PostgreSQL restore and application/schema/tenant checks; this command
alone is not a restore acceptance receipt. Key/connector/TOTP encryption escrow
is separate from this backup-encryption key.

Drills must include wrong key, altered remote ciphertext, stale snapshot,
unmounted/mismatched destination, loss of source host, missing recording and
clean-room decryption. Record actual elapsed time and artifact hashes. No live
remote upload, outage drill or production restore has been performed here.

To stop future uploads, remove only `TALKY_OFFHOST_ROOT` from the protected unit
configuration and retain backups/keys. Local backups continue, and health
correctly reports that off-host protection is unconfigured. No data rollback or
automatic remote retention is provided.

See [PITR preparation and proposed recovery targets](PITR.md). Daily logical
backup plus a local WAL tool does not meet a15-minute off-host RPO by itself.

## FTP copies (`offsite_ftp.py`, owner decision 12 Nov 2026)

When `TALKY_FTP_BACKUP_URL` is set in `/etc/talky/db-backup.env`, every nightly
dump also goes to the owner's FTP server, after the local dump is published:

1. It is encrypted with the same gpg AES256 primitive (`TALKY_FTP_PASSPHRASE_FILE`,
   mode 0600, 24+ characters). The owner keeps a copy of that passphrase off the
   server: without it no FTP copy can be restored.
2. The encrypted copy must decrypt to the dump's exact bytes before upload.
3. It is uploaded with TLS required (`curl --ssl-reqd`, certificate checked).
   The login is read only from `TALKY_FTP_NETRC` (mode 0600).
4. Its size is confirmed on the server. Copies older than `TALKY_FTP_KEEP_DAYS`
   (default 30) are deleted, but the newest three always stay.

A failed copy fails `talky-db-backup.service`, and local retention waits, as
with the mount-based copy above.

Settings in `/etc/talky/db-backup.env` (values are examples):

```
TALKY_FTP_BACKUP_URL=ftp://ftp.example.invalid/talky-db-backups/
TALKY_FTP_NETRC=/etc/talky/offsite-ftp.netrc
TALKY_FTP_PASSPHRASE_FILE=/etc/talky/offsite-backup.passphrase
TALKY_FTP_KEEP_DAYS=30
```

**Prove a restore works** (on the server, as root, with the same settings):
`set -a; . /etc/talky/db-backup.env; set +a; python3 scripts/database/offsite_ftp.py verify`.
This downloads the newest copy, decrypts it, and checks it against the local `.sha256`.

**Restore after losing the server:**
1. Download the newest `talky-<stamp>.dump.gpg` from the FTP server.
2. Decrypt it: `gpg --batch --pinentry-mode loopback --passphrase-file <passphrase> -d talky-<stamp>.dump.gpg > talky-<stamp>.dump`.
3. Restore it into a NEW database: see docs/RUNBOOK.md "Restore test".
