# Asterisk Layer (Primary)

This folder contains the primary B2BUA/media-app runtime for Talky telephony.

## Status

- Primary runtime: **Asterisk (chan_pjsip / res_pjsip)**
- SIP edge proxy in front: **OpenSIPS** (`../opensips`)
- Backup runtime: **FreeSWITCH** (`../freeswitch`)

## Official-aligned baseline

1. Use `chan_pjsip`/`res_pjsip` for new deployments.
2. Keep explicit `identify` matching for proxy-originated traffic.
3. Keep media anchored through B2BUA (`direct_media = no`) unless topology explicitly supports direct media.
4. Keep NAT options scoped to real NAT scenarios only.

## Structure

- `conf/` - Asterisk config files mounted into `/etc/asterisk`
- `Dockerfile` - container image baseline for local/staging
- Custom C/C++ modules belong in `../modules/asterisk/`
