# Talky.ai — P2 Upgrades Deployment Guide
## Alembic + OpenTelemetry + S3 Recordings

---

## Step 1 — Copy new files into your repo

```bash
# From your repo root
cp upgrades/backend/requirements.txt              backend/requirements.txt
cp upgrades/backend/alembic.ini                   backend/alembic.ini
cp upgrades/backend/alembic/env.py                backend/alembic/env.py
cp upgrades/backend/alembic/script.py.mako        backend/alembic/script.py.mako
cp -r upgrades/backend/alembic/versions/          backend/alembic/versions/
cp upgrades/backend/app/main.py                   backend/app/main.py
cp upgrades/backend/app/core/telemetry.py         backend/app/core/telemetry.py
cp upgrades/backend/app/core/kms.py               backend/app/core/kms.py
cp upgrades/backend/app/domain/services/recording_service.py \
                                                   backend/app/domain/services/recording_service.py
cp upgrades/backend/app/api/v1/endpoints/recordings.py \
                                                   backend/app/api/v1/endpoints/recordings.py
```

---

## Step 2 — Install new dependencies

```bash
cd backend
pip install -r requirements.txt
```

---

## Step 3 — Alembic setup

### 3a. Stamp existing database (if already bootstrapped from complete_schema.sql)

```bash
cd backend
export DATABASE_URL="postgresql://talkyai:yourpassword@localhost:5432/talkyai"

# Tell Alembic the DB is already at the baseline — don't re-run SQL
alembic stamp 0001_baseline

# Verify
alembic current
# Should print: 0001_baseline (head)
```

### 3b. Fresh database (never had complete_schema.sql applied)

```bash
# Apply the full schema first
psql $DATABASE_URL -f database/complete_schema.sql

# Then stamp baseline
alembic stamp 0001_baseline
```

### 3c. How to create future migrations

```bash
# Create a new migration (manual)
alembic revision -m "add_call_tags_column"

# Edit the generated file in alembic/versions/
# Then apply:
alembic upgrade head

# Rollback one step if needed:
alembic downgrade -1
```

### 3d. Update deploy.yml to run migrations

In `.github/workflows/deploy.yml`, replace the broken init.sql re-run with:

```yaml
- name: Run database migrations
  run: |
    docker run --rm \
      --env-file .env \
      "${DEPLOY_IMAGE}" \
      alembic upgrade head
```

---

## Step 4 — S3 bucket setup

### 4a. Create the bucket

**AWS S3:**
```bash
aws s3 mb s3://talky-recordings --region us-east-1

# Enable versioning (recommended)
aws s3api put-bucket-versioning \
  --bucket talky-recordings \
  --versioning-configuration Status=Enabled

# Block all public access
aws s3api put-public-access-block \
  --bucket talky-recordings \
  --public-access-block-configuration \
    BlockPublicAcls=true,IgnorePublicAcls=true,\
    BlockPublicPolicy=true,RestrictPublicBuckets=true
```

**Cloudflare R2 (cheaper, no egress fees — recommended for audio):**
```bash
# Via Cloudflare dashboard: R2 → Create bucket → talky-recordings
# Or via wrangler CLI:
wrangler r2 bucket create talky-recordings
```

### 4b. Set lifecycle policy for retention (AWS S3 only)

```bash
cat > lifecycle.json << 'EOF'
{
  "Rules": [
    {
      "ID": "basic-plan-30-days",
      "Filter": {"Prefix": ""},
      "Status": "Enabled",
      "Expiration": {"Days": 365}
    }
  ]
}
EOF

aws s3api put-bucket-lifecycle-configuration \
  --bucket talky-recordings \
  --lifecycle-configuration file://lifecycle.json
```

### 4c. Create IAM user with minimum permissions (AWS)

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "s3:PutObject",
        "s3:GetObject",
        "s3:DeleteObject",
        "s3:HeadObject"
      ],
      "Resource": "arn:aws:s3:::talky-recordings/*"
    }
  ]
}
```

### 4d. Add S3 config to .env

```bash
# Add to /opt/talky/.env
S3_BUCKET_NAME=talky-recordings
S3_REGION=us-east-1
S3_ACCESS_KEY_ID=AKIAxxxx
S3_SECRET_ACCESS_KEY=xxxx
# For Cloudflare R2:
# S3_ENDPOINT_URL=https://<account_id>.r2.cloudflarestorage.com
S3_PRESIGNED_URL_EXPIRY=3600
S3_STORAGE_CLASS=STANDARD
```

---

## Step 5 — OpenTelemetry setup

### 5a. Choose a tracing backend

**Option A — Grafana Cloud (free tier, easiest):**
1. Sign up at grafana.com → Cloud → Traces → Get connection details
2. You'll get an OTLP endpoint like: `https://tempo-prod-10-prod-eu-west-2.grafana.net:443`

