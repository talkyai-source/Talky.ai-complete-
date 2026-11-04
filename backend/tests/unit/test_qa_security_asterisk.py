"""Installer output contracts; packet-level evidence lives in the QA report."""
import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "setup-asterisk.sh"


def test_installed_pjsip_never_identifies_carriers_by_username():
    source = SCRIPT.read_text(encoding="utf-8")
    config = re.search(r"<<PJSIPEOF\n(.*?)\nPJSIPEOF", source, re.S).group(1)
    assert "[global]\ntype=global\nendpoint_identifier_order=ip\n" in config
    assert "user_agent=Talk-Lee" in config
    assert "identify_by=ip" in config
    assert "match=$TRUNK_HOST" in config
    assert "#include pjsip.d/*.conf" in config


def test_unused_channel_drivers_are_disabled_before_restart():
    source = SCRIPT.read_text(encoding="utf-8")
    assert "noload => chan_iax2.so" in source
    assert "noload => chan_sip.so" in source
    # endpoint_identifier_order is priority only, not an allow-list.
    assert "noload => res_pjsip_endpoint_identifier_user.so" in source
    assert "noload => res_pjsip_endpoint_identifier_anonymous.so" in source
    assert source.index("noload => chan_iax2.so") < source.index("systemctl restart asterisk")
