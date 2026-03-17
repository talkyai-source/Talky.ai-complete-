# WS-M Asterisk Primary Baseline (Official Guidance Aligned)

Date: February 25, 2026
Status: Implemented (baseline)

## Scope

Switch media/B2BUA primary runtime to Asterisk while retaining FreeSWITCH as backup.

## Implemented Changes

1. Added Asterisk runtime under `telephony/asterisk/`.
2. Updated compose to run `asterisk` as primary and keep `freeswitch` under backup profile.
3. Routed OpenSIPS dispatcher set 1/2 to Asterisk SIP listener (`127.0.0.1:5088`).
4. Added verification updates in WS-A/WS-B scripts for Asterisk baseline checks.

## Protocol and Security Baseline (Do/Don't)

### Do

1. Do use `chan_pjsip`/`res_pjsip` for new deployments.
2. Do define explicit endpoint identification for proxy-originated traffic (`type=identify`, `match=...`).
3. Do keep explicit codec allowlists (`disallow=all` then `allow=...`).
4. Do use loose-routing aware proxy route sets when proxying outbound SIP (`outbound_proxy ...;lr`).
5. Do keep media anchoring in B2BUA (`direct_media=no`) unless direct-media topology is validated.
6. Do plan TLS signaling and SRTP media for production secure calling.

### Don't

1. Don't use `chan_sip` for new installs.
2. Don't apply NAT parameters blindly when Asterisk and proxy are on the same network path.
3. Don't keep open codec policies.
4. Don't expose backup control paths as active defaults.

## Evidence Paths

1. `telephony/asterisk/conf/modules.conf`
2. `telephony/asterisk/conf/pjsip.conf`
3. `telephony/deploy/docker/docker-compose.telephony.yml`
4. `telephony/opensips/conf/dispatcher.list`
5. `telephony/scripts/verify_ws_a.sh`
6. `telephony/scripts/verify_ws_b.sh`
7. `telephony/tests/test_telephony_stack.py`

## Official References

1. Asterisk docs: PJSIP configuration sections and relationships
   - https://docs.asterisk.org/Configuration/Channel-Drivers/SIP/Configuring-res_pjsip/PJSIP-Configuration-Sections-and-Relationships/
2. Asterisk docs: Configuring res_pjsip to work through a SIP proxy
   - https://docs.asterisk.org/Configuration/Channel-Drivers/SIP/Configuring-res_pjsip/Configuring-res_pjsip-to-work-through-a-SIP-proxy/
3. Asterisk docs: PJSIP with Proxies (NAT-related do/don't around proxy scenarios)
   - https://docs.asterisk.org/Configuration/Channel-Drivers/SIP/Configuring-res_pjsip/PJSIP-with-Proxies/
4. Asterisk docs: chan_sip deprecation/removal context
   - https://docs.asterisk.org/Configuration/Channel-Drivers/SIP/Configuring-chan_sip/
5. Asterisk docs: secure calling tutorial (TLS + SRTP baseline)
   - https://docs.asterisk.org/Deployment/Important-Security-Considerations/Asterisk-Security-Framework/Asterisk-and-Secure-Calling/
