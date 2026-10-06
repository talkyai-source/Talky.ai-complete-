"""Shared contact formatting used by action authorization and privacy cleanup."""
from __future__ import annotations


from app.services.scripts.spoken_email_normalizer import natural_email_readback, natural_phone_readback


def test_natural_readback_word_local_part():
    assert natural_email_readback("allstateestimation@gmail.com") == \
        "allstateestimation at gmail dot com"


def test_natural_readback_word_plus_digits():
    assert natural_email_readback("john7890@gmail.com") == "john 7 8 9 0 at gmail dot com"


def test_natural_readback_spells_only_nonword_runs():
    assert natural_email_readback("xq7@gmail.com") == "x-q 7 at gmail dot com"


def test_natural_readback_multidot_domain():
    assert natural_email_readback("bob@yahoo.co.uk") == "bob at yahoo dot co dot uk"


def test_natural_readback_speaks_local_separators():
    assert natural_email_readback("j.smith@gmail.com") == "j dot smith at gmail dot com"
    assert natural_email_readback("john_smith@acme.com") == "john underscore smith at acme dot com"
    assert natural_email_readback("a-team@acme.com") == "a dash team at acme dot com"
    assert natural_email_readback("bob+tag@acme.com") == "bob plus tag at acme dot com"


def test_natural_phone_readback():
    # Every digit is still said on its own (a wrong one is catchable), but in
    # the chunks people use, with a comma pause between them (2026-09-29).
    assert natural_phone_readback("5551234567") == "5 5 5, 1 2 3, 4 5 6 7"
    assert natural_phone_readback("+447911") == "plus 4 4, 7 9 1 1"  # UK "7911" prefix
    assert natural_phone_readback("+923120750496") == "plus 9 2, 3 1 2, 0 7 5, 0 4 9 6"
    assert natural_phone_readback("+447429916656") == "plus 4 4, 7 4 2 9, 9 1 6, 6 5 6"
    assert natural_phone_readback("+16473476870") == "plus 1, 6 4 7, 3 4 7, 6 8 7 0"
    assert natural_phone_readback("12345") == "1 2 3 4 5"
    assert natural_phone_readback("") == ""
    assert natural_phone_readback(None) == ""
