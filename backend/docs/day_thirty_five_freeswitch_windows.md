# Day 35: FreeSWITCH Windows Installation & ESL Connection

**Date:** January 20, 2026 (Afternoon Session)  
**Objective:** Resolve Docker networking issues by installing FreeSWITCH natively on Windows

---

## Summary

Docker-based FreeSWITCH deployment had insurmountable networking issues with Windows Docker Desktop's WSL2 backend. The solution was to install FreeSWITCH natively on Windows, which immediately resolved all ESL connectivity issues.

---

## Problem: Docker Networking Limitations

### Issue Analysis

With Docker Desktop on Windows using WSL2:

1. **ESL Port Not Accessible**
   - Container bound to 0.0.0.0:8021 inside WSL2 VM
   - Port not forwarded to Windows host despite `network_mode: host`
   - `Test-NetConnection localhost -Port 8021` returned `TcpTestSucceeded: False`

2. **Cross-Subnet Routing**
   - Container on Docker bridge network (172.x.x.x)
   - PBX on local network (192.168.1.6)
   - Required mirrored networking in WSL2

3. **Attempted Workarounds**
   - Created Docker CLI client (`freeswitch_docker_cli.py`) using `docker exec fs_cli`
   - Still encountered issues with command execution reliability

---

## Solution: Native Windows FreeSWITCH

### Installation

Downloaded and installed FreeSWITCH Windows MSI:
- **Source:** `https://files.freeswitch.org/windows/installer/x64/`
- **Version:** FreeSWITCH-1.10.12-Release-x64.msi
- **Installation Path:** `C:\Program Files\FreeSWITCH`

### Gateway Configuration

Created 3CX gateway configuration:

```xml
<!-- C:\Program Files\FreeSWITCH\conf\sip_profiles\external\3cx-pbx.xml -->
<include>
  <gateway name="3cx-pbx">
    <param name="username" value="1001"/>
    <param name="password" value="1001"/>
    <param name="realm" value="192.168.1.6"/>
    <param name="proxy" value="192.168.1.6:5060"/>
    <param name="register" value="true"/>
    <param name="register-transport" value="udp"/>
    <param name="expire-seconds" value="300"/>
    <param name="retry-seconds" value="30"/>
    <param name="caller-id-in-from" value="true"/>
    <param name="ping" value="30"/>
  </gateway>
</include>
```

### Service Management

```powershell
# Start FreeSWITCH service
Start-Service -Name "FreeSWITCH"

# Load mod_sofia
& "C:\Program Files\FreeSWITCH\fs_cli.exe" -x "load mod_sofia"

# Restart profile with gateway
& "C:\Program Files\FreeSWITCH\fs_cli.exe" -x "sofia profile external restart reloadxml"
```

---

## ESL Connection Success

### Environment Configuration

Updated `.env` for localhost ESL:
```env
# FreeSWITCH ESL (Event Socket Library)
# Using localhost for native Windows FreeSWITCH installation
FREESWITCH_ESL_HOST=127.0.0.1
FREESWITCH_ESL_PORT=8021
FREESWITCH_ESL_PASSWORD=ClueCon
```

### Connection Test

```bash
curl -X POST "http://localhost:8000/api/v1/sip/freeswitch/start?use_docker=false"
```

**Result:**
```json
{
  "status": "connected",
  "message": "Connected to FreeSWITCH ESL",
  "mode": "esl_socket",
  "esl": {"host": "127.0.0.1", "port": 8021},
  "gateway_status": "Name: 3cx-pbx\nProfile: external\nState: REGED"
}
```

✅ **Gateway registered successfully with 3CX PBX!**

---

## Call Origination Fix

### Issue: DESTINATION_OUT_OF_ORDER

Initial call attempts failed with `-ERR DESTINATION_OUT_OF_ORDER`.

**Root Cause:** Complex originate command syntax with channel variables.

### Original (Broken)
```python
command = (
    f"originate {{{{"
    f"origination_caller_id_number={caller_id},"
    f"origination_caller_id_name=Talky AI,"
    f"call_timeout={timeout}"
    f"}}}}{dial_string} {app_string}"
)
```

### Fixed (Simple)
```python
command = f"originate {dial_string} {app_string}"
# Example: originate sofia/gateway/3cx-pbx/1002 &echo
```

**Result:** Calls now originate successfully!

---

## Successful Call Test

```bash
curl -X POST "http://localhost:8000/api/v1/sip/freeswitch/call?to_extension=1002&with_greeting=false"
```

**Response:**
```json
{
  "status": "calling",
  "call_uuid": "a43de28a-898e-46b6-bd57-5b65af8522fe",
  "to_extension": "1002",
  "mode": "esl_socket",
  "message": "Calling 1002..."
}
```

✅ **Phone rang and call connected!**

---

## Files Created/Modified

| File | Status | Description |
|------|--------|-------------|
| `freeswitch_config/windows/3cx-pbx.xml` | NEW | Gateway config for Windows FS |
| `app/infrastructure/telephony/freeswitch_docker_cli.py` | NEW | Docker CLI fallback (unused) |
| `app/infrastructure/telephony/freeswitch_esl.py` | MODIFIED | Simplified originate command |
| `.env` | MODIFIED | ESL host set to localhost |

---

## Key Learnings

1. **Docker Desktop on Windows** has significant networking limitations for VoIP applications
2. **Native installation** is more reliable for development on Windows
3. **Simple ESL commands** work better than complex syntax
4. **Test with fs_cli first** to validate commands before implementing in Python

---

## Next Steps

1. Add AI greeting playback using TTS
2. Implement full conversation loop with STT/LLM/TTS
3. Create AI conversation controller
