import os
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TELEPHONY_ROOT = REPO_ROOT / "telephony"
SCRIPTS_DIR = TELEPHONY_ROOT / "scripts"
CONF_DIR = TELEPHONY_ROOT / "opensips" / "conf"
FS_CONF = TELEPHONY_ROOT / "freeswitch" / "conf" / "autoload_configs" / "event_socket.conf.xml"
ASTERISK_PJSIP_CONF = TELEPHONY_ROOT / "asterisk" / "conf" / "pjsip.conf"
ASTERISK_MODULES_CONF = TELEPHONY_ROOT / "asterisk" / "conf" / "modules.conf"
ENV_EXAMPLE = TELEPHONY_ROOT / "deploy" / "docker" / ".env.telephony.example"
CHECKLIST_DOC = TELEPHONY_ROOT / "docs" / "phase_1" / "07_phase_one_gated_checklist.md"
PLAN_DOC = TELEPHONY_ROOT / "docs" / "phase_1" / "plan.md"
PHASE3_CHECKLIST_DOC = TELEPHONY_ROOT / "docs" / "phase_3" / "02_phase_three_gated_checklist.md"
DAY4_PLAN_DOC = TELEPHONY_ROOT / "docs" / "phase_3" / "22_day4_cpp_gateway_execution_plan.md"
DAY4_EVIDENCE_DOC = TELEPHONY_ROOT / "docs" / "phase_3" / "day4_cpp_gateway_evidence.md"
DAY5_PLAN_DOC = TELEPHONY_ROOT / "docs" / "phase_3" / "23_day5_asterisk_cpp_e2e_echo_execution_plan.md"
DAY5_EVIDENCE_DOC = TELEPHONY_ROOT / "docs" / "phase_3" / "day5_asterisk_cpp_echo_evidence.md"
WSK_COMPLETION_DOC = TELEPHONY_ROOT / "docs" / "phase_3" / "03_ws_k_completion.md"
WSM_COMPLETION_DOC = TELEPHONY_ROOT / "docs" / "phase_3" / "11_ws_m_completion.md"
WSM_MEDIA_REPORT_DOC = TELEPHONY_ROOT / "docs" / "phase_3" / "08_ws_m_media_quality_report.md"
WSM_TRANSFER_REPORT_DOC = TELEPHONY_ROOT / "docs" / "phase_3" / "09_ws_m_transfer_success_report.md"
WSM_LONGCALL_REPORT_DOC = TELEPHONY_ROOT / "docs" / "phase_3" / "10_ws_m_long_call_session_timer_report.md"
WSN_PLAN_DOC = TELEPHONY_ROOT / "docs" / "phase_3" / "12_ws_n_failure_injection_recovery_plan.md"
WSN_REPORT_DOC = TELEPHONY_ROOT / "docs" / "phase_3" / "13_ws_n_failure_recovery_report.md"
WSO_PLAN_DOC = TELEPHONY_ROOT / "docs" / "phase_3" / "15_ws_o_production_cutover_plan.md"
WSO_REPORT_DOC = TELEPHONY_ROOT / "docs" / "phase_3" / "16_ws_o_cutover_report.md"
WSO_DECOM_DOC = TELEPHONY_ROOT / "docs" / "phase_3" / "17_ws_o_decommission_readiness_checklist.md"
PHASE3_SIGNOFF_DOC = TELEPHONY_ROOT / "docs" / "phase_3" / "18_phase_three_signoff.md"
PROM_CONFIG = TELEPHONY_ROOT / "observability" / "prometheus" / "prometheus.yml"
PROM_RULES = TELEPHONY_ROOT / "observability" / "prometheus" / "rules" / "telephony_ws_k_rules.yml"
ALERTMANAGER_CONFIG = TELEPHONY_ROOT / "observability" / "alertmanager" / "alertmanager.yml"


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
            self.assertTrue(os.access(script, os.X_OK), f"Script is not executable: {script}")

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
            "day10_restart_recovery_drill.sh",
        ]
        for name in scripts:
            script = SCRIPTS_DIR / name
            proc = subprocess.run(
                ["bash", "-n", str(script)],
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
            '$si != "127.0.0.1"',
            "pike_check_req()",
            'rl_check("invite", 60, "TAILDROP")',
            "socket = tls:0.0.0.0:15061",
            'ds_select_dst(1, 4)',
        ]
        for marker in required_markers:
            self.assertIn(marker, cfg, f"Missing marker in opensips.cfg: {marker}")

    def test_opensips_acl_and_tls_files_exist(self) -> None:
        self.assertTrue((CONF_DIR / "address.list").exists(), "Missing address.list")
        self.assertTrue((CONF_DIR / "tls.cfg").exists(), "Missing tls.cfg")
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
        self.assertIn("type=identify", pjsip_cfg)
        self.assertIn("match=127.0.0.1", pjsip_cfg)
        self.assertIn("outbound_proxy=sip:127.0.0.1:15060\\;lr", pjsip_cfg)
        self.assertIn("noload => chan_sip.so", modules_cfg)

    def test_freeswitch_backup_config_retained(self) -> None:
        cfg = _read_text(FS_CONF)
        self.assertIn('listen-ip" value="127.0.0.1"', cfg)
        self.assertIn('apply-inbound-acl" value="loopback.auto"', cfg)
        self.assertTrue((TELEPHONY_ROOT / "freeswitch" / "README.md").exists())

    def test_opensips_ws_e_canary_markers_present(self) -> None:
        cfg = _read_text(CONF_DIR / "opensips.cfg")
        required_markers = [
            'loadmodule "cfgutils.so"',
            'loadmodule "mi_fifo.so"',
            'modparam("cfgutils", "initial_probability", 0)',
            'modparam("mi_fifo", "fifo_name", "/tmp/opensips_fifo")',
            "OPENSIPS_CANARY_ENABLED",
            "OPENSIPS_CANARY_PERCENT",
            'rand_set_prob("$def(OPENSIPS_CANARY_PERCENT)")',
            'ds_select_dst(2, 4)',
        ]
        for marker in required_markers:
            self.assertIn(marker, cfg, f"Missing WS-E marker in opensips.cfg: {marker}")

        dispatcher = _read_text(CONF_DIR / "dispatcher.list")
        self.assertIn("\n2 sip:", dispatcher, "Missing canary dispatcher set (set 2)")

    def test_env_example_has_ws_b_keys(self) -> None:
        env = _read_text(ENV_EXAMPLE)
        required = [
            "OPENSIPS_SIP_PORT=15060",
            "OPENSIPS_TLS_PORT=15061",
            "OPENSIPS_TLS_ONLY=0",
            "OPENSIPS_CANARY_ENABLED=0",
            "OPENSIPS_CANARY_PERCENT=0",
            "OPENSIPS_CANARY_FREEZE=0",
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
        self.assertIn("FREESWITCH_ESL_PASSWORD=", env, "Missing FREESWITCH_ESL_PASSWORD key")
        self.assertIn("ASTERISK_ARI_PASSWORD=", env, "Missing ASTERISK_ARI_PASSWORD key")

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
        self.assertIn("`WS-A complete`, `WS-B complete`, `WS-C complete`, `WS-D complete`, `WS-E complete`", plan)

    def test_ws_k_observability_artifacts_present(self) -> None:
        self.assertTrue(PROM_CONFIG.exists(), "Missing WS-K Prometheus config")
        self.assertTrue(PROM_RULES.exists(), "Missing WS-K Prometheus rules")
        self.assertTrue(ALERTMANAGER_CONFIG.exists(), "Missing WS-K Alertmanager config")

        prom = _read_text(PROM_CONFIG)
        self.assertIn("metrics_path: /metrics", prom)
        self.assertIn("telephony_ws_k_rules.yml", prom)

        rules = _read_text(PROM_RULES)
        self.assertIn("TalkyTelephonyCallSetupSLOViolation", rules)
        self.assertIn("TalkyTelephonyRuntimeActivationSuccessLow", rules)
        self.assertIn("job:talky_telephony_calls_setup_success_ratio:avg5m", rules)

        am = _read_text(ALERTMANAGER_CONFIG)
        self.assertIn("team=\"telephony\"", am)
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

        self.assertTrue((TELEPHONY_ROOT / "rtpengine" / "conf" / "rtpengine.userspace.conf").exists())
        self.assertTrue((TELEPHONY_ROOT / "asterisk" / "conf" / "features.conf").exists())
        self.assertTrue((TELEPHONY_ROOT / "freeswitch" / "conf" / "autoload_configs" / "xml_curl.conf.xml").exists())

        ext_cfg = _read_text(TELEPHONY_ROOT / "asterisk" / "conf" / "extensions.conf")
        for marker in ("[wsm-synthetic]", "exten => longcall,1", "exten => blind,1", "exten => attended,1"):
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
        self.assertIn("[x] RTP path validated for kernel and userspace modes.", checklist)
        self.assertIn("[x] Long-call synthetic scenarios pass target.", checklist)
        self.assertIn("[x] Blind transfer synthetic scenarios pass target.", checklist)
        self.assertIn("[x] Attended transfer synthetic scenarios pass target.", checklist)
        self.assertIn("[x] `mod_xml_curl` timeout and retry limits validated.", checklist)

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
        self.assertIn("## WS-N Gate: Failure Injection and Automated Recovery", checklist)
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
        self.assertTrue((TELEPHONY_ROOT / "asterisk" / "conf" / "http.conf").exists(), "Missing Asterisk http.conf")
        self.assertTrue((TELEPHONY_ROOT / "asterisk" / "conf" / "ari.conf").exists(), "Missing Asterisk ari.conf")

        ext_cfg = _read_text(TELEPHONY_ROOT / "asterisk" / "conf" / "extensions.conf")
        self.assertIn("exten => 750,1,NoOp(Day 5 ARI external media test call)", ext_cfg)
        self.assertIn("Stasis(talky_day5,inbound)", ext_cfg)

        http_cfg = _read_text(TELEPHONY_ROOT / "asterisk" / "conf" / "http.conf")
        self.assertIn("enabled = yes", http_cfg)
        self.assertIn("bindaddr = 127.0.0.1", http_cfg)
        self.assertIn("bindport = 8088", http_cfg)

        ari_cfg = _read_text(TELEPHONY_ROOT / "asterisk" / "conf" / "ari.conf")
        self.assertIn("[day5]", ari_cfg)
        self.assertIn("read_only = no", ari_cfg)

        compose = _read_text(TELEPHONY_ROOT / "deploy" / "docker" / "docker-compose.telephony.yml")
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

    @unittest.skipUnless(RUN_INTEGRATION, "Set TELEPHONY_RUN_DOCKER_TESTS=1 to run docker integration checks")
    def test_ws_a_verifier_passes(self) -> None:
        output = self._run_script("verify_ws_a.sh")
        self.assertIn("WS-A verification PASSED.", output)

    @unittest.skipUnless(RUN_INTEGRATION, "Set TELEPHONY_RUN_DOCKER_TESTS=1 to run docker integration checks")
    def test_ws_b_verifier_passes(self) -> None:
        output = self._run_script("verify_ws_b.sh")
        self.assertIn("WS-B verification PASSED.", output)

    @unittest.skipUnless(RUN_INTEGRATION, "Set TELEPHONY_RUN_DOCKER_TESTS=1 to run docker integration checks")
    def test_ws_c_verifier_passes(self) -> None:
        output = self._run_script("verify_ws_c.sh")
        self.assertIn("WS-C verification PASSED.", output)

    @unittest.skipUnless(RUN_INTEGRATION, "Set TELEPHONY_RUN_DOCKER_TESTS=1 to run docker integration checks")
    def test_ws_d_verifier_passes(self) -> None:
        output = self._run_script("verify_ws_d.sh")
        self.assertIn("WS-D verification PASSED.", output)

    @unittest.skipUnless(RUN_INTEGRATION, "Set TELEPHONY_RUN_DOCKER_TESTS=1 to run docker integration checks")
    def test_ws_e_verifier_passes(self) -> None:
        output = self._run_script("verify_ws_e.sh")
        self.assertIn("WS-E verification PASSED.", output)

    @unittest.skipUnless(RUN_INTEGRATION, "Set TELEPHONY_RUN_DOCKER_TESTS=1 to run docker integration checks")
    def test_ws_i_verifier_passes(self) -> None:
        output = self._run_script("verify_ws_i.sh")
        self.assertIn("WS-I verification PASSED.", output)

    @unittest.skipUnless(RUN_INTEGRATION, "Set TELEPHONY_RUN_DOCKER_TESTS=1 to run docker integration checks")
    def test_ws_j_verifier_passes(self) -> None:
        output = self._run_script("verify_ws_j.sh")
        self.assertIn("WS-J verification PASSED.", output)

    @unittest.skipUnless(RUN_INTEGRATION, "Set TELEPHONY_RUN_DOCKER_TESTS=1 to run docker integration checks")
    def test_ws_k_verifier_passes(self) -> None:
        output = self._run_script("verify_ws_k.sh")
        self.assertIn("WS-K verification PASSED.", output)

    @unittest.skipUnless(RUN_INTEGRATION, "Set TELEPHONY_RUN_DOCKER_TESTS=1 to run docker integration checks")
    def test_ws_l_verifier_passes(self) -> None:
        output = self._run_script("verify_ws_l.sh")
        self.assertIn("WS-L verification PASSED.", output)

    @unittest.skipUnless(RUN_INTEGRATION, "Set TELEPHONY_RUN_DOCKER_TESTS=1 to run docker integration checks")
    def test_ws_m_verifier_passes(self) -> None:
        output = self._run_script("verify_ws_m.sh")
        self.assertIn("WS-M verification PASSED.", output)

    @unittest.skipUnless(RUN_INTEGRATION, "Set TELEPHONY_RUN_DOCKER_TESTS=1 to run docker integration checks")
    def test_ws_n_verifier_passes(self) -> None:
        output = self._run_script("verify_ws_n.sh", timeout_seconds=900)
        self.assertIn("WS-N verification PASSED.", output)

    @unittest.skipUnless(RUN_INTEGRATION, "Set TELEPHONY_RUN_DOCKER_TESTS=1 to run docker integration checks")
    def test_ws_o_verifier_passes(self) -> None:
        output = self._run_script("verify_ws_o.sh", timeout_seconds=1200)
        self.assertIn("WS-O verification PASSED.", output)

    @unittest.skipUnless(RUN_INTEGRATION, "Set TELEPHONY_RUN_DOCKER_TESTS=1 to run docker integration checks")
    def test_day4_verifier_passes(self) -> None:
        output = self._run_script("verify_day4_cpp_gateway.sh", timeout_seconds=600)
        self.assertIn("WS-DAY4 verification PASSED.", output)

    @unittest.skipUnless(RUN_INTEGRATION, "Set TELEPHONY_RUN_DOCKER_TESTS=1 to run docker integration checks")
    def test_day5_verifier_passes(self) -> None:
        output = self._run_script("verify_day5_asterisk_cpp_echo.sh", timeout_seconds=1200)
        self.assertIn("Day 5 verification PASSED.", output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