**Option B — Self-hosted Grafana Tempo (on same server):**
```bash
# Add to docker-compose.yml:
  tempo:
    image: grafana/tempo:latest
    container_name: talky-tempo
    ports:
      - "127.0.0.1:4317:4317"   # OTLP gRPC
      - "127.0.0.1:3200:3200"   # Tempo HTTP API
    networks:
      - talky-network
    volumes:
      - tempo-data:/var/tempo
    command: ["-config.file=/etc/tempo.yaml"]
```

**Option C — Console only (development):**
Leave `OTEL_EXPORTER_ENDPOINT` empty — spans print to stdout.

### 5b. Add OTel config to .env

```bash
# Add to .env
OTEL_ENABLED=true
OTEL_SERVICE_NAME=talky-backend
OTEL_ENVIRONMENT=production
# Set to your Tempo/Jaeger endpoint, or leave empty for console:
OTEL_EXPORTER_ENDPOINT=http://localhost:4317
```

### 5c. Use pipeline spans in voice code

The `telemetry.py` module provides `pipeline_span` for the STT→LLM→TTS chain.
Add it to your voice pipeline service:

```python
from app.core.telemetry import pipeline_span, record_latency
import time

# In voice_pipeline_service.py or conversation_engine.py:

async def transcribe(self, audio: bytes, call_id: str) -> str:
    t0 = time.monotonic()
    with pipeline_span("stt", call_id, provider="deepgram") as span:
        result = await self._stt.transcribe(audio)
        record_latency(span, "stt", (time.monotonic() - t0) * 1000)
        span.set_attribute("stt.words", len(result.split()))
    return result

async def generate_response(self, text: str, call_id: str) -> str:
    t0 = time.monotonic()
    with pipeline_span("llm", call_id, provider="groq") as span:
        response = await self._llm.generate(text)
        record_latency(span, "llm", (time.monotonic() - t0) * 1000)
    return response

async def synthesize(self, text: str, call_id: str) -> bytes:
    t0 = time.monotonic()
    with pipeline_span("tts", call_id, provider="cartesia") as span:
        audio = await self._tts.synthesize(text)
        record_latency(span, "tts", (time.monotonic() - t0) * 1000)
    return audio
```

---

## Step 6 — KMS setup

### Option A — Local KMS (single-server, simplest)

```bash
# Generate a strong 32-byte master key
python3 -c "import secrets; print(secrets.token_hex(32))"
# Output: e.g. a3f2c1d9e8b7a6f5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a2b1

# Add to .env
KMS_PROVIDER=local
SECRETS_MASTER_KEY=a3f2c1d9e8b7a6f5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a8b7c6d5e4f3a2b1
```

### Option B — AWS KMS (production recommended)

```bash
# Create a KMS key in AWS Console or CLI
aws kms create-key --description "Talky.ai secrets KEK" --region us-east-1
# Note the KeyId from the output

# Add to .env
KMS_PROVIDER=aws
KMS_KEY_ID=arn:aws:kms:us-east-1:123456789012:key/abc-def-ghi
KMS_REGION=us-east-1
```

### Apply the secrets_manager.py patch

```bash
cd backend

# Apply the 4 call-site changes automatically
sed -i 's/encrypted_dek = self\._encrypt_dek(dek)/encrypted_dek = await self._wrap_dek(dek)/g' \
  app/domain/services/secrets_manager.py

sed -i 's/dek = self\._decrypt_dek(encrypted_dek)/dek = await self._unwrap_dek(encrypted_dek)/g' \
  app/domain/services/secrets_manager.py

sed -i 's/dek = self\._decrypt_dek(row\["encrypted_dek"\])/dek = await self._unwrap_dek(row["encrypted_dek"])/g' \
  app/domain/services/secrets_manager.py

# Then manually apply the __init__ and method changes from secrets_manager_kms_patch.py
# Add the import at the top:
# from app.core.kms import get_kms_backend, KMSBackend
```

---

## Step 7 — Restart and verify

```bash
cd /opt/talky

# Rebuild with new deps
docker compose build backend

# Run migrations
docker compose run --rm backend alembic upgrade head

# Start everything
docker compose up -d

# Check logs
docker compose logs backend --tail=30

# Verify OTel traces appear
docker compose logs backend | grep -i "otel\|telemetry\|tracing"

# Test recording upload (make a test call and check recordings_s3 table)
docker compose exec db psql -U talkyai -c "SELECT id, status, s3_key FROM recordings_s3 LIMIT 5;"

# Check S3 bucket has recordings
aws s3 ls s3://talky-recordings/ --recursive | head -10
```

---

## Expected score improvements after these upgrades

| Category             | Before P2 | After P2 |
|----------------------|-----------|----------|
| CI/CD pipeline       | 82        | 82       |
| Security posture     | 73        | 73       |
| Code architecture    | 78        | 82       |
| Observability        | 55        | 80       |
| Docker/container     | 78        | 80       |
| Test coverage        | 72        | 72       |
| Secrets management   | 65        | 85       |
| Documentation        | 85        | 87       |
| **Overall average**  | **74**    | **80**   |
