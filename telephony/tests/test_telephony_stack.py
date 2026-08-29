import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TELEPHONY_ROOT = REPO_ROOT / "telephony"
SCRIPTS_DIR = TELEPHONY_ROOT / "scripts"
CONF_DIR = TELEPHONY_ROOT / "opensips" / "conf"
FS_CONF = (
    TELEPHONY_ROOT
    / "freeswitch"
    / "conf"
    / "autoload_configs"
    / "event_socket.conf.xml"
)
ASTERISK_PJSIP_CONF = TELEPHONY_ROOT / "asterisk" / "conf" / "pjsip.conf"
ASTERISK_MODULES_CONF = TELEPHONY_ROOT / "asterisk" / "conf" / "modules.conf"
ENV_EXAMPLE = TELEPHONY_ROOT / "deploy" / "docker" / ".env.telephony.example"
CHECKLIST_DOC = TELEPHONY_ROOT / "docs" / "phase_1" / "07_phase_one_gated_checklist.md"
PLAN_DOC = TELEPHONY_ROOT / "docs" / "phase_1" / "plan.md"
PHASE3_CHECKLIST_DOC = (
    TELEPHONY_ROOT / "docs" / "phase_3" / "02_phase_three_gated_checklist.md"
)
DAY4_PLAN_DOC = (
    TELEPHONY_ROOT / "docs" / "phase_3" / "22_day4_cpp_gateway_execution_plan.md"
)
DAY4_EVIDENCE_DOC = TELEPHONY_ROOT / "docs" / "phase_3" / "day4_cpp_gateway_evidence.md"
DAY5_PLAN_DOC = (
    TELEPHONY_ROOT
    / "docs"
    / "phase_3"
    / "23_day5_asterisk_cpp_e2e_echo_execution_plan.md"
)
DAY5_EVIDENCE_DOC = (
    TELEPHONY_ROOT / "docs" / "phase_3" / "day5_asterisk_cpp_echo_evidence.md"
)
WSK_COMPLETION_DOC = TELEPHONY_ROOT / "docs" / "phase_3" / "03_ws_k_completion.md"
WSM_COMPLETION_DOC = TELEPHONY_ROOT / "docs" / "phase_3" / "11_ws_m_completion.md"
WSM_MEDIA_REPORT_DOC = (
    TELEPHONY_ROOT / "docs" / "phase_3" / "08_ws_m_media_quality_report.md"
)
WSM_TRANSFER_REPORT_DOC = (
    TELEPHONY_ROOT / "docs" / "phase_3" / "09_ws_m_transfer_success_report.md"
)
WSM_LONGCALL_REPORT_DOC = (
    TELEPHONY_ROOT / "docs" / "phase_3" / "10_ws_m_long_call_session_timer_report.md"
)
WSN_PLAN_DOC = (
    TELEPHONY_ROOT / "docs" / "phase_3" / "12_ws_n_failure_injection_recovery_plan.md"
)
WSN_REPORT_DOC = (
    TELEPHONY_ROOT / "docs" / "phase_3" / "13_ws_n_failure_recovery_report.md"
)
WSO_PLAN_DOC = (
    TELEPHONY_ROOT / "docs" / "phase_3" / "15_ws_o_production_cutover_plan.md"
)
WSO_REPORT_DOC = TELEPHONY_ROOT / "docs" / "phase_3" / "16_ws_o_cutover_report.md"
WSO_DECOM_DOC = (
    TELEPHONY_ROOT / "docs" / "phase_3" / "17_ws_o_decommission_readiness_checklist.md"
)
PHASE3_SIGNOFF_DOC = TELEPHONY_ROOT / "docs" / "phase_3" / "18_phase_three_signoff.md"
PROM_CONFIG = TELEPHONY_ROOT / "observability" / "prometheus" / "prometheus.yml"
PROM_RULES = (
    TELEPHONY_ROOT
    / "observability"
    / "prometheus"
    / "rules"
    / "telephony_ws_k_rules.yml"
)
ALERTMANAGER_CONFIG = (
    TELEPHONY_ROOT / "observability" / "alertmanager" / "alertmanager.yml"
)


def _find_bash() -> str | None:
    configured = os.getenv("TELEPHONY_BASH")
    if configured:
        return configured
    if os.name == "nt":
        for candidate in (
            Path(r"C:\Program Files\Git\bin\bash.exe"),
            Path(r"C:\Program Files\Git\usr\bin\bash.exe"),
        ):
            if candidate.exists():
                return str(candidate)
    return shutil.which("bash")


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _tail(text: str, lines: int = 60) -> str:
    split_lines = text.splitlines()
    return "\n".join(split_lines[-lines:])


