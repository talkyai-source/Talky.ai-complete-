# WS-M Media Quality Report

Date: February 26, 2026  
Workstream: WS-M (Media and Transfer Reliability)  
Status: Passed

---

## Scope

This report validates WS-M media-path requirements:
1. OpenSIPS media relay hooks are active and syntax-valid.
2. RTPengine kernel-mode baseline config is present and bounded.
3. RTPengine userspace-mode config is present and boot-valid.

---

## Validation Method

Primary command:

```bash
bash telephony/scripts/verify_ws_m.sh telephony/deploy/docker/.env.telephony.example
```

Media-specific checks executed by verifier:
1. OpenSIPS markers:
   - `loadmodule "rtpengine.so"`
   - `modparam("rtpengine", "rtpengine_sock", "udp:127.0.0.1:2223")`
   - `rtpengine_offer`, `rtpengine_answer`, `rtpengine_delete`
2. Kernel mode config:
   - `telephony/rtpengine/conf/rtpengine.conf` -> `table = 0`
3. Userspace mode config:
   - `telephony/rtpengine/conf/rtpengine.userspace.conf` -> `table = -1`
4. Userspace boot check:
   - Runs `rtpengine --foreground` with userspace config in an isolated container window.

---

## Results

1. OpenSIPS media hook markers: PASS
2. Kernel-mode RTPengine config bounds: PASS
3. Userspace-mode RTPengine config bounds: PASS
4. Userspace-mode boot validation: PASS

Evidence file:
1. `telephony/docs/phase_3/evidence/ws_m_media_mode_check.txt`

---

## Operational Notes

1. Kernel mode remains active default (`table = 0`) for primary runtime.
2. Userspace mode (`table = -1`) is validated as fallback-safe startup path.
3. RTP port bounds remain fixed at `30000-34999` for predictable firewalling.

---

## Exit Statement

WS-M media quality validation is complete and passes gate requirements for kernel/userspace mode coverage.
