import os
import subprocess
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TELEPHONY_ROOT = REPO_ROOT / "telephony"
SCRIPTS_DIR = TELEPHONY_ROOT / "scripts"
CONF_DIR = TELEPHONY_ROOT / "kamailio" / "conf"
FS_CONF = TELEPHONY_ROOT / "freeswitch" / "conf" / "autoload_configs" / "event_socket.conf.xml"
ENV_EXAMPLE = TELEPHONY_ROOT / "deploy" / "docker" / ".env.telephony.example"
CHECKLIST_DOC = TELEPHONY_ROOT / "docs" / "07_phase_one_gated_checklist.md"
PLAN_DOC = TELEPHONY_ROOT / "docs" / "plan.md"


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
            SCRIPTS_DIR / "canary_set_stage.sh",
            SCRIPTS_DIR / "canary_freeze.sh",
            SCRIPTS_DIR / "canary_rollback.sh",
            SCRIPTS_DIR / "generate_kamailio_tls_certs.sh",
            SCRIPTS_DIR / "sip_options_probe.py",
            SCRIPTS_DIR / "sip_options_probe_tls.sh",
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
            "canary_set_stage.sh",
            "canary_freeze.sh",
            "canary_rollback.sh",
            "generate_kamailio_tls_certs.sh",
            "sip_options_probe_tls.sh",
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

    def test_kamailio_ws_b_security_modules_present(self) -> None:
        cfg = _read_text(CONF_DIR / "kamailio.cfg")
        required_markers = [
            'loadmodule "tls.so"',
            'loadmodule "permissions.so"',
            'loadmodule "pike.so"',
            'loadmodule "ratelimit.so"',
            'modparam("tls", "config", "/etc/kamailio/tls.cfg")',
            'modparam("permissions", "address_file", "/etc/kamailio/address.list")',
            'allow_source_address("1")',
            "pike_check_req()",
            "rl_check()",
            "listen = tls:KAMAILIO_SIP_IP:KAMAILIO_TLS_PORT",
        ]
        for marker in required_markers:
            self.assertIn(marker, cfg, f"Missing marker in kamailio.cfg: {marker}")

    def test_kamailio_acl_and_tls_files_exist(self) -> None:
        self.assertTrue((CONF_DIR / "address.list").exists(), "Missing address.list")
        self.assertTrue((CONF_DIR / "tls.cfg").exists(), "Missing tls.cfg")
        cert_gitignore = TELEPHONY_ROOT / "kamailio" / "certs" / ".gitignore"
        cert_gitkeep = TELEPHONY_ROOT / "kamailio" / "certs" / ".gitkeep"
        self.assertTrue(cert_gitignore.exists(), "Missing certs .gitignore")
        self.assertTrue(cert_gitkeep.exists(), "Missing certs .gitkeep")

    def test_freeswitch_esl_hardening(self) -> None:
        cfg = _read_text(FS_CONF)
        self.assertIn('listen-ip" value="127.0.0.1"', cfg)
        self.assertIn('apply-inbound-acl" value="loopback.auto"', cfg)

    def test_kamailio_ws_e_canary_markers_present(self) -> None:
        cfg = _read_text(CONF_DIR / "kamailio.cfg")
        required_markers = [
            'loadmodule "cfgutils.so"',
            'loadmodule "ctl.so"',
            'modparam("cfgutils", "initial_probability", 0)',
            'modparam("ctl", "binrpc", "unix:/var/run/kamailio/kamailio_ctl")',
            "KAMAILIO_CANARY_ENABLED",
            "KAMAILIO_CANARY_PERCENT",
            'rand_set_prob("KAMAILIO_CANARY_PERCENT")',
            "rand_event()",
            'ds_select_dst("2", "4")',
        ]
        for marker in required_markers:
            self.assertIn(marker, cfg, f"Missing WS-E marker in kamailio.cfg: {marker}")

        dispatcher = _read_text(CONF_DIR / "dispatcher.list")
        self.assertIn("\n2 sip:", dispatcher, "Missing canary dispatcher set (set 2)")

    def test_env_example_has_ws_b_keys(self) -> None:
        env = _read_text(ENV_EXAMPLE)
        required = [
            "KAMAILIO_SIP_PORT=15060",
            "KAMAILIO_TLS_PORT=15061",
            "KAMAILIO_TLS_ONLY=0",
            "KAMAILIO_CANARY_ENABLED=0",
            "KAMAILIO_CANARY_PERCENT=0",
            "KAMAILIO_CANARY_FREEZE=0",
            "FREESWITCH_ESL_PORT=8021",
        ]
        for marker in required:
            self.assertIn(marker, env, f"Missing env key/value: {marker}")

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
