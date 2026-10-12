"""Bughunt telephony-control-6: ';' in credentials must survive Asterisk's parser.

Asterisk treats an unescaped ';' as a comment start, so ``password=pa;ss`` is
read as ``pa`` and the carrier rejects REGISTER forever. The renderer must
write a backslash before each ';'. The helper below applies Asterisk's rule
(drop an unescaped ';' comment, unescape backslash-semicolon) to prove the
value round-trips.
"""
import re

import pytest

from app.infrastructure.telephony.pjsip_config_generator import TrunkConfigInput, render_trunk_conf

BS = "\\"


def _input(**over):
    base = dict(
        trunk_id="t1",
        tenant_id="ten1",
        trunk_name="acme",
        sip_domain="sip.acme.example",
        port=5060,
        transport="udp",
        auth_username="acmeuser",
        auth_password="s3cr3t",
        register=True,
    )
    base.update(over)
    return TrunkConfigInput(**base)


def _asterisk_value(conf: str, key: str) -> str:
    """Value of the first ``key=`` line as Asterisk's config parser reads it."""
    for line in conf.splitlines():
        if line.startswith(f"{key}="):
            raw = line[len(key) + 1:]
            comment = re.search(r"(?<!\\);", raw)
            if comment:
                raw = raw[: comment.start()]
            return raw.replace(BS + ";", ";").strip()
    raise AssertionError(f"{key} not rendered")


@pytest.mark.parametrize(
    "password",
    ["a;b", "pa;ss", "x;--y", ";lead", "trail;", "a;;b", "plain-pass"],
)
def test_password_with_semicolon_round_trips(password):
    conf = render_trunk_conf(_input(auth_password=password))
    assert _asterisk_value(conf, "password") == password


def test_password_semicolon_is_written_escaped():
    assert "password=a" + BS + ";b\n" in render_trunk_conf(_input(auth_password="a;b"))


def test_username_and_realm_semicolons_are_escaped():
    conf = render_trunk_conf(_input(auth_username="us;er", auth_realm="re;alm"))
    assert _asterisk_value(conf, "username") == "us;er"
    assert _asterisk_value(conf, "realm") == "re;alm"


def test_already_escaped_semicolon_is_not_double_escaped():
    conf = render_trunk_conf(_input(auth_password="a" + BS + ";b"))
    assert "password=a" + BS + ";b\n" in conf
    assert "password=a" + BS + BS + ";b" not in conf


def test_newline_guard_still_applies():
    with pytest.raises(ValueError):
        render_trunk_conf(_input(auth_password="a\nb"))
