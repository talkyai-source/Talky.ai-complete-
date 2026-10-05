"""Actual pyotp skew-window/replay controls; synthetic secret, deterministic clock."""
from datetime import datetime, timezone

import pyotp
import pytest

from app.core.security import totp


@pytest.fixture
def clock(monkeypatch):
    class Clock(datetime):
        current = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(cls.current.timestamp(), tz)

    monkeypatch.setattr(totp, "datetime", Clock)
    monkeypatch.setattr(pyotp.totp.datetime, "datetime", Clock)
    return Clock


def test_previous_window_cannot_replay_after_clock_advances(clock):
    secret = pyotp.random_base32()
    accepted = datetime.fromtimestamp(clock.current.timestamp() - 30, timezone.utc)
    code = pyotp.TOTP(secret).at(accepted.timestamp())
    accepted_again = totp.verify_totp_code(secret, code, last_used_at=accepted)
    assert accepted_again is False


@pytest.mark.parametrize("offset", [-1, 0, 1])
def test_each_existing_skew_window_records_exact_accepted_step(clock, offset):
    secret = pyotp.random_base32()
    accepted = datetime.fromtimestamp(clock.current.timestamp() + offset * 30, timezone.utc)
    code = pyotp.TOTP(secret).at(accepted.timestamp())
    assert totp.verify_totp_step(secret, code) == accepted
    assert totp.verify_totp_step(secret, code, last_used_at=accepted) is None


def test_newer_step_allowed_but_older_step_cannot_go_backwards(clock):
    secret = pyotp.random_base32()
    before = datetime.fromtimestamp(clock.current.timestamp() - 30, timezone.utc)
    after = datetime.fromtimestamp(clock.current.timestamp() + 30, timezone.utc)
    assert totp.verify_totp_step(secret, pyotp.TOTP(secret).at(clock.current.timestamp()), last_used_at=before) == clock.current
    assert totp.verify_totp_step(secret, pyotp.TOTP(secret).at(clock.current.timestamp()), last_used_at=after) is None
