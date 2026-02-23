# RTPengine Layer

This folder holds media relay configuration.

## Purpose

- RTP anchoring and NAT traversal
- Packet relay for stable media paths
- Codec/media stream continuity under load

## Structure

- `conf/` - rtpengine configs and startup options

## Notes

- Keep explicit interface and port range documentation.
- Validate relay behavior in canary before full cutover.
- Custom patches (if needed) live in `../modules/rtpengine/`.