class TelephonyStaticTests(unittest.TestCase):
    def test_required_scripts_exist(self) -> None:
        required = [
            SCRIPTS_DIR / "verify_ws_a.sh",
            SCRIPTS_DIR / "verify_ws_b.sh",
            SCRIPTS_DIR / "verify_ws_c.sh",
            SCRIPTS_DIR / "verify_ws_d.sh",
            SCRIPTS_DIR / "verify_ws_e.sh",
            SCRIPTS_DIR / "verify_ws_g.sh",
            SCRIPTS_DIR / "verify_ws_h.sh",
            SCRIPTS_DIR / "verify_ws_i.sh",
            SCRIPTS_DIR / "verify_ws_j.sh",
            SCRIPTS_DIR / "verify_ws_k.sh",
            SCRIPTS_DIR / "verify_ws_l.sh",
            SCRIPTS_DIR / "verify_ws_m.sh",
            SCRIPTS_DIR / "verify_ws_n.sh",
            SCRIPTS_DIR / "verify_ws_o.sh",
            SCRIPTS_DIR / "failure_drill_opensips.sh",
            SCRIPTS_DIR / "failure_drill_rtpengine.sh",
            SCRIPTS_DIR / "failure_drill_freeswitch_backup.sh",
            SCRIPTS_DIR / "failure_drill_combined.sh",
            SCRIPTS_DIR / "canary_set_stage.sh",
            SCRIPTS_DIR / "canary_freeze.sh",
            SCRIPTS_DIR / "canary_rollback.sh",
            SCRIPTS_DIR / "canary_lock.sh",
            SCRIPTS_DIR / "assert_canary_ingress.sh",
            SCRIPTS_DIR / "generate_opensips_tls_certs.sh",
            SCRIPTS_DIR / "generate_kamailio_tls_certs.sh",
            SCRIPTS_DIR / "sip_options_probe.py",
            SCRIPTS_DIR / "sip_options_probe_tls.sh",
            SCRIPTS_DIR / "sip_invite_call_probe.py",
            SCRIPTS_DIR / "verify_day1_lan_setup.sh",
            SCRIPTS_DIR / "verify_day2_asterisk_first_call.sh",
            SCRIPTS_DIR / "verify_day3_opensips_edge.sh",
            SCRIPTS_DIR / "verify_day4_cpp_gateway.sh",
            SCRIPTS_DIR / "verify_day5_asterisk_cpp_echo.sh",
            SCRIPTS_DIR / "verify_day6_media_resilience.sh",
            SCRIPTS_DIR / "verify_day7_stt_streaming.sh",
            SCRIPTS_DIR / "verify_day8_tts_bargein.sh",
            SCRIPTS_DIR / "verify_day9_transfer_tenant_controls.sh",
            SCRIPTS_DIR / "verify_day10_concurrency_soak.sh",
            SCRIPTS_DIR / "gateway_test_env.sh",
            SCRIPTS_DIR / "day4_rtp_probe.py",
            SCRIPTS_DIR / "day5_ari_external_media_controller.py",
            SCRIPTS_DIR / "day5_sip_rtp_echo_probe.py",
            SCRIPTS_DIR / "day6_media_resilience_probe.py",
            SCRIPTS_DIR / "day7_stt_stream_probe.py",
            SCRIPTS_DIR / "day8_tts_bargein_probe.py",
            SCRIPTS_DIR / "day9_transfer_tenant_probe.py",
            SCRIPTS_DIR / "day10_concurrency_soak_probe.py",
            SCRIPTS_DIR / "day10_restart_recovery_drill.sh",
        ]
        for script in required:
            self.assertTrue(script.exists(), f"Missing script: {script}")
            if script.name not in {
                "assert_canary_ingress.sh",
                "canary_lock.sh",
                "gateway_test_env.sh",
            }:
                self.assertTrue(
                    os.access(script, os.X_OK), f"Script is not executable: {script}"
                )

    def test_script_syntax_is_valid(self) -> None:
        scripts = [
            "verify_ws_a.sh",
            "verify_ws_b.sh",
            "verify_ws_c.sh",
            "verify_ws_d.sh",
            "verify_ws_e.sh",
            "verify_ws_g.sh",
            "verify_ws_h.sh",
            "verify_ws_i.sh",
            "verify_ws_j.sh",
            "verify_ws_k.sh",
            "verify_ws_l.sh",
            "verify_ws_m.sh",
            "verify_ws_n.sh",
            "verify_ws_o.sh",
            "failure_drill_opensips.sh",
            "failure_drill_rtpengine.sh",
            "failure_drill_freeswitch_backup.sh",
            "failure_drill_combined.sh",
            "canary_set_stage.sh",
            "canary_freeze.sh",
            "canary_rollback.sh",
            "canary_lock.sh",
            "assert_canary_ingress.sh",
            "generate_opensips_tls_certs.sh",
            "generate_kamailio_tls_certs.sh",
            "sip_options_probe_tls.sh",
            "verify_day1_lan_setup.sh",
            "verify_day2_asterisk_first_call.sh",
            "verify_day3_opensips_edge.sh",
            "verify_day4_cpp_gateway.sh",
            "verify_day5_asterisk_cpp_echo.sh",
            "verify_day6_media_resilience.sh",
            "verify_day7_stt_streaming.sh",
            "verify_day8_tts_bargein.sh",
            "verify_day9_transfer_tenant_controls.sh",
            "verify_day10_concurrency_soak.sh",
            "gateway_test_env.sh",
            "day10_restart_recovery_drill.sh",
        ]
        bash = _find_bash()
        if not bash:
            self.skipTest("No Bash runtime is available for syntax validation")
        for name in scripts:
            script = SCRIPTS_DIR / name
            proc = subprocess.run(
                [bash, "-n", str(script)],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
                timeout=30,
            )
            self.assertEqual(
                proc.returncode,
                0,
                f"Syntax check failed for {name}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}",
            )

    def test_opensips_ws_b_security_modules_present(self) -> None:
        cfg = _read_text(CONF_DIR / "opensips.cfg")
        required_markers = [
            'loadmodule "proto_tls.so"',
            'loadmodule "tls_mgm.so"',
            'loadmodule "tls_openssl.so"',
            'loadmodule "pike.so"',
            'loadmodule "ratelimit.so"',
            'loadmodule "sipmsgops.so"',
            'modparam("tls_mgm", "server_domain", "default")',
            'modparam("tls_mgm", "match_ip_address", "[default]*")',
            'modparam("tls_mgm", "tls_method", "[default]TLSv1_2-TLSv1_3")',
            'modparam("tls_mgm", "certificate", "[default]/etc/opensips/certs/server.crt")',
            'modparam("tls_mgm", "private_key", "[default]/etc/opensips/certs/server.key")',
            "OPENSIPS_CANARY_SOURCE_REGEX",
            'if (!($si =~ "$def(OPENSIPS_CANARY_SOURCE_REGEX)"))',
            "pike_check_req()",
            'rl_check("invite", 60, "TAILDROP")',
            "socket = tls:0.0.0.0:15061",
            "ds_select_dst(2, 4)",
        ]
        for marker in required_markers:
            self.assertIn(marker, cfg, f"Missing marker in opensips.cfg: {marker}")

    def test_opensips_acl_and_tls_files_exist(self) -> None:
        self.assertTrue((CONF_DIR / "address.list").exists(), "Missing address.list")
        self.assertTrue(
            (TELEPHONY_ROOT / "opensips" / "tls.cfg").exists(), "Missing tls.cfg"
        )
        cert_gitignore = TELEPHONY_ROOT / "opensips" / "certs" / ".gitignore"
        cert_gitkeep = TELEPHONY_ROOT / "opensips" / "certs" / ".gitkeep"
        self.assertTrue(cert_gitignore.exists(), "Missing certs .gitignore")
        self.assertTrue(cert_gitkeep.exists(), "Missing certs .gitkeep")

    def test_asterisk_primary_pjsip_baseline(self) -> None:
        pjsip_cfg = _read_text(ASTERISK_PJSIP_CONF)
        modules_cfg = _read_text(ASTERISK_MODULES_CONF)
        self.assertIn("type=transport", pjsip_cfg)
        self.assertIn("bind=0.0.0.0:5070", pjsip_cfg)
        self.assertIn("direct_media=no", pjsip_cfg)
        self.assertIn("disallow=all", pjsip_cfg)
        self.assertIn("allow=ulaw", pjsip_cfg)
        self.assertNotIn("allow=ulaw,alaw,g722", pjsip_cfg)
        self.assertIn("endpoint_identifier_order=ip", pjsip_cfg)
        self.assertIn("type=identify", pjsip_cfg)
        self.assertIn("match=127.0.0.1:15060", pjsip_cfg)
        self.assertIn("outbound_proxy=sip:127.0.0.1:15060\\;lr", pjsip_cfg)
        self.assertIn("noload => chan_sip.so", modules_cfg)

    def test_freeswitch_backup_config_retained(self) -> None:
        cfg = _read_text(FS_CONF)
        self.assertIn('listen-ip" value="127.0.0.1"', cfg)
        self.assertIn('apply-inbound-acl" value="loopback.auto"', cfg)
        self.assertTrue((TELEPHONY_ROOT / "freeswitch" / "README.md").exists())
        compose = _read_text(
            TELEPHONY_ROOT / "deploy" / "docker" / "docker-compose.telephony.yml"
        )
        freeswitch_service = compose.split("  freeswitch:", 1)[1].split(
            "  rtpengine:", 1
        )[0]
        self.assertIn('profiles: ["backup"]', freeswitch_service)

    def test_opensips_ws_e_canary_markers_present(self) -> None:
        cfg = _read_text(CONF_DIR / "opensips.cfg")
        required_markers = [
            'loadmodule "cfgutils.so"',
            'loadmodule "mi_fifo.so"',
            'modparam("cfgutils", "initial_probability", 0)',
            'modparam("mi_fifo", "fifo_name", "/tmp/opensips_fifo")',
            "OPENSIPS_CANARY_ENABLED",
            "OPENSIPS_CANARY_PERCENT",
            "if ($def(OPENSIPS_CANARY_ENABLED) != 1",
            "|| $def(OPENSIPS_CANARY_PERCENT) != 100",
            "|| $def(OPENSIPS_CANARY_FREEZE) != 0)",
            '$rU != "$def(OPENSIPS_CANARY_DID)"',
            'append_hf("X-Talky-Agent-ID: $def(OPENSIPS_CANARY_AGENT_ID)\\r\\n")',
            "ds_select_dst(2, 4)",
        ]
        for marker in required_markers:
            self.assertIn(marker, cfg, f"Missing WS-E marker in opensips.cfg: {marker}")

        self.assertNotIn("rand_set_prob", cfg)
        self.assertNotIn("ds_select_dst(1,", cfg)
        self.assertNotIn("falling back", cfg.lower())

        dispatcher = _read_text(CONF_DIR / "dispatcher.list")
        entries = [
            line.split()
            for line in dispatcher.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertEqual([entry[0] for entry in entries].count("2"), 1)
        self.assertTrue(all(entry[1] == "sip:127.0.0.1:5070" for entry in entries))
        self.assertIn('remove_hf("X-Talky-Original-DID")', cfg)
        self.assertIn('append_hf("X-Talky-Original-DID: $rU\\r\\n")', cfg)

    def test_env_example_has_ws_b_keys(self) -> None:
        env = _read_text(ENV_EXAMPLE)
        required = [
            "OPENSIPS_SIP_PORT=15060",
            "OPENSIPS_TLS_PORT=15061",
            "OPENSIPS_TLS_ONLY=0",
            "OPENSIPS_CANARY_ENABLED=0",
            "OPENSIPS_CANARY_PERCENT=0",
            "OPENSIPS_CANARY_FREEZE=1",
            "OPENSIPS_CANARY_DID=__UNCONFIGURED_DID__",
            "OPENSIPS_CANARY_AGENT_ID=__UNCONFIGURED_AGENT__",
            "OPENSIPS_CANARY_SOURCE_REGEX='^127\\.0\\.0\\.1$'",
            "FREESWITCH_ESL_PORT=8021",
            "ASTERISK_SIP_PORT=5070",
            "ASTERISK_ARI_HOST=127.0.0.1",
            "ASTERISK_ARI_PORT=8088",
            "ASTERISK_ARI_USERNAME=day5",
            "ASTERISK_ARI_APP=talky_day5",
            "DAY5_TEST_EXTENSION=750",
            "ASTERISK_IMAGE=talky/asterisk:bookworm",
        ]
        for marker in required:
            self.assertIn(marker, env, f"Missing env key/value: {marker}")
        self.assertIn(
            "FREESWITCH_ESL_PASSWORD=", env, "Missing FREESWITCH_ESL_PASSWORD key"
        )
        self.assertIn(
            "ASTERISK_ARI_PASSWORD=", env, "Missing ASTERISK_ARI_PASSWORD key"
        )

    def test_inbound_canary_admission_precedes_media_and_has_no_fallback(self) -> None:
        for name in ("opensips.cfg", "opensips-with-auth.cfg"):
            cfg = _read_text(CONF_DIR / name)
            did_gate = cfg.index('$rU != "$def(OPENSIPS_CANARY_DID)"')
            target_gate = cfg.index("if (!ds_select_dst(2, 4))")
            media_gate = cfg.rindex("route(WS_M_MANAGE_RTP);")
            self.assertLess(did_gate, target_gate, name)
            self.assertLess(target_gate, media_gate, name)
            self.assertNotIn("ds_select_dst(1,", cfg, name)
            self.assertNotIn("rand_set_prob", cfg, name)
            self.assertNotIn("falling back", cfg.lower(), name)
            self.assertIn('sl_send_reply(503, "Canary Asterisk Unavailable")', cfg)
            self.assertIn("|| $def(OPENSIPS_CANARY_FREEZE) != 0)", cfg)

    def test_asterisk_never_answers_before_admission_and_blocks_bypass(self) -> None:
        ext_cfg = _read_text(TELEPHONY_ROOT / "asterisk" / "conf" / "extensions.conf")
        inbound = ext_cfg.split("[from-opensips]", 1)[1].split("[ai-outbound]", 1)[0]
        inbound_code = "\n".join(
            line for line in inbound.splitlines() if not line.lstrip().startswith(";")
        )
        self.assertNotIn("Answer(", inbound_code)
        self.assertNotIn("Set(TALKY_ORIGINAL_DID=${EXTEN})", inbound_code)
        self.assertLess(
            inbound_code.index("X-Talky-Ingress-Policy"), inbound_code.index("Stasis(")
        )
        self.assertLess(
            inbound_code.index("X-Talky-Agent-ID"), inbound_code.index("Stasis(")
        )
        self.assertIn('"${TALKY_ORIGINAL_DID}" = "${EXTEN}"', inbound_code)
        self.assertIn("Hangup(21)", inbound_code)

        pjsip = _read_text(ASTERISK_PJSIP_CONF)
        self.assertIn("endpoint_identifier_order=ip", pjsip)
        self.assertIn("type=identify", pjsip)
        self.assertIn("match=127.0.0.1:15060", pjsip)
        lan_endpoint = pjsip.split("[lan-pbx]", 1)[1].split("[lan-pbx-identify]", 1)[0]
        self.assertIn("context=default", lan_endpoint)
        self.assertNotIn("context=from-opensips", lan_endpoint)

    def test_canary_startup_assertions_are_wired(self) -> None:
        script = SCRIPTS_DIR / "assert_canary_ingress.sh"
        compose = _read_text(
            TELEPHONY_ROOT / "deploy" / "docker" / "docker-compose.telephony.yml"
        )
        self.assertIn("assert_canary_ingress.sh opensips", compose)
        self.assertIn("assert_canary_ingress.sh asterisk", compose)
        self.assertIn('profiles: ["backup"]', compose)

        bash = _find_bash()
        if not bash:
            self.skipTest("No Bash runtime is available for startup assertion test")
        proc = subprocess.run(
            [bash, str(script), "all", str(ENV_EXAMPLE)],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Canary ingress assertions PASSED.", proc.stdout)

        sourced_regex = subprocess.run(
            [
                bash,
                "-c",
                'set -a; source "$1"; printf "%s" "$OPENSIPS_CANARY_SOURCE_REGEX"',
                "bash",
                str(ENV_EXAMPLE),
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=30,
        )
        self.assertEqual(sourced_regex.returncode, 0, sourced_regex.stderr)
        self.assertEqual(sourced_regex.stdout, r"^127\.0\.0\.1$")

        frozen_active_env = {
            **os.environ,
            "OPENSIPS_CANARY_ENABLED": "1",
            "OPENSIPS_CANARY_PERCENT": "100",
            "OPENSIPS_CANARY_FREEZE": "1",
            "OPENSIPS_CANARY_DID": "15551234567",
            "OPENSIPS_CANARY_AGENT_ID": "123e4567-e89b-42d3-a456-426614174000",
            "OPENSIPS_CANARY_SOURCE_REGEX": r"^127\.0\.0\.1$",
        }
        frozen = subprocess.run(
            [bash, str(script), "env"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=frozen_active_env,
            timeout=30,
        )
        self.assertNotEqual(frozen.returncode, 0)
        self.assertIn("freeze=1 blocks ingress", frozen.stdout + frozen.stderr)

        broad_source_env = {
            **os.environ,
            "OPENSIPS_CANARY_ENABLED": "0",
            "OPENSIPS_CANARY_PERCENT": "0",
            "OPENSIPS_CANARY_FREEZE": "1",
            "OPENSIPS_CANARY_SOURCE_REGEX": "^.*$",
        }
        broad_source = subprocess.run(
            [bash, str(script), "env"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=broad_source_env,
            timeout=30,
        )
        self.assertNotEqual(broad_source.returncode, 0)
        self.assertIn(
            "exact IPv4 alternatives only", broad_source.stdout + broad_source.stderr
        )

    def test_rollback_and_freeze_are_fail_closed(self) -> None:
        rollback = _read_text(SCRIPTS_DIR / "canary_rollback.sh")
        set_stage = _read_text(SCRIPTS_DIR / "canary_set_stage.sh")
        controller = _read_text(SCRIPTS_DIR / "canary_stage_controller.sh")
        freeze = _read_text(SCRIPTS_DIR / "canary_freeze.sh")
        lock = _read_text(SCRIPTS_DIR / "canary_lock.sh")

        self.assertIn('set_kv "OPENSIPS_CANARY_ENABLED" "0"', rollback)
        self.assertIn('set_kv "OPENSIPS_CANARY_PERCENT" "0"', rollback)
        self.assertIn('set_kv "OPENSIPS_CANARY_FREEZE" "1"', rollback)
        self.assertIn('"${compose_cmd[@]}" stop opensips', rollback)
        full_case = rollback.split("  full)", 1)[1].split("    ;;", 1)[0]
        self.assertLess(
            full_case.index("durable_rollback"), full_case.index("runtime_rollback")
        )
        runtime_case = rollback.split("  runtime)", 1)[1].split("    ;;", 1)[0]
        self.assertLess(
            runtime_case.index("durable_rollback"),
            runtime_case.index("runtime_rollback"),
        )
        self.assertIn('set_kv "OPENSIPS_CANARY_ENABLED" "0"', freeze)
        self.assertIn('set_kv "OPENSIPS_CANARY_PERCENT" "0"', freeze)
        self.assertLess(
            freeze.index('set_kv "OPENSIPS_CANARY_ENABLED" "0"'),
            freeze.index('"${compose_cmd[@]}" up -d opensips'),
        )
        self.assertIn('"${compose_cmd[@]}" stop opensips', freeze)
        self.assertIn("0|100)", set_stage)
        self.assertNotIn("0|5|20|25|50|100", set_stage)
        self.assertIn('if [[ "$STAGE_PERCENT" == "0" ]]', set_stage)
        self.assertIn('"${compose_cmd[@]}" stop opensips', set_stage)
        self.assertIn("STAGE_SEQUENCE=(0 100)", controller)
        self.assertIn('current_freeze" == "1"', set_stage)
        self.assertIn('ACTION" != "freeze"', freeze)
        self.assertIn("flock -n", lock)
        for script in (rollback, set_stage, freeze):
            self.assertIn('source "$LOCK_SCRIPT"', script)
            self.assertIn('acquire_canary_state_lock "$ENV_FILE"', script)
        self.assertLess(
            set_stage.index('acquire_canary_state_lock "$ENV_FILE"'),
            set_stage.index('current_freeze="$(grep'),
            "activation must re-read freeze only after acquiring the shared lock",
        )
        activation_block = set_stage.split("# Activation is the inverse:", 1)[1]
        self.assertLess(
            activation_block.index('"${candidate_compose[@]}" exec'),
            activation_block.index('mv -f "$candidate_env" "$ENV_FILE"'),
            "active durable state must not publish before runtime validation",
        )
        self.assertNotIn("--skip-gates", controller)
        self.assertIn("env/runtime unchanged", controller)

    def test_authenticated_canary_metrics_command_keeps_curl_as_executable(
        self,
    ) -> None:
        controller = _read_text(SCRIPTS_DIR / "canary_stage_controller.sh")

        self.assertIn(
            "curl_cmd=(curl -fsS --connect-timeout 3 --max-time 15)", controller
        )
        self.assertIn(
            'curl_cmd+=("--header" "@${metrics_header_file}")',
            controller,
        )
        self.assertIn('curl_cmd+=("$metrics_url")', controller)
        self.assertIn('metrics_token="${TELEPHONY_METRICS_TOKEN:-}"', controller)
        self.assertNotIn("--metrics-token", controller)
        self.assertNotIn("DECISION_METRICS_TOKEN", controller)
        self.assertNotRegex(controller, r"echo[^\n]*\$metrics_token")
        self.assertNotIn(
            'curl_cmd=(-H "X-Metrics-Token: ${metrics_token}"',
            controller,
        )

    def test_canary_activation_and_rollback_scripts_enforce_safe_state(self) -> None:
        bash = _find_bash()
        if not bash:
            self.skipTest("No Bash runtime is available for canary control test")

        env_text = _read_text(ENV_EXAMPLE)
        env_text = env_text.replace(
            "OPENSIPS_CANARY_FREEZE=1", "OPENSIPS_CANARY_FREEZE=0"
        )
        env_text = env_text.replace(
            "OPENSIPS_CANARY_DID=__UNCONFIGURED_DID__",
            "OPENSIPS_CANARY_DID=15551234567",
        )
        env_text = env_text.replace(
            "OPENSIPS_CANARY_AGENT_ID=__UNCONFIGURED_AGENT__",
            "OPENSIPS_CANARY_AGENT_ID=123e4567-e89b-42d3-a456-426614174000",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / "canary.env"
            env_file.write_text(env_text, encoding="utf-8")
            baseline_state = env_file.read_text(encoding="utf-8")

            activate = subprocess.run(
                [
                    bash,
                    str(SCRIPTS_DIR / "canary_set_stage.sh"),
                    "100",
                    str(env_file),
                    "--no-docker",
                ],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
                timeout=30,
            )
            self.assertEqual(activate.returncode, 0, activate.stdout + activate.stderr)
            self.assertEqual(env_file.read_text(encoding="utf-8"), baseline_state)
            self.assertIn("env/runtime unchanged", activate.stdout + activate.stderr)

            # Construct an active fixture explicitly; the no-docker validation
            # path is intentionally forbidden from persisting latent ingress.
            active_state = baseline_state.replace(
                "OPENSIPS_CANARY_ENABLED=0", "OPENSIPS_CANARY_ENABLED=1"
            ).replace("OPENSIPS_CANARY_PERCENT=0", "OPENSIPS_CANARY_PERCENT=100")
            env_file.write_text(active_state, encoding="utf-8")
            self.assertIn("OPENSIPS_CANARY_ENABLED=1", active_state)
            self.assertIn("OPENSIPS_CANARY_PERCENT=100", active_state)

            rollback = subprocess.run(
                [
                    bash,
                    str(SCRIPTS_DIR / "canary_stage_controller.sh"),
                    "rollback",
                    str(env_file),
                    "--reason",
                    "unit-test rollback",
                    "--operator",
                    "unittest",
                    "--dry-run",
                    "--evidence-dir",
                    temp_dir,
                ],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
                env={**os.environ, "TELEPHONY_PYTHON_BIN": sys.executable},
                timeout=30,
            )
            self.assertEqual(rollback.returncode, 0, rollback.stdout + rollback.stderr)
            self.assertEqual(
                env_file.read_text(encoding="utf-8"),
                active_state,
                "dry-run rollback must not leave a latent durable state change",
            )
            self.assertIn("env/runtime unchanged", rollback.stdout + rollback.stderr)

            disable = subprocess.run(
                [
                    bash,
                    str(SCRIPTS_DIR / "canary_set_stage.sh"),
                    "0",
                    str(env_file),
                    "--no-docker",
                ],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
                timeout=30,
            )
            self.assertEqual(disable.returncode, 0, disable.stdout + disable.stderr)
            rolled_back = env_file.read_text(encoding="utf-8")
            self.assertIn("OPENSIPS_CANARY_ENABLED=0", rolled_back)
            self.assertIn("OPENSIPS_CANARY_PERCENT=0", rolled_back)
            self.assertIn("OPENSIPS_CANARY_FREEZE=1", rolled_back)

            frozen_reenable = subprocess.run(
                [
                    bash,
                    str(SCRIPTS_DIR / "canary_set_stage.sh"),
                    "100",
                    str(env_file),
                    "--no-docker",
                ],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
                timeout=30,
            )
            self.assertNotEqual(frozen_reenable.returncode, 0)
            self.assertIn(
                "Canary is frozen", frozen_reenable.stdout + frozen_reenable.stderr
            )

            forced_reenable = subprocess.run(
                [
                    bash,
                    str(SCRIPTS_DIR / "canary_set_stage.sh"),
                    "100",
                    str(env_file),
                    "--force",
                    "--no-docker",
                ],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
                timeout=30,
            )
            self.assertNotEqual(forced_reenable.returncode, 0)
            self.assertIn(
                "cannot bypass", forced_reenable.stdout + forced_reenable.stderr
            )

            forced_controller = subprocess.run(
                [
                    bash,
                    str(SCRIPTS_DIR / "canary_stage_controller.sh"),
                    "set",
                    "100",
                    str(env_file),
                    "--reason",
                    "unit-test forced frozen activation",
                    "--operator",
                    "unittest",
                    "--force",
                    "--dry-run",
                    "--evidence-dir",
                    temp_dir,
                ],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
                env={**os.environ, "TELEPHONY_PYTHON_BIN": sys.executable},
                timeout=30,
            )
            self.assertNotEqual(forced_controller.returncode, 0)
            self.assertIn(
                "cannot bypass", forced_controller.stdout + forced_controller.stderr
            )

    def test_failed_canary_runtime_apply_leaves_no_latent_activation(self) -> None:
        bash = _find_bash()
        if not bash:
            self.skipTest("No Bash runtime is available for canary failure test")

        env_text = _read_text(ENV_EXAMPLE)
        env_text = env_text.replace(
            "OPENSIPS_CANARY_FREEZE=1", "OPENSIPS_CANARY_FREEZE=0"
        )
        env_text = env_text.replace(
            "OPENSIPS_CANARY_DID=__UNCONFIGURED_DID__",
            "OPENSIPS_CANARY_DID=15551234567",
        )
        env_text = env_text.replace(
            "OPENSIPS_CANARY_AGENT_ID=__UNCONFIGURED_AGENT__",
            "OPENSIPS_CANARY_AGENT_ID=123e4567-e89b-42d3-a456-426614174000",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / "canary.env"
            env_file.write_text(env_text, encoding="utf-8")
            docker_log = Path(temp_dir) / "docker.log"
            fake_docker = Path(temp_dir) / "fake-docker"
            fake_docker.write_text(
                "#!/usr/bin/env bash\n"
                f"printf '%s\\n' \"$*\" >> {docker_log.as_posix()!r}\n"
                'if [[ " $* " == *" up -d opensips "* ]]; then exit 42; fi\n'
                "exit 0\n",
                encoding="utf-8",
            )
            fake_docker.chmod(0o755)

            failed = subprocess.run(
                [
                    bash,
                    str(SCRIPTS_DIR / "canary_set_stage.sh"),
                    "100",
                    env_file.as_posix(),
                ],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
                env={
                    **os.environ,
                    "TELEPHONY_DOCKER_BIN": fake_docker.as_posix(),
                },
                timeout=30,
            )
            self.assertNotEqual(failed.returncode, 0)
            state = env_file.read_text(encoding="utf-8")
            self.assertIn("OPENSIPS_CANARY_ENABLED=0", state)
            self.assertIn("OPENSIPS_CANARY_PERCENT=0", state)
            self.assertIn("OPENSIPS_CANARY_FREEZE=1", state)
            self.assertIn("forcing disabled", failed.stdout + failed.stderr)
            self.assertIn("stop opensips", docker_log.read_text(encoding="utf-8"))

    def test_canary_controller_requires_inbound_evidence_and_dry_run_is_pure(
        self,
    ) -> None:
        bash = _find_bash()
        if not bash:
            self.skipTest("No Bash runtime is available for canary control test")

        env_text = _read_text(ENV_EXAMPLE)
        env_text = env_text.replace(
            "OPENSIPS_CANARY_FREEZE=1", "OPENSIPS_CANARY_FREEZE=0"
        )
        env_text = env_text.replace(
            "OPENSIPS_CANARY_DID=__UNCONFIGURED_DID__",
            "OPENSIPS_CANARY_DID=15551234567",
        )
        env_text = env_text.replace(
            "OPENSIPS_CANARY_AGENT_ID=__UNCONFIGURED_AGENT__",
            "OPENSIPS_CANARY_AGENT_ID=123e4567-e89b-42d3-a456-426614174000",
        )

        scope_tenant_id = "11111111-1111-4111-8111-111111111111"
        scope_config_id = "22222222-2222-4222-8222-222222222222"
        scope_did = "15551234567"
        scope_candidate_digest = "sha256:" + ("a" * 64)
        scope_run_id = "33333333-3333-4333-8333-333333333333"
        scope_started_at = (datetime.now(UTC) - timedelta(seconds=60)).replace(
            microsecond=0
        )
        scope_started_at_text = scope_started_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        scope_started_at_epoch = int(scope_started_at.timestamp())
        expected_scope_hash = hashlib.sha256(
            ":".join(
                (
                    scope_tenant_id,
                    scope_config_id,
                    scope_did,
                    scope_candidate_digest,
                    scope_run_id,
                    scope_started_at_text,
                )
            ).encode()
        ).hexdigest()

        def metrics(
            *,
            setup=30,
            unique_calls=None,
            transfer=0,
            runtime=3,
            rollback=1,
            scope_hash=None,
            baseline=None,
            scrape_timestamp=None,
            latest_call_timestamp=None,
        ) -> str:
            now_epoch = int(datetime.now(UTC).timestamp())
            return "\n".join(
                [
                    "talky_telephony_metrics_scrape_success 1",
                    (
                        "talky_telephony_metrics_scrape_timestamp_seconds "
                        f"{scrape_timestamp if scrape_timestamp is not None else now_epoch}"
                    ),
                    "talky_telephony_canary_scope_valid 1",
                    (
                        "talky_telephony_canary_scope_info"
                        f'{{scope_hash="{scope_hash or expected_scope_hash}"}} 1'
                    ),
                    (
                        "talky_telephony_canary_evidence_baseline_timestamp_seconds "
                        f"{baseline if baseline is not None else scope_started_at_epoch}"
                    ),
                    f"talky_telephony_calls_setup_attempts {setup}",
                    (
                        "talky_telephony_canary_unique_call_ids "
                        f"{setup if unique_calls is None else unique_calls}"
                    ),
                    (
                        "talky_telephony_canary_latest_call_timestamp_seconds "
                        f"{latest_call_timestamp if latest_call_timestamp is not None else now_epoch}"
                    ),
                    "talky_telephony_calls_setup_success_ratio 1",
                    "talky_telephony_calls_answer_latency_p95_seconds 0.5",
                    f"talky_telephony_transfers_attempts {transfer}",
                    "talky_telephony_transfers_success_ratio 1",
                    f"talky_telephony_runtime_activation_attempts {runtime}",
                    "talky_telephony_runtime_activation_success_ratio 1",
                    f"talky_telephony_runtime_rollback_attempts {rollback}",
                    "talky_telephony_runtime_rollback_latency_p95_seconds 1",
                    "",
                ]
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / "canary.env"
            env_file.write_text(env_text, encoding="utf-8")
            original = env_file.read_bytes()
            metrics_file = Path(temp_dir) / "metrics.prom"

            def run(extra_env=None):
                return subprocess.run(
                    [
                        bash,
                        str(SCRIPTS_DIR / "canary_stage_controller.sh"),
                        "set",
                        "100",
                        str(env_file),
                        "--reason",
                        "unit-test evidence gate",
                        "--operator",
                        "unittest",
                        "--dry-run",
                        "--metrics-url",
                        metrics_file.as_uri(),
                        "--evidence-dir",
                        temp_dir,
                    ],
                    capture_output=True,
                    text=True,
                    cwd=REPO_ROOT,
                    env={
                        **os.environ,
                        "TELEPHONY_PYTHON_BIN": sys.executable,
                        "TELEPHONY_CANARY_TENANT_ID": scope_tenant_id,
                        "TELEPHONY_CANARY_CONFIG_ID": scope_config_id,
                        "TELEPHONY_CANARY_DID": scope_did,
                        "TELEPHONY_CANARY_CANDIDATE_DIGEST": scope_candidate_digest,
                        "TELEPHONY_CANARY_RUN_ID": scope_run_id,
                        "TELEPHONY_CANARY_GATE_STARTED_AT": scope_started_at_text,
                        **(extra_env or {}),
                    },
                    timeout=90,
                )

            metrics_file.write_text(metrics(), encoding="utf-8")
            passing = run()
            self.assertEqual(passing.returncode, 0, passing.stdout + passing.stderr)
            self.assertEqual(env_file.read_bytes(), original)
            self.assertIn("simulated", passing.stdout + passing.stderr)

            metrics_file.write_text(metrics(scope_hash="0" * 64), encoding="utf-8")
            mismatched_scope = run()
            self.assertNotEqual(mismatched_scope.returncode, 0)
            self.assertIn(
                "insufficient evidence",
                mismatched_scope.stdout + mismatched_scope.stderr,
            )
            self.assertEqual(env_file.read_bytes(), original)

            metrics_file.write_text(metrics(), encoding="utf-8")
            missing_scope = run({"TELEPHONY_CANARY_CONFIG_ID": ""})
            self.assertNotEqual(missing_scope.returncode, 0)
            self.assertIn(
                "Exact tenant/config/DID, sha256 candidate digest, UUIDv4 run ID",
                missing_scope.stdout + missing_scope.stderr,
            )
            self.assertEqual(env_file.read_bytes(), original)

            stale_run_start = (datetime.now(UTC) - timedelta(hours=7)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            stale_run = run({"TELEPHONY_CANARY_GATE_STARTED_AT": stale_run_start})
            self.assertNotEqual(stale_run.returncode, 0)
            self.assertIn(
                "fresh canonical UTC gate start",
                stale_run.stdout + stale_run.stderr,
            )

            metrics_file.write_text(
                metrics(baseline=scope_started_at_epoch - 1), encoding="utf-8"
            )
            mismatched_baseline = run()
            self.assertNotEqual(mismatched_baseline.returncode, 0)
            self.assertIn(
                "insufficient evidence",
                mismatched_baseline.stdout + mismatched_baseline.stderr,
            )

            stale_timestamp = int(
                (datetime.now(UTC) - timedelta(minutes=20)).timestamp()
            )
            metrics_file.write_text(
                metrics(scrape_timestamp=stale_timestamp), encoding="utf-8"
            )
            stale_scrape = run()
            self.assertNotEqual(stale_scrape.returncode, 0)

            metrics_file.write_text(
                metrics(latest_call_timestamp=stale_timestamp), encoding="utf-8"
            )
            stale_calls = run()
            self.assertNotEqual(stale_calls.returncode, 0)

            metrics_file.write_text(
                metrics(setup=30, unique_calls=29), encoding="utf-8"
            )
            duplicate_counter = run()
            self.assertNotEqual(duplicate_counter.returncode, 0)

            secret = "unit-test-metrics-token-do-not-log"
            metrics_file.write_text(metrics(), encoding="utf-8")
            with_token = run(
                {
                    "TELEPHONY_METRICS_TOKEN": secret,
                    "TMPDIR": Path(temp_dir).as_posix(),
                }
            )
            self.assertEqual(
                with_token.returncode, 0, with_token.stdout + with_token.stderr
            )
            self.assertNotIn(secret, with_token.stdout + with_token.stderr)
            for artifact in Path(temp_dir).iterdir():
                if artifact.is_file():
                    self.assertNotIn(secret.encode(), artifact.read_bytes())
            decisions = [
                json.loads(line)
                for line in (Path(temp_dir) / "ws_l_stage_decisions.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            matching_decision = next(
                row for row in reversed(decisions) if row.get("result") == "simulated"
            )
            self.assertEqual(
                matching_decision["canary_scope_hash"], expected_scope_hash
            )
            self.assertEqual(
                matching_decision["candidate_digest"], scope_candidate_digest
            )
            self.assertEqual(matching_decision["run_id"], scope_run_id)
            self.assertEqual(
                matching_decision["gate_started_at"], scope_started_at_text
            )

            metrics_file.write_text(metrics(setup=0), encoding="utf-8")
            no_setup_evidence = run()
            self.assertNotEqual(no_setup_evidence.returncode, 0)
            self.assertIn(
                "insufficient evidence",
                no_setup_evidence.stdout + no_setup_evidence.stderr,
            )
            self.assertEqual(env_file.read_bytes(), original)

            metrics_file.write_text(metrics(runtime=2), encoding="utf-8")
            no_runtime_evidence = run()
            self.assertNotEqual(no_runtime_evidence.returncode, 0)
            self.assertEqual(env_file.read_bytes(), original)

            metrics_file.write_text(metrics(transfer=9), encoding="utf-8")
            no_transfer_evidence = run(
                {"TELEPHONY_CANARY_GATE_REQUIRE_TRANSFER_EVIDENCE": "1"}
            )
            self.assertNotEqual(no_transfer_evidence.returncode, 0)
            self.assertEqual(env_file.read_bytes(), original)

            bypass = subprocess.run(
                [
                    bash,
                    str(SCRIPTS_DIR / "canary_stage_controller.sh"),
                    "set",
                    "100",
                    str(env_file),
                    "--reason",
                    "attempted gate bypass",
                    "--skip-gates",
                ],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
                env={**os.environ, "TELEPHONY_PYTHON_BIN": sys.executable},
                timeout=30,
            )
            self.assertNotEqual(bypass.returncode, 0)
            self.assertIn("Unknown option", bypass.stdout + bypass.stderr)
            self.assertEqual(env_file.read_bytes(), original)

            token_argv = subprocess.run(
                [
                    bash,
                    str(SCRIPTS_DIR / "canary_stage_controller.sh"),
                    "set",
                    "100",
                    str(env_file),
                    "--reason",
                    "attempted token argv",
                    "--metrics-token",
                    "must-not-be-accepted",
                ],
                capture_output=True,
                text=True,
                cwd=REPO_ROOT,
                env={**os.environ, "TELEPHONY_PYTHON_BIN": sys.executable},
                timeout=30,
            )
            self.assertNotEqual(token_argv.returncode, 0)
            self.assertIn("Unknown option", token_argv.stdout + token_argv.stderr)

    def test_canary_state_lock_rejects_concurrent_activation(self) -> None:
        bash = _find_bash()
        if not bash:
            self.skipTest("No Bash runtime is available for canary lock test")
        has_flock = subprocess.run(
            [bash, "-lc", "command -v flock >/dev/null 2>&1"],
            cwd=REPO_ROOT,
            timeout=30,
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / "canary.env"
            env_file.write_text(_read_text(ENV_EXAMPLE), encoding="utf-8")
            original = env_file.read_bytes()
            env_arg = env_file.as_posix()
            holder = None
            fallback_lock = Path(f"{env_file}.canary.lock.d")
            try:
                if has_flock.returncode == 0:
                    holder = subprocess.Popen(
                        [
                            bash,
                            "-c",
                            'exec 9>"$1.canary.lock"; flock -n 9; echo locked; sleep 30',
                            "canary-lock-holder",
                            env_arg,
                        ],
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        cwd=REPO_ROOT,
                    )
                    self.assertEqual(holder.stdout.readline().strip(), "locked")
                else:
                    fallback_lock.mkdir()
                    (fallback_lock / "owner_pid").write_text(
                        "unit-test\n", encoding="utf-8"
                    )
                blocked = subprocess.run(
                    [
                        bash,
                        str(SCRIPTS_DIR / "canary_set_stage.sh"),
                        "0",
                        env_arg,
                        "--no-docker",
                    ],
                    capture_output=True,
                    text=True,
                    cwd=REPO_ROOT,
                    timeout=30,
                )
                self.assertNotEqual(blocked.returncode, 0)
                self.assertIn(
                    "Another canary state change is in progress",
                    blocked.stdout + blocked.stderr,
                )
                self.assertEqual(env_file.read_bytes(), original)
            finally:
                if holder is not None:
                    holder.terminate()
                    holder.wait(timeout=10)
                elif fallback_lock.exists():
                    (fallback_lock / "owner_pid").unlink(missing_ok=True)
                    fallback_lock.rmdir()

    def test_canary_controller_env_lock_blocks_distinct_run_and_evidence_dir(
        self,
    ) -> None:
        bash = _find_bash()
        if not bash:
            self.skipTest(
                "No Bash runtime is available for canary controller lock test"
            )

        with tempfile.TemporaryDirectory() as temp_dir:
            env_file = Path(temp_dir) / "canary.env"
            env_file.write_text(_read_text(ENV_EXAMPLE), encoding="utf-8")
            original = env_file.read_bytes()
            other_evidence_dir = Path(temp_dir) / "different-run-evidence"
            controller_lock = Path(f"{env_file.resolve()}.controller.lock.d")
            controller_lock.mkdir()
            try:
                blocked = subprocess.run(
                    [
                        bash,
                        str(SCRIPTS_DIR / "canary_stage_controller.sh"),
                        "set",
                        "100",
                        str(env_file),
                        "--reason",
                        "distinct-run contention proof",
                        "--dry-run",
                        "--evidence-dir",
                        str(other_evidence_dir),
                    ],
                    capture_output=True,
                    text=True,
                    cwd=REPO_ROOT,
                    env={
                        **os.environ,
                        "TELEPHONY_PYTHON_BIN": sys.executable,
                        "TELEPHONY_CANARY_RUN_ID": (
                            "44444444-4444-4444-8444-444444444444"
                        ),
                    },
                    timeout=30,
                )
                self.assertNotEqual(blocked.returncode, 0)
                self.assertIn(
                    "Another controller decision is already evaluating this canary env",
                    blocked.stdout + blocked.stderr,
                )
                self.assertEqual(env_file.read_bytes(), original)
                self.assertTrue(controller_lock.is_dir())
                self.assertFalse(other_evidence_dir.exists())
            finally:
                controller_lock.rmdir()

        controller = _read_text(SCRIPTS_DIR / "canary_stage_controller.sh")
        lock_acquire = controller.index('if [[ "$command" != "status" ]]; then')
        state_read = controller.index(
            'current_stage="$(read_kv "OPENSIPS_CANARY_PERCENT" "$env_file")"'
        )
        self.assertLess(lock_acquire, state_read)

    def test_docs_reflect_ws_a_ws_b_ws_c_ws_d_ws_e_progress(self) -> None:
        checklist = _read_text(CHECKLIST_DOC)
        self.assertIn("WS-A, WS-B, WS-C, WS-D, WS-E Complete", checklist)
        self.assertIn("## WS-A: Telephony Infrastructure Bootstrap", checklist)
        self.assertIn("## WS-B: Security and Signaling Baseline", checklist)
        self.assertIn("## WS-C: Call Control and Transfer Baseline", checklist)
        self.assertIn("## WS-D: Media Bridge and Latency Baseline", checklist)
        self.assertIn("## WS-E: Canary and Rollback Control", checklist)
        self.assertIn("Status: `Complete`", checklist)

        plan = _read_text(PLAN_DOC)
        self.assertIn(
            "`WS-A complete`, `WS-B complete`, `WS-C complete`, `WS-D complete`, `WS-E complete`",
            plan,
        )

    def test_ws_k_observability_artifacts_present(self) -> None:
        self.assertTrue(PROM_CONFIG.exists(), "Missing WS-K Prometheus config")
        self.assertTrue(PROM_RULES.exists(), "Missing WS-K Prometheus rules")
        self.assertTrue(
            ALERTMANAGER_CONFIG.exists(), "Missing WS-K Alertmanager config"
        )

        prom = _read_text(PROM_CONFIG)
        self.assertIn("metrics_path: /metrics", prom)
        self.assertIn("telephony_ws_k_rules.yml", prom)
        self.assertIn("alerting:", prom)
        self.assertIn("127.0.0.1:9093", prom)

        rules = _read_text(PROM_RULES)
        self.assertIn("TalkyTelephonyMetricsTargetDown", rules)
        self.assertIn('up{job="talky_backend_telephony"} == 0', rules)
        self.assertIn("TalkyTelephonyCallSetupSLOViolation", rules)
        self.assertIn("TalkyTelephonyRuntimeActivationSuccessLow", rules)
        self.assertIn("job:talky_telephony_calls_setup_success_ratio:avg5m", rules)

        runtime_metrics = _read_text(
            REPO_ROOT / "backend" / "app" / "core" / "telephony_observability.py"
        )
        rule_expressions = "\n".join(
            line
            for line in rules.splitlines()
            if not line.lstrip().startswith("- name:")
        )
        rule_metric_names = set(
            re.findall(r"\b(talky_telephony_[a-z0-9_]+)", rule_expressions)
        )
        missing_exports = sorted(
            name for name in rule_metric_names if f'"{name}"' not in runtime_metrics
        )
        self.assertEqual(
            missing_exports, [], "Alert rule references an unexported metric"
        )

        am = _read_text(ALERTMANAGER_CONFIG)
        self.assertIn('team="telephony"', am)
        self.assertIn("telephony-critical", am)

        checklist = _read_text(PHASE3_CHECKLIST_DOC)
        self.assertIn("## WS-K Gate: SLO Contract and Telemetry Hardening", checklist)

        completion = _read_text(WSK_COMPLETION_DOC)
        self.assertIn("WS-K Completion Record", completion)
        self.assertIn("/metrics", completion)

    def test_ws_m_artifacts_present(self) -> None:
        opensips_cfg = _read_text(CONF_DIR / "opensips.cfg")
        self.assertIn('loadmodule "rtpengine.so"', opensips_cfg)
        self.assertIn("route(WS_M_MANAGE_RTP);", opensips_cfg)
        self.assertIn("onreply_route[WS_M_RTP_REPLY]", opensips_cfg)

        self.assertTrue(
            (
                TELEPHONY_ROOT / "rtpengine" / "conf" / "rtpengine.userspace.conf"
            ).exists()
        )
        self.assertTrue(
            (TELEPHONY_ROOT / "asterisk" / "conf" / "features.conf").exists()
        )
        self.assertTrue(
            (
                TELEPHONY_ROOT
                / "freeswitch"
                / "conf"
                / "autoload_configs"
                / "xml_curl.conf.xml"
            ).exists()
        )

        ext_cfg = _read_text(TELEPHONY_ROOT / "asterisk" / "conf" / "extensions.conf")
        for marker in (
            "[wsm-synthetic]",
            "exten => longcall,1",
            "exten => blind,1",
            "exten => attended,1",
        ):
            self.assertIn(marker, ext_cfg)

        for path in (
            WSM_COMPLETION_DOC,
            WSM_MEDIA_REPORT_DOC,
            WSM_TRANSFER_REPORT_DOC,
            WSM_LONGCALL_REPORT_DOC,
        ):
            self.assertTrue(path.exists(), f"Missing WS-M doc: {path}")

        checklist = _read_text(PHASE3_CHECKLIST_DOC)
        self.assertIn("## WS-M Gate: Media and Transfer Reliability", checklist)
        self.assertIn(
            "[x] RTP path validated for kernel and userspace modes.", checklist
        )
        self.assertIn("[x] Long-call synthetic scenarios pass target.", checklist)
        self.assertIn("[x] Blind transfer synthetic scenarios pass target.", checklist)
        self.assertIn(
            "[x] Attended transfer synthetic scenarios pass target.", checklist
        )
        self.assertIn(
            "[x] `mod_xml_curl` timeout and retry limits validated.", checklist
        )

    def test_ws_n_artifacts_present(self) -> None:
        for path in (
            SCRIPTS_DIR / "verify_ws_n.sh",
            SCRIPTS_DIR / "failure_drill_opensips.sh",
            SCRIPTS_DIR / "failure_drill_rtpengine.sh",
            SCRIPTS_DIR / "failure_drill_freeswitch_backup.sh",
            SCRIPTS_DIR / "failure_drill_combined.sh",
            SCRIPTS_DIR / "ws_n_common.sh",
            WSN_PLAN_DOC,
            WSN_REPORT_DOC,
        ):
            self.assertTrue(path.exists(), f"Missing WS-N artifact: {path}")

        checklist = _read_text(PHASE3_CHECKLIST_DOC)
        self.assertIn(
            "## WS-N Gate: Failure Injection and Automated Recovery", checklist
        )
        self.assertIn("OpenSIPS failure drill completed.", checklist)
        self.assertIn("rtpengine degradation drill completed.", checklist)
        self.assertIn("FreeSWITCH disruption drill completed.", checklist)

    def test_ws_o_artifacts_present(self) -> None:
        for path in (
            SCRIPTS_DIR / "verify_ws_o.sh",
            WSO_PLAN_DOC,
            WSO_REPORT_DOC,
            WSO_DECOM_DOC,
            PHASE3_SIGNOFF_DOC,
        ):
            self.assertTrue(path.exists(), f"Missing WS-O artifact: {path}")

        checklist = _read_text(PHASE3_CHECKLIST_DOC)
        self.assertIn("## WS-O Gate: Production Cutover and Sign-off", checklist)
        self.assertIn("Canary progression completed to 100% traffic.", checklist)
        self.assertIn("Stabilization window completed without SLO breach.", checklist)
        self.assertIn("Legacy path hot-standby readiness confirmed.", checklist)
        self.assertIn("All WS-K through WS-O gates complete.", checklist)

    def test_day4_gateway_artifacts_present(self) -> None:
        evidence_dir = TELEPHONY_ROOT / "docs" / "phase_3" / "evidence" / "day4"
        required_files = (
            DAY4_PLAN_DOC,
            DAY4_EVIDENCE_DOC,
            evidence_dir / "day4_build_output.txt",
            evidence_dir / "day4_rtp_loopback_results.json",
            evidence_dir / "day4_pacing_analysis.txt",
            evidence_dir / "day4_stats_endpoint_sample.json",
            evidence_dir / "day4_log_excerpt.txt",
        )
        for path in required_files:
            self.assertTrue(path.exists(), f"Missing Day 4 artifact: {path}")

        plan = _read_text(DAY4_PLAN_DOC)
        self.assertIn("Acceptance Gate Definition (Day 4 -> Day 5 unlock)", plan)
        self.assertIn("RTP loopback", plan)

        evidence = _read_text(DAY4_EVIDENCE_DOC)
        self.assertIn("Day 4 C++ Gateway Evidence", evidence)
        self.assertIn("Open Issues", evidence)

    def test_day5_artifacts_present(self) -> None:
        self.assertTrue(DAY5_PLAN_DOC.exists(), "Missing Day 5 execution plan doc")
        self.assertTrue(
            (TELEPHONY_ROOT / "asterisk" / "conf" / "http.conf").exists(),
            "Missing Asterisk http.conf",
        )
        self.assertTrue(
            (TELEPHONY_ROOT / "asterisk" / "conf" / "ari.conf").exists(),
            "Missing Asterisk ari.conf",
        )

        ext_cfg = _read_text(TELEPHONY_ROOT / "asterisk" / "conf" / "extensions.conf")
        self.assertIn(
            "exten => _X!,1,NoOp(Validate admitted inbound canary call to ${EXTEN})",
            ext_cfg,
        )
        self.assertIn(
            "Stasis(talky_ai,inbound,${TALKY_ORIGINAL_DID},${CONTEXT},${TALKY_AGENT_ID})",
            ext_cfg,
        )
        inbound_dialplan = ext_cfg.split("[from-opensips]", 1)[1].split(
            "[ai-outbound]", 1
        )[0]
        inbound_code = "\n".join(
            line
            for line in inbound_dialplan.splitlines()
            if not line.lstrip().startswith(";")
        )
        self.assertNotIn("Answer(", inbound_code)
        self.assertNotIn("Set(TALKY_ORIGINAL_DID=${EXTEN})", inbound_dialplan)
        self.assertIn("X-Talky-Ingress-Policy", inbound_dialplan)
        self.assertIn("X-Talky-Agent-ID", inbound_dialplan)
        self.assertIn("Hangup(21)", inbound_dialplan)

        http_cfg = _read_text(TELEPHONY_ROOT / "asterisk" / "conf" / "http.conf")
        self.assertIn("enabled = yes", http_cfg)
        self.assertIn("bindaddr = 127.0.0.1", http_cfg)
        self.assertIn("bindport = 8088", http_cfg)

        ari_cfg = _read_text(TELEPHONY_ROOT / "asterisk" / "conf" / "ari.conf")
        self.assertIn("[day5]", ari_cfg)
        self.assertIn("read_only = no", ari_cfg)

        compose = _read_text(
            TELEPHONY_ROOT / "deploy" / "docker" / "docker-compose.telephony.yml"
        )
        self.assertIn("/etc/asterisk/http.conf", compose)
        self.assertIn("/etc/asterisk/ari.conf", compose)


class TelephonyIntegrationTests(unittest.TestCase):
    RUN_INTEGRATION = os.getenv("TELEPHONY_RUN_DOCKER_TESTS") == "1"

    def _run_script(self, script_name: str, timeout_seconds: int = 420) -> str:
        script = SCRIPTS_DIR / script_name
        proc = subprocess.run(
            ["bash", str(script), str(ENV_EXAMPLE)],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            timeout=timeout_seconds,
        )
        output = (proc.stdout or "") + "\n" + (proc.stderr or "")
        self.assertEqual(
            proc.returncode,
            0,
            f"{script_name} failed with exit={proc.returncode}\nOutput tail:\n{_tail(output)}",
        )
        return output

    @unittest.skipUnless(
        RUN_INTEGRATION,
        "Set TELEPHONY_RUN_DOCKER_TESTS=1 to run docker integration checks",
    )
    def test_ws_a_verifier_passes(self) -> None:
        output = self._run_script("verify_ws_a.sh")
        self.assertIn("WS-A verification PASSED.", output)

    @unittest.skipUnless(
        RUN_INTEGRATION,
        "Set TELEPHONY_RUN_DOCKER_TESTS=1 to run docker integration checks",
    )
    def test_ws_b_verifier_passes(self) -> None:
        output = self._run_script("verify_ws_b.sh")
        self.assertIn("WS-B verification PASSED.", output)

    @unittest.skipUnless(
        RUN_INTEGRATION,
        "Set TELEPHONY_RUN_DOCKER_TESTS=1 to run docker integration checks",
    )
    def test_ws_c_verifier_passes(self) -> None:
        output = self._run_script("verify_ws_c.sh")
        self.assertIn("WS-C verification PASSED.", output)

    @unittest.skipUnless(
        RUN_INTEGRATION,
        "Set TELEPHONY_RUN_DOCKER_TESTS=1 to run docker integration checks",
    )
    def test_ws_d_verifier_passes(self) -> None:
        output = self._run_script("verify_ws_d.sh")
        self.assertIn("WS-D verification PASSED.", output)

    @unittest.skipUnless(
        RUN_INTEGRATION,
        "Set TELEPHONY_RUN_DOCKER_TESTS=1 to run docker integration checks",
    )
    def test_ws_e_verifier_passes(self) -> None:
        output = self._run_script("verify_ws_e.sh")
        self.assertIn("WS-E verification PASSED.", output)

    @unittest.skipUnless(
        RUN_INTEGRATION,
        "Set TELEPHONY_RUN_DOCKER_TESTS=1 to run docker integration checks",
    )
    def test_ws_i_verifier_passes(self) -> None:
        output = self._run_script("verify_ws_i.sh")
        self.assertIn("WS-I verification PASSED.", output)

    @unittest.skipUnless(
        RUN_INTEGRATION,
        "Set TELEPHONY_RUN_DOCKER_TESTS=1 to run docker integration checks",
    )
    def test_ws_j_verifier_passes(self) -> None:
        output = self._run_script("verify_ws_j.sh")
        self.assertIn("WS-J verification PASSED.", output)

    @unittest.skipUnless(
        RUN_INTEGRATION,
        "Set TELEPHONY_RUN_DOCKER_TESTS=1 to run docker integration checks",
    )
    def test_ws_k_verifier_passes(self) -> None:
        output = self._run_script("verify_ws_k.sh")
        self.assertIn("WS-K verification PASSED.", output)

    @unittest.skipUnless(
        RUN_INTEGRATION,
        "Set TELEPHONY_RUN_DOCKER_TESTS=1 to run docker integration checks",
    )
    def test_ws_l_verifier_passes(self) -> None:
        output = self._run_script("verify_ws_l.sh")
        self.assertIn("WS-L verification PASSED.", output)

    @unittest.skipUnless(
        RUN_INTEGRATION,
        "Set TELEPHONY_RUN_DOCKER_TESTS=1 to run docker integration checks",
    )
    def test_ws_m_verifier_passes(self) -> None:
        output = self._run_script("verify_ws_m.sh")
        self.assertIn("WS-M verification PASSED.", output)

    @unittest.skipUnless(
        RUN_INTEGRATION,
        "Set TELEPHONY_RUN_DOCKER_TESTS=1 to run docker integration checks",
    )
    def test_ws_n_verifier_passes(self) -> None:
        output = self._run_script("verify_ws_n.sh", timeout_seconds=900)
        self.assertIn("WS-N verification PASSED.", output)

    @unittest.skipUnless(
        RUN_INTEGRATION,
        "Set TELEPHONY_RUN_DOCKER_TESTS=1 to run docker integration checks",
    )
    def test_ws_o_verifier_passes(self) -> None:
        output = self._run_script("verify_ws_o.sh", timeout_seconds=1200)
        self.assertIn("WS-O verification PASSED.", output)

    @unittest.skipUnless(
        RUN_INTEGRATION,
        "Set TELEPHONY_RUN_DOCKER_TESTS=1 to run docker integration checks",
    )
    def test_day4_verifier_passes(self) -> None:
        output = self._run_script("verify_day4_cpp_gateway.sh", timeout_seconds=600)
        self.assertIn("WS-DAY4 verification PASSED.", output)

    @unittest.skipUnless(
        RUN_INTEGRATION,
        "Set TELEPHONY_RUN_DOCKER_TESTS=1 to run docker integration checks",
    )
    def test_day5_verifier_passes(self) -> None:
        output = self._run_script(
            "verify_day5_asterisk_cpp_echo.sh", timeout_seconds=1200
        )
        self.assertIn("Day 5 verification PASSED.", output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
