---
name: pbx-voip-telephony
description: Expert in PBX telephony platforms (FreeSWITCH, Asterisk, Kamailio, OpenSIPS), VoIP protocols (SIP, RTP, SRTP, WebRTC), media handling, dial plans, and real-time voice infrastructure. Use when working with SIP trunks, IVR, call routing, media gateways, RTP bridging, NAT traversal, TLS/SRTP security, codec negotiation, or any FreeSWITCH/Asterisk/Kamailio/OpenSIPS configuration, scripting, or debugging.
---

# PBX & VoIP Telephony Expert

## Platforms at a Glance

| Platform | Primary Role | Config Language |
|---|---|---|
| **FreeSWITCH** | Soft-switch / media server | XML, Lua, ESL |
| **Asterisk** | PBX / IVR / ARI | dialplan (extensions.conf), AEL, ARI REST |
| **Kamailio** | SIP proxy / registrar / load-balancer | kamailio.cfg (native DSL) |
| **OpenSIPS** | SIP proxy / B2BUA / load-balancer | opensips.cfg (native DSL) |

---

## FreeSWITCH

### Key concepts
- **ESL** (Event Socket Library) — control FS remotely via `inbound`/`outbound` socket.
- **Sofia SIP** module handles SIP transport. Config lives in `conf/sip_profiles/`.
- **XML dialplan** routes calls: `conf/dialplan/default.xml`.
- **Lua / JavaScript** scripts run inline via `<action application="lua" data="script.lua"/>`.
- **mod_rtpproxy / mod_avmd** for RTP handling, voice activity detection.
- **WebRTC** support via `mod_verto` (JSON-RPC over WebSocket).

### Dialplan snippet — route to extension
```xml
<extension name="local_extensions">
  <condition field="destination_number" expression="^(1\d{3})$">
    <action application="set" data="call_timeout=30"/>
    <action application="bridge" data="sofia/internal/$1@${domain_name}"/>
  </condition>
</extension>
```

### ESL (Python) — originate a call
```python
from ESL import ESLconnection
con = ESLconnection("127.0.0.1", "8021", "ClueCon")
con.api("originate", "sofia/gateway/mytrunk/+15551234567 &echo()")
```

### Common FS CLI commands
```
fs_cli -x "sofia status"
fs_cli -x "sofia profile internal restart"
fs_cli -x "show channels"
fs_cli -x "originate sofia/internal/1001 &bridge(sofia/internal/1002)"
```

---

## Asterisk

### Key concepts
- **extensions.conf** — priority-based dialplan (`exten => pattern,priority,application(args)`).
- **ARI** (Asterisk REST Interface) — modern REST+WebSocket control plane (replaces AGI/AMI for new work).
- **PJSIP** is the preferred SIP channel driver (replaces `chan_sip`).
- **Stasis** application bridges ARI to the dialplan.

### PJSIP endpoint (pjsip.conf)
```ini
[mytrunk]
type=endpoint
transport=transport-udp
context=from-trunk
disallow=all
allow=ulaw,alaw,opus
outbound_auth=mytrunk_auth
aors=mytrunk_aor

[mytrunk_auth]
type=auth
auth_type=userpass
username=user
password=secret

[mytrunk_aor]
type=aor
contact=sip:sip.provider.com
```

### Dialplan example
```ini
[from-internal]
exten => _1NXXNXXXXXX,1,NoOp(Outbound PSTN)
 same => n,Dial(PJSIP/${EXTEN}@mytrunk,30,tT)
 same => n,Hangup()
```

### ARI — answer and play audio (Python)
```python
import ari
client = ari.connect("http://localhost:8088", "asterisk", "secret")
def on_start(channel, ev):
    channel.answer()
    channel.play(media="sound:hello-world")
client.on_channel_event("StasisStart", on_start)
client.run(apps="my_app")
```

---

## Kamailio

### Key concepts
- **Routing blocks**: `request_route`, `reply_route`, `failure_route`, `branch_route`.
- **Modules**: `registrar`, `usrloc`, `dispatcher`, `htable`, `presence`, `tls`, `websocket`.
- **Pseudo-variables** prefixed with `$` (e.g., `$rU`, `$si`, `$hdr(Contact)`).
- **Database** via `db_mysql` / `db_postgres` modules for subscriber/location storage.

### Minimal SIP proxy config skeleton
```cfg
#!KAMAILIO
loadmodule "sl.so"
loadmodule "tm.so"
loadmodule "rr.so"
loadmodule "registrar.so"
loadmodule "usrloc.so"

modparam("usrloc", "db_mode", 0)

request_route {
    if (is_method("REGISTER")) {
        save("location");
        exit;
    }
    if (!lookup("location")) {
        sl_send_reply("404","Not Found");
        exit;
    }
    t_relay();
}
```

### Dispatcher (load balancing)
```cfg
loadmodule "dispatcher.so"
modparam("dispatcher", "db_url", DBURL)
modparam("dispatcher", "ds_ping_interval", 10)

request_route {
    if (!ds_select_dst(1, 4)) {  # group 1, round-robin
        send_reply("503","Service Unavailable");
        exit;
    }
    t_relay();
}
```

