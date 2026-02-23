# FreeSWITCH Layer

This folder holds FreeSWITCH-specific configuration and optional custom modules.

## Purpose

- B2BUA call control
- Media app execution (playback, bridge, transfer)
- Event socket integration with Python backend
- Audio fork/bridge to AI pipeline

## Structure

- `conf/autoload_configs/` - module configs
- `conf/dialplan/public/` - dialplan entries
- `conf/sip_profiles/` - SIP profile configs
- `conf/vars/` - variable templates

## Notes

- Keep production secrets out of committed files.
- Commit templates and examples only.
- Any custom C/C++ module code should go in `../modules/freeswitch/`.
