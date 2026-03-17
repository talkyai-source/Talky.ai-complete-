# PBX / VoIP Telephony — Extended Reference

## SIP Call Flow (INVITE dialogue)

```
UAC                    Proxy/B2BUA               UAS
 |---INVITE----------->|                           |
 |                     |---INVITE---------------->|
 |                     |<--100 Trying-------------|
 |<--100 Trying--------|                           |
 |                     |<--180 Ringing------------|
 |<--180 Ringing-------|                           |
 |                     |<--200 OK (SDP answer)----|
 |<--200 OK------------|                           |
 |---ACK-------------->|---ACK------------------->|
 |<====== RTP =========================>|
 |---BYE-------------->|---BYE------------------->|
 |                     |<--200 OK-----------------|
 |<--200 OK------------|                           |
```

---

## FreeSWITCH — Advanced Topics

### mod_xml_curl — fetch dialplan from HTTP
```xml
<!-- conf/autoload_configs/xml_curl.conf.xml -->
<configuration name="xml_curl.conf" description="XML cURL">
  <bindings>
    <binding name="dialplan">
      <param name="gateway-url" value="http://backend:8000/freeswitch/dialplan"/>
      <param name="bindings" value="dialplan"/>
    </binding>
  </bindings>
</configuration>
```

### ESL inbound connection (Python asyncio)
```python
import asyncio
from ESL import ESLconnection

async def watch_events():
    con = ESLconnection("127.0.0.1", "8021", "ClueCon")
    con.events("plain", "CHANNEL_CREATE CHANNEL_DESTROY DTMF")
    while True:
        e = con.recvEvent()
        if e:
            print(e.getHeader("Event-Name"), e.getHeader("Caller-Destination-Number"))
        await asyncio.sleep(0.01)
```

### Lua script — simple IVR
```lua
-- ivr.lua
session:answer()
session:sleep(500)
session:streamFile("ivr/welcome.wav")
local digit = session:getDigits(1, "#", 5000)
if digit == "1" then
    session:execute("transfer", "sales XML default")
elseif digit == "2" then
    session:execute("transfer", "support XML default")
else
    session:streamFile("ivr/invalid.wav")
    session:hangup()
end
```

---

## Asterisk — Advanced Topics

### AGI script (Python)
```python
#!/usr/bin/env python3
import sys

def agi_command(cmd):
    sys.stdout.write(cmd + "\n")
    sys.stdout.flush()
    return sys.stdin.readline().strip()

agi_command("ANSWER")
agi_command('STREAM FILE "hello-world" ""')
result = agi_command('GET DIGIT 1 ""')
agi_command("HANGUP")
```

### AMI — originate call
```python
import socket, time

s = socket.socket()
s.connect(("127.0.0.1", 5038))
s.recv(1024)  # banner

cmd = (
    "Action: Login\r\nUsername: admin\r\nSecret: secret\r\n\r\n"
    "Action: Originate\r\nChannel: PJSIP/1001\r\nExten: 1002\r\n"
    "Context: from-internal\r\nPriority: 1\r\nAsync: true\r\n\r\n"
)
s.sendall(cmd.encode())
time.sleep(1)
print(s.recv(4096).decode())
s.close()
```

### PJSIP transport config
```ini
[transport-udp]
type=transport
protocol=udp
bind=0.0.0.0:5060

[transport-tls]
type=transport
protocol=tls
bind=0.0.0.0:5061
cert_file=/etc/asterisk/keys/asterisk.crt
priv_key_file=/etc/asterisk/keys/asterisk.key
method=tlsv1_2
```

---

## Kamailio — Advanced Topics

### TLS configuration
```cfg
loadmodule "tls.so"
modparam("tls", "tls_method", "TLSv1.2")
modparam("tls", "certificate", "/etc/kamailio/tls/server.pem")
modparam("tls", "private_key", "/etc/kamailio/tls/server.key")
modparam("tls", "ca_list", "/etc/kamailio/tls/ca.pem")
modparam("tls", "verify_certificate", 1)
```

### htable — fast in-memory key/value
```cfg
loadmodule "htable.so"
modparam("htable", "htable", "calllimit=>size=8;initval=0")

route[RATELIMIT] {
    $sht(calllimit=>$si) = $sht(calllimit=>$si) + 1;
    if ($sht(calllimit=>$si) > 10) {
        sl_send_reply("429", "Too Many Requests");
        exit;
    }
}
```

### presence (BLF / MWI)
```cfg
loadmodule "presence.so"
loadmodule "presence_xml.so"

modparam("presence", "db_url", DBURL)

request_route {
    if (is_method("SUBSCRIBE")) {
        handle_subscribe();
        exit;
    }
    if (is_method("NOTIFY")) {
        route(RELAY);
        exit;
    }
}
```

