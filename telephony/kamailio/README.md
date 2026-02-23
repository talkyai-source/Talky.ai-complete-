# Kamailio Layer

This folder holds SBC/SIP edge routing configs.

## Purpose

- SIP ingress/egress routing
- Tenant-aware trunk policy
- ACL/rate-limit/fraud controls
- SIP normalization before B2BUA handoff

## Structure

- `conf/` - Kamailio configs and include files

## Notes

- Keep tenant routes data-driven where possible.
- Avoid hardcoding customer trunk details in static config.
- Custom C modules/patches (if needed) live in `../modules/kamailio/`.
