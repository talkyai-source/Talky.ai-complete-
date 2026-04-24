# Talky.ai — Production Fix Deployment Runbook
**Date:** April 2026  
**Applies to:** Single Linux server deployment  
**Estimated time:** 2–3 hours for full rollout

---

## What this runbook fixes

| Fix | File changed | Risk |
|-----|-------------|------|
| CI/CD test gating | `.github/workflows/ci.yml` | Low — CI only |
| Dependency scanning | `.github/workflows/ci.yml` | Low — CI only |
| SHA-based deploys + rollback | `.github/workflows/deploy.yml` | Low — CI only |
| Hardcoded DB password removed | `docker-compose.yml` | **Medium — restarts DB** |
| Redis password added | `docker-compose.yml` | **Medium — restarts Redis** |
| Source code volume removed | `docker-compose.yml` | Low — restarts backend |
| Ports restricted to localhost | `docker-compose.yml` | Low |
| pgAdmin moved to SSH-tunnel only | `docker-compose.yml` | Low |
| RTPEngine CVE-2025-53399 patched | `telephony/rtpengine/conf/rtpengine.conf` | **High — restarts telephony** |
| OpenSIPS TLS cert validation | `telephony/opensips/conf/tls.cfg` | **High — restarts SIP** |
| Kamailio TLS cert validation | `telephony/kamailio/conf/tls.cfg` | **High — restarts SIP** |
| Alertmanager Slack/email wired | `telephony/observability/alertmanager/alertmanager.yml` | Low |
| Firewall rules | `scripts/firewall-setup.sh` | **High — run carefully** |
| .gitignore comprehensive | `.gitignore` | Low — git only |

---

## Step 0 — Copy fixed files to your repo

On your development machine, copy the files from this `fixes/` directory into your repo:

```bash
# From repo root
cp fixes/.github/workflows/ci.yml         .github/workflows/ci.yml
cp fixes/.github/workflows/deploy.yml      .github/workflows/deploy.yml
cp fixes/docker-compose.yml                docker-compose.yml
cp fixes/.env.docker                       .env.docker
cp fixes/.gitignore                        .gitignore
cp fixes/telephony/rtpengine/conf/rtpengine.conf  telephony/rtpengine/conf/rtpengine.conf
cp fixes/telephony/opensips/conf/tls.cfg           telephony/opensips/conf/tls.cfg
cp fixes/telephony/kamailio/conf/tls.cfg           telephony/kamailio/conf/tls.cfg
cp fixes/telephony/observability/alertmanager/alertmanager.yml \
       telephony/observability/alertmanager/alertmanager.yml
cp fixes/scripts/firewall-setup.sh         scripts/firewall-setup.sh
cp fixes/scripts/generate-secrets.sh       scripts/generate-secrets.sh
cp fixes/scripts/gen-sip-certs.sh          scripts/gen-sip-certs.sh
chmod +x scripts/*.sh
```

---

## Step 1 — GitHub Secrets (do this first)

Add these secrets to your GitHub repo at:  
**Settings → Secrets and variables → Actions → New repository secret**

| Secret name | Value |
|------------|-------|
| `DEPLOY_HOST` | Your server IP or hostname |
| `DEPLOY_USER` | SSH user (e.g. `deploy` or `ubuntu`) |
| `DEPLOY_SSH_KEY` | Private SSH key for deploy user |
| `DEPLOY_PATH` | `/opt/talky` (or your deploy path) |
| `SLACK_WEBHOOK_URL` | Your Slack incoming webhook URL |

---

## Step 2 — Generate secrets on the server

SSH into your server and run:

```bash
ssh user@your-server
cd /opt/talky

# Pull the latest code (with the new .env.docker template)
git pull origin main

# Generate strong secrets
bash scripts/generate-secrets.sh

# Now fill in your API keys manually
nano .env
# Fill in: DEEPGRAM_API_KEY, GROQ_API_KEY, CARTESIA_API_KEY,
#          VONAGE_API_KEY, VONAGE_API_SECRET, CORS_ORIGINS

# Verify no empty required values
docker compose config | grep -E "^  (JWT_SECRET|POSTGRES_PASSWORD|REDIS_PASSWORD|DEEPGRAM|GROQ|CARTESIA):"
```

---

## Step 3 — Generate SIP TLS certificates

```bash
# On your server
cd /opt/talky
bash scripts/gen-sip-certs.sh sip.your-domain.com

# Verify certs were created
ls -la telephony/certs/opensips/
ls -la telephony/certs/kamailio/
```

Then add these volumes to docker-compose.yml for your OpenSIPS/Kamailio services (if you run them as containers):

```yaml
volumes:
  - ./telephony/certs/opensips:/etc/opensips/certs:ro
  - ./telephony/certs/kamailio:/etc/kamailio/certs:ro
```

