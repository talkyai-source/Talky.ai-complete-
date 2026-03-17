# FreeSWITCH Layer (Backup)

This folder holds FreeSWITCH-specific configuration and optional custom modules.
It is retained as a backup runtime path.

## Purpose

- Backup B2BUA call control path
- Legacy media app execution (playback, bridge, transfer)
- Backup event socket integration path
- Fallback audio fork/bridge path

## Structure

- `conf/autoload_configs/` - module configs
- `conf/dialplan/public/` - dialplan entries
- `conf/sip_profiles/` - SIP profile configs
- `conf/vars/` - variable templates

## Notes

- Keep production secrets out of committed files.
- Commit templates and examples only.
- Any custom C/C++ module code should go in `../modules/freeswitch/`.
- Active primary runtime is Asterisk under `../asterisk/`.
- Docker compose starts FreeSWITCH only when backup profile is requested:
  - `docker compose --profile backup ... up -d freeswitch`