---

## OpenSIPS

### Key concepts
- **Script** (`opensips.cfg`) is similar to Kamailio but with different module names.
- **B2BUA** mode via `b2b_entities` + `b2b_logic` modules.
- **MI** (Management Interface) for runtime control: `opensips-cli -x mi`.
- **dialog**, **registrar**, **drouting** (dynamic routing) are core modules.

### Dynamic routing snippet
```cfg
loadmodule "drouting.so"
modparam("drouting", "db_url", "mysql://opensips:pass@localhost/opensips")

route[PSTN] {
    if (!do_routing("0")) {
        send_reply(503, "No route");
        exit;
    }
    t_relay();
}
```

### MI commands
```bash
opensips-cli -x mi list_routes
opensips-cli -x mi dr_reload
opensips-cli -x mi get_statistics dialog:
```

---

## VoIP Protocols & RTP

### SIP fundamentals
- **INVITE / 200 OK / ACK** — call setup three-way handshake.
- **SDP** negotiated in INVITE and 200 OK — carries codec list, RTP IP/port.
- **re-INVITE** — used for hold, transfer, codec renegotiation.
- **REFER** — blind/attended transfer signaling.
- **OPTIONS** — keepalive / capability discovery.

### RTP / SRTP
- RTP (RFC 3550) carries encoded audio/video on even UDP ports.
- RTCP (RFC 3551) on the next odd port for statistics.
- **SRTP** (RFC 3711) — encrypted RTP; requires DTLS-SRTP or SDES key exchange.
- Codec payload types: PCMU=0, PCMA=8, G729=18, opus=dynamic (96–127).

### NAT traversal
- **STUN** — discover public IP/port.
- **TURN** — relay media when STUN fails.
- **ICE** — coordinate STUN/TURN negotiation (mandatory for WebRTC).
- **rtpengine** — kernel-space RTP proxy used with Kamailio/OpenSIPS for media relay.
- **rtpproxy** — userspace RTP proxy alternative.

### rtpengine integration (Kamailio)
```cfg
loadmodule "rtpengine.so"
modparam("rtpengine", "rtpengine_sock", "udp:127.0.0.1:2223")

route[MEDIA] {
    rtpengine_manage("replace-origin replace-session-connection");
}
```

---

## TLS / SRTP Security

- **SIP TLS** — wrap SIP signaling in TLS (port 5061). Configure certs in profile.
- **SRTP** — encrypt RTP media. Always pair with TLS signaling.
- **FreeSWITCH**: set `<param name="tls" value="true"/>` in sip_profile + `<param name="rtp-secure-media" value="true"/>`.
- **Asterisk PJSIP**: `media_encryption=sdes` or `media_encryption=dtls` in endpoint.
- **Kamailio**: load `tls.so`, set `tls_method=TLSv1.2`, supply cert/key paths.

---

## Codec Reference

| Codec | Bitrate | Notes |
|---|---|---|
| G.711 µ-law (PCMU) | 64 kbps | North America standard |
| G.711 A-law (PCMA) | 64 kbps | Europe/international |
| G.729 | 8 kbps | Compressed, requires license |
| Opus | 6–510 kbps | WebRTC default, variable |
| G.722 | 64 kbps | HD voice (wideband) |
| iLBC | 13.3/15.2 kbps | Lossy-tolerant |

---

## Debugging & Troubleshooting

### Capture SIP/RTP traffic
```bash
# SIP only
sngrep -I capture.pcap

# tcpdump SIP
tcpdump -i eth0 -w sip.pcap 'port 5060 or port 5061'

# tcpdump RTP (specific call)
tcpdump -i eth0 -w rtp.pcap 'udp portrange 10000-20000'
```

### FreeSWITCH log levels
```
fs_cli -x "sofia loglevel all 9"   # verbose SIP trace
fs_cli -x "console loglevel debug"
```

### Asterisk SIP debug
```
asterisk -rx "pjsip set logger on"
asterisk -rx "core set verbose 5"
```

### Kamailio debug
```bash
# Increase log level at runtime
kamctl fifo debug 3
# Check registered endpoints
kamctl ul show
```

### OpenSIPS debug
```bash
opensips-cli -x mi log_level 4
opensips-cli -x mi ul_dump
```

---

## Common Patterns in This Project

- **RTP media gateway** connects WebRTC browser clients to PSTN via FreeSWITCH/Asterisk.
- **Kamailio / OpenSIPS** sits at the edge for SIP proxy, load-balancing, and TLS termination.
- **rtpengine** handles RTP relay/transcoding between NAT'd endpoints.
- **Python ESL / ARI** used for call control logic in the backend (`backend/app/infrastructure/telephony/`).
- SIP credentials and trunk config stored in tenant-scoped DB tables; loaded dynamically.

## Additional Resources

- For detailed architecture decisions, see [reference.md](reference.md)