---

## Step 4 — Plan a maintenance window

The next steps restart services. Schedule this during off-hours.

**Estimate:** ~15 minutes of service interruption for the telephony stack.

---

## Step 5 — Apply the database/redis changes

> ⚠️ This will restart the database container. Active connections will drop.
> Verify no active calls are in progress before running.

```bash
cd /opt/talky

# Stop backend first (gracefully drain connections)
docker compose stop backend

# Recreate DB and Redis with new password settings
# NOTE: If the DB already has data, DO NOT use 'down -v' — that wipes the volume.
# Instead, change the password inside Postgres first:
docker compose exec db psql -U $POSTGRES_USER -c "ALTER USER $POSTGRES_USER PASSWORD 'your_new_password';"

# Then update .env with the new password and restart
docker compose up -d db redis backend

# Verify all services healthy
docker compose ps
docker compose logs backend --tail=20
```

---

## Step 6 — Apply the firewall rules

> ⚠️ Read the script and add your carrier IPs before running.

```bash
# Edit firewall-setup.sh and add your SIP carrier IP ranges
nano scripts/firewall-setup.sh

# Apply rules
sudo bash scripts/firewall-setup.sh

# Verify you can still SSH
ssh -o ConnectTimeout=5 user@your-server echo "SSH OK"
```

---

## Step 7 — Apply telephony changes (RTPEngine + TLS)

> ⚠️ This restarts the SIP stack. All active calls will drop.
> Schedule during off-hours / low-traffic window.

```bash
# Restart OpenSIPS with new TLS config
docker compose restart opensips || systemctl restart opensips

# Restart Kamailio
docker compose restart kamailio || systemctl restart kamailio

# Restart RTPEngine (picks up new rtpengine.conf)
docker compose restart rtpengine || systemctl restart rtpengine

# Test a SIP call to verify TLS is working
# You should see TLS in the SIP trace: sip:5080;transport=TLS
```

---

## Step 8 — Configure Alertmanager notifications

Edit `telephony/observability/alertmanager/alertmanager.yml` and fill in:

```bash
# Set your Slack webhook URL
export ALERTMANAGER_SLACK_WEBHOOK_URL="https://hooks.slack.com/services/YOUR/WEBHOOK/URL"

# Set your SMTP credentials for email alerts
export ALERTMANAGER_SMTP_HOST="smtp.gmail.com"
export ALERTMANAGER_SMTP_USER="alerts@your-domain.com"
export ALERTMANAGER_SMTP_PASSWORD="your-app-password"

# Restart alertmanager
docker compose restart alertmanager

# Test that alerts fire
curl -s -X POST http://localhost:9093/api/v1/alerts \
  -H 'Content-Type: application/json' \
  -d '[{"labels":{"alertname":"TestAlert","severity":"warning","team":"telephony"},"annotations":{"summary":"Test alert from runbook"}}]'
```

---

## Step 9 — Commit and push to trigger fixed CI

```bash
# On your dev machine
git add .github/workflows/ docker-compose.yml .env.docker .gitignore \
        telephony/rtpengine/conf/ telephony/opensips/conf/tls.cfg \
        telephony/kamailio/conf/tls.cfg \
        telephony/observability/alertmanager/ scripts/

git commit -m "fix: resolve all P0 security and DevOps issues

- Remove || true from pytest and ESLint — CI now gates on failures
- Add gitleaks secret scanning to CI
- Add pip-audit, npm audit, bandit, Trivy image scanning
- Add secret placeholder guard step
- Remove hardcoded DB/JWT defaults from docker-compose
- Add Redis AUTH password
- Restrict all internal ports to localhost
- Move pgAdmin to admin-profile + SSH tunnel only
- Remove source code volume mount from backend
- Add SHA-based image tags + rollback workflow
- Fix RTPEngine CVE-2025-53399: strict-source, force-srtp, heuristic learning
- Enable TLS cert validation in OpenSIPS and Kamailio
- Wire Alertmanager Slack + email receivers
- Comprehensive .gitignore
- Add firewall-setup.sh, generate-secrets.sh, gen-sip-certs.sh"

git push origin main
```

Watch the CI run — it will now actually fail if there are test/lint errors.

---

## Verification checklist

After all steps, confirm:

- [ ] GitHub Actions CI passes without `|| true` bypasses
- [ ] `docker compose ps` shows all services healthy
- [ ] `curl http://localhost:8000/health` returns 200
- [ ] SIP test call completes successfully
- [ ] `ss -tlnp | grep -E "5432|6379"` shows no external port bindings
- [ ] `ufw status` shows SIP/RTP restricted to carrier IPs
- [ ] Test Alertmanager fires a Slack message
- [ ] pgAdmin accessible via SSH tunnel (`ssh -L 5050:localhost:5050 ...`)
