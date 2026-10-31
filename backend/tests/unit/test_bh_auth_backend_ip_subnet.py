"""auth-sessions-12: IPv6 privacy-address rotation within a /64, and a v4/v6
family switch on a dual-stack client, must not be reported as an IP change."""
import pytest

from app.core.security.device_fingerprint import (
    generate_ip_subnet,
    is_ip_change_significant,
)


def test_ipv6_rotation_inside_one_slash_64_is_not_significant():
    r = is_ip_change_significant("2001:db8:abcd:12::1", "2001:db8:abcd:12:aaaa:bbbb:cccc:dddd")
    assert r == {"significant": False, "reason": "same_subnet"}


def test_ipv6_change_to_another_slash_64_is_significant():
    r = is_ip_change_significant("2001:db8:abcd:12::1", "2001:db8:abcd:13::1")
    assert r["significant"] is True and r["reason"] == "different_subnet"


def test_dual_stack_family_switch_alone_is_not_significant():
    for a, b in (("203.0.113.9", "2001:db8::5"), ("2001:db8::5", "203.0.113.9")):
        r = is_ip_change_significant(a, b)
        assert r["significant"] is False, (a, b, r)


def test_ipv4_mapped_ipv6_compares_as_the_ipv4_address():
    assert is_ip_change_significant("203.0.113.9", "::ffff:203.0.113.77")["significant"] is False
    assert is_ip_change_significant("203.0.113.9", "::ffff:198.51.100.7")["significant"] is True


def test_strict_mode_still_flags_every_change():
    r = is_ip_change_significant("2001:db8::1", "2001:db8::2", strict_mode=True)
    assert r == {"significant": True, "reason": "strict_mode_ip_change"}


def test_unknown_and_missing_original_are_unchanged():
    assert is_ip_change_significant("unknown", "2001:db8::1")["reason"] == "no_original_ip"
    assert is_ip_change_significant(None, "2001:db8::1")["reason"] == "no_original_ip"


def test_garbage_current_ip_is_still_flagged():
    assert is_ip_change_significant("203.0.113.9", "not-an-ip")["significant"] is True


@pytest.mark.parametrize("ip,expected", [
    ("192.168.1.100", "192.168.1.0/24"),
    ("2001:db8:abcd:12:1:2:3:4", "2001:db8:abcd:12::/64"),
    ("fe80::1%eth0", "fe80::/64"),
    ("::ffff:203.0.113.9", "203.0.113.0/24"),
    ("999.1.1.1", None), ("192.168.1", None), ("not-an-ip", None), ("unknown", None), ("", None),
])
def test_generate_ip_subnet(ip, expected):
    assert generate_ip_subnet(ip) == expected