---

## OpenSIPS — Advanced Topics

### B2BUA setup
```cfg
loadmodule "b2b_entities.so"
loadmodule "b2b_logic.so"

modparam("b2b_logic", "db_url", "mysql://opensips:pass@localhost/opensips")

route[B2B_CALL] {
    b2b_init_request("top hiding");
}
```

### Fraud detection with pike
```cfg
loadmodule "pike.so"
modparam("pike", "sampling_time_unit", 2)
modparam("pike", "reqs_density_per_unit", 30)
modparam("pike", "remove_latency", 4)

request_route {
    if (!pike_check_req()) {
        sl_send_reply("503","Slow down");
        exit;
    }
}
```

---

## RTP / Media Deep Dive

### SDP offer/answer example
```
v=0
o=FreeSWITCH 1234567890 1234567891 IN IP4 203.0.113.1
s=FreeSWITCH
c=IN IP4 203.0.113.1
t=0 0
m=audio 16384 RTP/SAVP 0 8 101
a=rtpmap:0 PCMU/8000
a=rtpmap:8 PCMA/8000
a=rtpmap:101 telephone-event/8000
a=fmtp:101 0-16
a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:BASE64KEYGOESHERE==
a=sendrecv
```

### rtpengine control (ng protocol)
```python
import bencode, socket

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.connect(("127.0.0.1", 2223))

cmd = bencode.encode({
    b"command": b"offer",
    b"call-id": b"abc123",
    b"from-tag": b"tag1",
    b"sdp": SDP_BYTES,
    b"replace": [b"origin", b"session-connection"],
    b"ICE": b"remove",
    b"transport protocol": b"RTP/SAVPF",
})
cookie = b"abc "
sock.send(cookie + cmd)
response = bencode.decode(sock.recv(65535)[4:])
print(response[b"sdp"].decode())
```

### Jitter buffer tuning (FreeSWITCH)
```xml
<param name="rtp-jitter-buffer-plc" value="true"/>
<param name="rtp-jitter-buffer-during-bridge" value="false"/>
<param name="jitterbuffer-msec" value="60:200:40"/>
<!-- min:max:max_packet_loss_percent -->
```

---

## WebRTC ↔ SIP Gateway

### Typical flow
1. Browser opens WebSocket to FreeSWITCH (`mod_verto`) or Asterisk (ARI over WS).
2. ICE/DTLS handshake establishes SRTP session between browser and media server.
3. Media server transcodes / relays RTP to PSTN trunk via G.711.
4. SIP signaling goes to Kamailio/OpenSIPS proxy for routing.

### mod_verto endpoint (FreeSWITCH)
```xml
<!-- conf/autoload_configs/verto.conf.xml -->
<profile name="default">
  <param name="bind-local" value="0.0.0.0:8081"/>
  <param name="tls-cert-dir" value="/etc/freeswitch/tls"/>
  <param name="apply-candidate-acl" value="localnet.auto"/>
  <param name="rtp-ip" value="auto-nat"/>
  <param name="ext-rtp-ip" value="auto-nat"/>
  <param name="local-network" value="localnet.auto"/>
  <param name="outbound-codec-string" value="opus,PCMU,PCMA"/>
</profile>
```

---

## Monitoring & Metrics

### Key metrics to track
| Metric | Tool | Alert threshold |
|---|---|---|
| ASR (Answer Seizure Ratio) | Custom / Prometheus | < 60% |
| ACD (Avg Call Duration) | CDR analysis | Sudden drop |
| MOS score | RTCP-XR / Homer | < 3.5 |
| RTP packet loss | rtpengine stats | > 1% |
| SIP 5xx rate | Kamailio xlog | > 2% |
| Concurrent calls | FS `show channels` | Near capacity |

### Homer SIP capture (HEP)
```cfg
# Kamailio — send SIP to Homer
loadmodule "siptrace.so"
modparam("siptrace", "duplicate_uri", "sip:homer:9060")
modparam("siptrace", "hep_mode_on", 1)
modparam("siptrace", "trace_to_database", 0)
modparam("siptrace", "trace_flag", 22)
modparam("siptrace", "trace_on", 1)
```

---

## Tenant / Multi-Tenant Patterns

- Store SIP credentials per-tenant in DB; load dynamically into FS/Asterisk via XML cURL or ARI.
- Use Kamailio `dispatcher` groups per-tenant for isolated routing tables.
- RLS (Row-Level Security) in PostgreSQL ensures tenant data isolation (see `backend/app/core/tenant_rls.py`).
- Rate-limit per-tenant using `htable` in Kamailio or `pike` in OpenSIPS.
- Concurrency limits enforced in `backend/app/domain/services/telephony_concurrency_limiter.py`.
