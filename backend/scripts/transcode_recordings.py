"""Re-encode existing local WAV recordings to the configured storage codec.

Dry-run by default; ``--apply`` performs the conversion. For each
``recordings_s3`` row with s3_bucket='local' whose file is a .wav:

  1. ffmpeg WAV -> codec (MP3 64 kbps by default, see RECORDING_AUDIO_CODEC)
  2. ffprobe both files; refuse if the durations differ by more than 1 s
  3. UPDATE recordings_s3 SET s3_key, mime_type, file_size_bytes  (tenant-scoped)
  4. only then delete the WAV

Run on the production host from backend/ with the app venv and .env loaded:

    set -a; . ./.env; set +a
    venv/bin/python scripts/transcode_recordings.py            # report only
    venv/bin/python scripts/transcode_recordings.py --apply    # convert

Never touches S3 rows, rows whose file is missing, or files already encoded.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db_utils import acquire_with_tenant  # noqa: E402
from app.domain.services.recording_service import (  # noqa: E402
    RECORDING_AUDIO_CODEC,
    encode_recording_audio,
)


def _probe_duration(path: str) -> float | None:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", path],
            capture_output=True, text=True, timeout=60, check=False,
        )
        return float(json.loads(out.stdout)["format"]["duration"])
    except Exception:  # noqa: BLE001
        return None


async def main(apply: bool, limit: int) -> int:
    import asyncpg

    if RECORDING_AUDIO_CODEC == "wav":
        print("RECORDING_AUDIO_CODEC=wav — nothing to do")
        return 0
    dsn = os.environ["DATABASE_URL"]
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
    try:
        async with acquire_with_tenant(pool, None) as conn:
            await conn.execute("SET LOCAL app.bypass_rls = 'true'")
            rows = await conn.fetch(
                """
                SELECT id, tenant_id, s3_key, file_size_bytes, duration_seconds
                  FROM recordings_s3
                 WHERE s3_bucket = 'local' AND status = 'uploaded'
                   AND (mime_type IS NULL OR mime_type = 'audio/wav')
                   AND s3_key LIKE '%.wav'
                 ORDER BY created_at
                 LIMIT $1
                """,
                limit,
            )
        print(f"candidates: {len(rows)}  codec={RECORDING_AUDIO_CODEC}  apply={apply}")
        saved_bytes = 0
        converted = 0
        for row in rows:
            src = str(row["s3_key"])
            if not os.path.exists(src):
                print(f"skip {row['id']}: file missing {src}")
                continue
            wav = Path(src).read_bytes()
            data, ext, mime = encode_recording_audio(wav)
            if ext == ".wav":
                print(f"skip {row['id']}: encoder unavailable")
                continue
            dst = str(Path(src).with_suffix(ext))
            print(f"{row['id']}: {len(wav):>10,} -> {len(data):>9,} bytes ({len(wav)/max(1,len(data)):.1f}x) {os.path.basename(dst)}")
            saved_bytes += len(wav) - len(data)
            if not apply:
                continue
            Path(dst).write_bytes(data)
            src_dur, dst_dur = _probe_duration(src), _probe_duration(dst)
            if src_dur is None or dst_dur is None or abs(src_dur - dst_dur) > 1.0:
                print(f"  !! duration check failed ({src_dur} vs {dst_dur}) — keeping WAV, removing new file")
                Path(dst).unlink(missing_ok=True)
                continue
            async with acquire_with_tenant(pool, str(row["tenant_id"])) as conn:
                res = await conn.execute(
                    """
                    UPDATE recordings_s3 SET s3_key = $2, mime_type = $3, file_size_bytes = $4, updated_at = NOW()
                     WHERE id = $1 AND tenant_id = $5::uuid
                    """,
                    row["id"], dst, mime, len(data), str(row["tenant_id"]),
                )
            if res != "UPDATE 1":
                print(f"  !! row update failed ({res}) — keeping WAV, removing new file")
                Path(dst).unlink(missing_ok=True)
                continue
            Path(src).unlink(missing_ok=True)
            converted += 1
        print(f"done: converted={converted} would_save={saved_bytes/1024/1024:.1f} MB apply={apply}")
        return 0
    finally:
        await pool.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=10000)
    args = ap.parse_args()
    sys.exit(asyncio.run(main(args.apply, args.limit)))
