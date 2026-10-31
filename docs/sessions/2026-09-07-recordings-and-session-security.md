# 2026-09-07 — Recordings (why none since Aug 23, size/quality) and login/session hardening

## Recordings — what the evidence said (prod, read-only)

| Fact | Evidence |
|---|---|
| **No recording has been stored since 2026-08-23** | `recordings_s3`: 427 rows all-time, last `created_at` 2026-08-23; newest file in `/opt/talky/backend/recordings` dated Aug 23 12:16; 0 rows for any tenant since |
| Root cause: the recorder fails closed when the tenant has no `tenant_recording_policy` row | `recording_policy_service.decide` → `recording_policy_absent … recording disabled` (WARNING on every answered call for tenants `1845a165` and `790ca2db`). Only two dormant tenants have a policy row (`5e666d8a`, `66f601cd`, one_party). There was **no API or UI** to create the row — a DBA-only table. |
| "Not appearing in the UI" | `/calls` shows a player only when `recording_id` is set; `/recordings` lists `recordings_s3` — both empty for new calls since Aug 23 |
| Size / "high-res" | Stored as 16 kHz **stereo PCM16 WAV**: 63 KB/s ≈ 3.8 MB/min (avg 4.2 MB for 67 s). The carrier leg is G.711 at 8 kHz, so the 16 kHz WAV carries no fidelity a compressed codec would lose. `ffmpeg` (libmp3lame, libopus, aac) is installed on the host. |
| Consumers of the file type | `recordings.py` stream/download already use `recordings_s3.mime_type`; the UI plays a blob with the response type |

What SOTA call-recording products do: dual-channel (agent/caller on separate channels) compressed audio — MP3 or Opus — served with HTTP Range and a download name that matches the type. That is now what this does.

## Recordings — changes

| Change | Where |
|---|---|
| **Storage encoding**: WAV → **MP3 64 kbps** by default (env `RECORDING_AUDIO_CODEC=mp3|opus|wav`, `RECORDING_AUDIO_BITRATE`), via `ffmpeg` on the host in a worker thread; any encoder failure keeps the WAV. Stereo preserved. `recordings_s3.mime_type` written; S3 key / local filename carry the real extension | `recording_service.py` (`encode_recording_audio`, `_save_local`, S3 branch, `_insert_recording_record`) |
| Download filename follows the stored MIME type | `recordings.py` (`_download_extension`) |
| **Tenant recording policy API**: `GET /recordings/policy` (any tenant user) and `PUT /recordings/policy` (tenant admins; audited). Validates consent mode, DTMF key, ISO country codes, retention 1–3650 days. Upsert on `tenant_recording_policy` | `recordings.py` |
| **Settings → Recording tab** with the policy form and a plain-language "effect" banner (absent policy = "calls are NOT recorded") | `components/settings/recording-policy-section.tsx`, `app/settings/page.tsx` |
| Backfill tool for the 1.8 GB of existing WAVs (dry-run default, ffprobe duration check, row updated before the WAV is deleted) | `backend/scripts/transcode_recordings.py` — **not run** |
| Data: policy row for tenant `1845a165` (owner's tenant; Dojo campaigns dial the team's own numbers → one_party is the recorded decision, see memory) | applied on prod after deploy; `790ca2db` left for the owner to set in the new UI (its calls are UK inbound — a two-party notice is the lawful default there) |

## Login / signup / session — what the evidence said

| Area | Finding |
|---|---|
| Auth outcomes (7 d) | login 15×200 / 2×401; signup start/verify/complete 1 each, all 200; logout 10; `/auth/me` 625×200 / 82×401; refresh 141×204 / 85×401 |
| Refresh 401s | Zero `reuse_detected`, zero `revoked`/`expired` log lines → the 401s are anonymous visitors with no refresh cookie (the frontend probes refresh after a 401 on `/auth/me`). Not a rotation race: the frontend single-flights refresh. Healthy. |
| **Session fingerprint flapping** | **1,082** `Suspicious session activity … fingerprint_mismatch` warnings for **one** session in a week. The fingerprint hashed `Accept`, `Accept-Encoding`, `Sec-Fetch-Dest`, `Sec-Fetch-Mode`, `DNT`, `Sec-Ch-Ua-Platform-Version` — headers that differ between a navigation, a `fetch()`, an `<audio>` element and an EventSource from the same tab. With `SESSION_STRICT_BINDING` on, this would have logged every user out on their first audio play; off, it buried any real hijack signal in noise and wrote a `suspicious` flag on every session. |
| Cookies | `httponly`, `Secure` in production, `SameSite=strict`; app and API share the eTLD+1 (`talkleeai.com`) so strict is correct. Vercel preview domains cannot carry the cookie (expected). |
| CSRF | Origin check on all unsafe methods; Bearer and internal-token exempt; auth flows exempt |
| Lockout / MFA / passkeys | present and exercised in logs (mfa/verify 200, passkeys 200) |

## Session — changes

| Change | Where |
|---|---|
| Fingerprint = `User-Agent` + `Accept-Language` + `Sec-Ch-Ua` + `Sec-Ch-Ua-Mobile` + `Sec-Ch-Ua-Platform` only; **versioned** (`v2:` prefix) | `device_fingerprint.py` |
| Sessions carrying a v1 fingerprint are **re-bound once** to the v2 value instead of being flagged on every request until they expire | `sessions/lifecycle.py` |

Not changed: the refresh-token family model (correct as is); cookie policy; signup flow (no defects found in code or logs).

## Verification

```text
backend/.venv/Scripts/python -m pytest tests/unit tests/security -q
8880 passed, 8 skipped in 306.48s (0:05:06)

ruff check app/ --select F --extend-ignore F401,F841
All checks passed!

cd Talk-Leee && npm run typecheck && npm run lint && npm test
typecheck exit=0 · lint 0 errors 0 warnings · tests 385, pass 383, fail 0, skipped 2
```

New tests: `test_recording_encoding.py` (13), `test_device_fingerprint_stability.py` (4),
`recording-policy-section.test.ts` (4). Two legacy tests re-stated to the new contracts
(byte-identical offload test pinned to the raw codec; fingerprint format test asserts the
versioned value).

## 2026-09-08 follow-up (owner approved)

- **Backfill applied** (as root — the recordings dir is root-owned, the first run as `admins` hit `PermissionError` and changed nothing): `converted=427`, 0 failures, `recordings_s3` now 429 rows all `audio/mpeg` (223 MB); `/opt/talky/backend/recordings` 1.8 GB → 260 MB. 6 orphan WAVs with no DB row were left untouched.
- **First live MP3 recording** confirmed: 142 s, 1.1 MB, mp3 16 kHz stereo (ffprobe) — 8x smaller than the WAV path.
- **AllState tenant `790ca2db`** policy set: two_party, default notice, opt-out key 9, notice everywhere, 90 days.
- Stuck call `c9c2ca44` has since settled (`completed / no_answer`).
