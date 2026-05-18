# Twilio multi-region trunk failover (Phase 4.1)

This document captures the **operator-side** Twilio configuration that
makes Talky's SIP path survive a regional Twilio outage. The Python /
Asterisk code already routes calls through whichever trunk is healthy;
all the work here is in Twilio's console plus DNS.

## Architecture

```
                    ┌─────────────────────────┐
                    │   talky.example.com     │
                    │   (DNS — 60s TTL)       │
                    └──────────┬──────────────┘
                               │
                ┌──────────────┴──────────────┐
                │                              │
        ┌───────▼─────────┐          ┌────────▼────────┐
        │  trunk-us-east  │          │  trunk-us-west  │
        │  Twilio Edge    │          │  Twilio Edge    │
        │  ashburn        │          │  umatilla       │
        └───────┬─────────┘          └────────┬────────┘
                │                              │
                └─────────┬────────────────────┘
                          │ SIP
                  ┌───────▼────────┐
                  │   Asterisk     │
                  │   pjsip dial   │
                  │   plan         │
                  └────────────────┘
```

Two **Elastic SIP Trunks** are provisioned in two Twilio Edge
Locations (Ashburn + Umatilla, or Sydney + Frankfurt for non-US).
DNS resolves to both; Twilio's edge selection plus our Asterisk
dialplan does the failover.

## Twilio configuration steps

For each region, in the Twilio Console (`Voice → Elastic SIP Trunking`):

1. Create a trunk: `talky-prod-us-east` / `talky-prod-us-west`.
2. Set **Termination URI** to a unique hostname per trunk:
   - `talky-prod-use.pstn.ashburn.twilio.com`
   - `talky-prod-usw.pstn.umatilla.twilio.com`
3. **Origination URI** points to your Asterisk public address with
   region-suffixed credentials so leaked credentials in one region
   don't unlock the other:
   - `sip:talky-use@sip.talky.example.com:5060` (priority 10)
   - `sip:talky-usw@sip.talky.example.com:5060` (priority 20)
4. Reserve **2× peak channel capacity** per trunk so a single-region
   failover absorbs the full load (50-call target → 100 channels per
   trunk; 1000-call target → 2000 channels per trunk).
5. Enable **Voice Insights — Advanced** so Twilio's MOS / packet-loss
   data appears in Grafana via the Twilio API exporter.

## DNS configuration

In your DNS provider:

```
sip.talky.example.com.   60   IN   A   <asterisk-us-east.public.ip>
sip.talky.example.com.   60   IN   A   <asterisk-us-west.public.ip>
```

60s TTL is short enough that a hostname removal during outage takes
effect within a Twilio retry window. SRV records are an alternative
when you have edge-aware Twilio routing — see the Twilio docs.

## Asterisk dialplan (`pjsip.conf`)

Configure the two trunks side-by-side and let pjsip's `failover_dial`
contact the secondary when the primary returns 5xx / times out:

```ini
[trunk-twilio-use]
type = endpoint
transport = transport-udp
context = from-twilio
disallow = all
allow = ulaw
aors = aor-twilio-use
outbound_auth = auth-twilio-use

[aor-twilio-use]
type = aor
contact = sip:talky-prod-use.pstn.ashburn.twilio.com

[trunk-twilio-usw]
type = endpoint
transport = transport-udp
context = from-twilio
disallow = all
allow = ulaw
aors = aor-twilio-usw
outbound_auth = auth-twilio-usw

[aor-twilio-usw]
type = aor
contact = sip:talky-prod-usw.pstn.umatilla.twilio.com
```

In `extensions.conf`:

```ini
[outbound]
exten => _X.,1,Dial(PJSIP/${EXTEN}@trunk-twilio-use,30,m)
 same => n,GotoIf($[${DIALSTATUS} = ANSWER]?hangup)
 same => n,Dial(PJSIP/${EXTEN}@trunk-twilio-usw,30,m)
 same => n(hangup),Hangup()
```

The two-step Dial gives the primary 30 seconds; on
`CONGESTION` / `CHANUNAVAIL` / `BUSY` it falls through to the secondary
without the caller hearing dead air.

## Talky env vars (helm `values.yaml`)

The application code doesn't need to know which trunk is in use — it
calls Asterisk by trunk name. But the env block in
`infra/helm/talky/values.yaml` SHOULD record the names so dashboards /
ops scripts can correlate:

```yaml
telephony:
  trunks:
    primary: trunk-twilio-use
    fallback: trunk-twilio-usw
```

These don't drive runtime behaviour (Asterisk does), but they're
surfaced in `/api/v1/sip/telephony/status` for visibility.

## Verification

A quarterly drill — not a production-time test:

1. Pick the in-use trunk (use Twilio Voice Insights to confirm).
2. In Twilio console, **temporarily disable** the trunk's Termination URI.
3. Originate a test call through Talky. Expected: Asterisk's Dial
   times out on the primary at 30s, immediately re-dials on the secondary,
   call connects successfully on Twilio's other Edge.
4. Re-enable the primary trunk. Confirm the next test call rebalances.
5. Capture Twilio Voice Insights MOS for the failover call —
   should be ≥ 4.0 (carrier-grade).

## Cost note

Two trunks at 2× peak capacity = 4× the channel reservation. Twilio's
unit pricing is per-minute, not per-channel, so the bill is unchanged
unless the secondary is actively used. Reserve charges only apply if
you have **purchased channel reservations**; pay-as-you-go trunks
incur no idle cost.
