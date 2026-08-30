"""Contract tests for the SHIPPED production provisioning script.

``setup-asterisk.sh`` is what actually runs on the Hetzner box (the
``telephony/`` OpenSIPS/canary stack is modelled, not deployed).  Until this
file existed, ``grep -rlE "setup-asterisk|pjsip\\.d" backend/tests/`` returned
nothing: no test in the suite read the script that defines production's
dialplan.  That is how an inbound-routing S0 survived two audits with a fully
green suite.

Every assertion here is derived from the consumer, not restated from memory:

* the Stasis argument order is checked by feeding the script's own argument
  list through :meth:`AsteriskAdapter._extract_inbound_meta` — the real parser.
  If the parser's argument contract changes, this test changes with it.
* the ``pjsip.d`` ownership/mode is read out of
  :mod:`app.infrastructure.telephony.pjsip_config_generator`'s own docstring,
  so the script and the generator cannot drift apart silently.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.infrastructure.telephony import pjsip_config_generator
from app.infrastructure.telephony.asterisk_adapter import AsteriskAdapter

# backend/tests/unit/<this file> -> backend/tests -> backend -> repo root
REPO_ROOT = Path(__file__).resolve().parents[3]
SETUP_SCRIPT = REPO_ROOT / "setup-asterisk.sh"
DIALPLAN_SOURCE = REPO_ROOT / "telephony" / "asterisk" / "conf" / "talky-inbound.conf"

INBOUND_CONTEXT = "from-talky-inbound"

# Sentinels standing in for what Asterisk expands at call time.
DIALLED_DID = "+441184960111"
CALLER_ANI = "+447700900123"

# Dialplan variables the inbound context is allowed to reference, mapped to the
# value Asterisk would substitute for a call to DIALLED_DID from CALLER_ANI.
DIALPLAN_VARS = {
    "${EXTEN}": DIALLED_DID,
    "${CALLERID(num)}": CALLER_ANI,
    "${CALLERID(number)}": CALLER_ANI,
    "${CONTEXT}": INBOUND_CONTEXT,
    "${TALKY_ROUTE_DID}": DIALLED_DID,
}


@pytest.fixture(scope="module")
def script_text() -> str:
    assert SETUP_SCRIPT.is_file(), (
        f"{SETUP_SCRIPT} is missing. This is the script that provisions "
        "production Asterisk; without it inbound routing is unprovisioned."
    )
    return SETUP_SCRIPT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def managed_dialplan_text() -> str:
    assert DIALPLAN_SOURCE.is_file(), "repository-owned inbound dialplan is missing"
    return DIALPLAN_SOURCE.read_text(encoding="utf-8")


def _heredoc_body(script: str, target_path: str) -> str:
    """Return the body of the `cat > <target_path> <<EOF ... EOF` heredoc."""
    m = re.search(
        r"cat\s*>\s*" + re.escape(target_path) + r"\s*<<\s*'?(\w+)'?\s*\n(.*?)\n\1\s*$",
        script,
        re.DOTALL | re.MULTILINE,
    )
    assert m, (
        f"setup-asterisk.sh no longer writes {target_path} via a heredoc. "
        "This test parses the shipped script; update the parser, do not delete "
        "the assertions."
    )
    return m.group(2)


def _context_block(dialplan: str, context: str) -> list[str]:
    """Return the dialplan lines of `[context]` up to the next section."""
    lines = dialplan.splitlines()
    out: list[str] = []
    inside = False
    for line in lines:
        stripped = line.strip()
        if re.fullmatch(r"\[[^\]]+\]", stripped):
            inside = stripped == f"[{context}]"
            continue
        if inside:
            out.append(line)
    assert out, f"[{context}] context not found in the generated extensions.conf"
    return out


def _balanced_call_body(code: str, start: int) -> str:
    """Return the text inside `(...)` beginning at the '(' at `start`."""
    depth, out = 0, ""
    for ch in code[start:]:
        if ch == "(":
            depth += 1
            if depth == 1:
                continue
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return out
        out += ch
    raise AssertionError(f"unbalanced parentheses in dialplan line: {code!r}")


def _stasis_args(context_lines: list[str]) -> list[str]:
    """Extract the Stasis() arguments AFTER the app name, as written."""
    for line in context_lines:
        code = line.split(";", 1)[0]
        m = re.search(r"\bStasis\(", code)
        if m:
            raw = _balanced_call_body(code, m.end() - 1)
            # Split on commas that are not inside ${...( ... )} function calls.
            parts, depth, cur = [], 0, ""
            for ch in raw:
                if ch == "(":
                    depth += 1
                elif ch == ")":
                    depth -= 1
                if ch == "," and depth == 0:
                    parts.append(cur.strip())
                    cur = ""
                else:
                    cur += ch
            parts.append(cur.strip())
            assert parts[0] == "talky_ai", (
                f"inbound Stasis app is {parts[0]!r}, not 'talky_ai' — the "
                "backend only subscribes to the 'talky_ai' Stasis app, so the "
                "call would never reach it."
            )
            return parts[1:]
    raise AssertionError(
        f"no Stasis() invocation in the [{INBOUND_CONTEXT}] context — inbound "
        "calls would never be handed to the backend at all."
    )


def _substitute(arg: str) -> str:
    if "${" not in arg:
        return arg  # a literal, e.g. the 'inbound' direction marker
    value = DIALPLAN_VARS.get(arg)
    assert value is not None, (
        f"Stasis argument {arg!r} uses a dialplan variable this test does not "
        "model. Add it to DIALPLAN_VARS with the value Asterisk substitutes, "
        "and confirm asterisk_adapter reads it. Note: ${TALKY_ORIGINAL_DID} / "
        "${TALKY_AGENT_ID} are set by OpenSIPS, which is NOT deployed — they "
        "would expand to the empty string on the production box."
    )
    return value


def _stasis_start_event(args: list[str], *, with_dialplan_exten: bool) -> dict:
    """Build the StasisStart ARI event Asterisk would emit for this dialplan."""
    dialplan: dict = {"context": INBOUND_CONTEXT}
    if with_dialplan_exten:
        dialplan["exten"] = DIALLED_DID
    return {
        "type": "StasisStart",
        "args": [_substitute(a) for a in args],
        "channel": {
            "id": "1756400000.1",
            "name": "PJSIP/blazedigitel-00000001",
            "dialplan": dialplan,
            "caller": {"number": CALLER_ANI, "name": ""},
            "connected": {"number": "", "name": ""},
        },
    }


@pytest.fixture(scope="module")
def inbound_stasis_args(managed_dialplan_text: str) -> list[str]:
    return _stasis_args(_context_block(managed_dialplan_text, INBOUND_CONTEXT))


# ── S0 #1: Stasis argument order ─────────────────────────────────────────────


def test_inbound_stasis_args_parse_to_the_dialled_did(inbound_stasis_args):
    """The shipped dialplan's own args, through the adapter's real parser.

    `dialplan.exten` is withheld so the args alone must carry the DID — which
    is the whole point of passing them. If the caller's number sits in the DID
    position, this resolves the ANI as the called DID: every inbound call then
    lands on `unknown_did`, or worse routes to whichever OTHER tenant happens
    to have registered that caller's number as their DID.
    """
    adapter = AsteriskAdapter()
    meta = adapter._extract_inbound_meta(
        _stasis_start_event(inbound_stasis_args, with_dialplan_exten=False)
    )

    assert meta["direction"] == "inbound", (
        f"adapter parsed direction={meta['direction']!r} from args "
        f"{inbound_stasis_args!r}; it only accepts 'inbound'/'outbound' in "
        "position 0, so the remaining args are read one slot off."
    )
    assert meta["called_did"] == DIALLED_DID, (
        "PRODUCTION INBOUND ROUTING IS BROKEN: setup-asterisk.sh passes "
        f"{inbound_stasis_args!r} to Stasis, which the adapter resolves to "
        f"called_did={meta['called_did']!r} for a call from {CALLER_ANI} to "
        f"{DIALLED_DID}. Expected the DIALLED number. Emit "
        "Stasis(talky_ai,inbound,${EXTEN},${CONTEXT})."
    )
    assert meta["context"] == INBOUND_CONTEXT, (
        f"context resolved to {meta['context']!r}; the dialplan context must "
        "be in the third Stasis argument (${CONTEXT})."
    )


def test_caller_number_is_not_in_the_did_position(inbound_stasis_args):
    """The ANI must not occupy the DID slot, and must still be recoverable."""
    assert len(inbound_stasis_args) >= 2, (
        f"inbound Stasis args {inbound_stasis_args!r} carry no DID at all; the "
        "adapter would fall back to dialplan.exten/connected.number, which the "
        "carrier does not reliably populate."
    )
    did_arg = inbound_stasis_args[1]
    assert "CALLERID" not in did_arg.upper(), (
        f"the DID position of Stasis() is {did_arg!r} — that is the CALLER's "
        "number. Inbound tenant routing keys on the CALLED DID; passing the "
        "ANI here resolves unknown_did, or routes the call to another tenant "
        "that owns that number. Use ${EXTEN}."
    )
    # The ANI is read off the channel (channel.caller.number), so dropping it
    # from the args loses nothing — prove it with the real parser.
    adapter = AsteriskAdapter()
    meta = adapter._extract_inbound_meta(
        _stasis_start_event(inbound_stasis_args, with_dialplan_exten=True)
    )
    assert meta["caller_number"] == CALLER_ANI, (
        "the caller number must still reach the adapter (it reads "
        "channel.caller.number); got {!r}".format(meta["caller_number"])
    )


def test_no_answer_before_stasis_in_inbound_context(managed_dialplan_text):
    """Route-before-answer is a release-gate invariant.

    Answering before the routing decision bills the carrier leg for calls we
    then reject, and destroys the ability to return a SIP rejection code.
    """
    for line in _context_block(managed_dialplan_text, INBOUND_CONTEXT):
        code = line.split(";", 1)[0]
        if "Stasis(" in code:
            break
        assert "Answer(" not in code, (
            f"[{INBOUND_CONTEXT}] answers the channel before Stasis(): "
            f"{line.strip()!r}. Inbound must stay ringing until the backend "
            "admits or rejects the call."
        )


def test_setup_never_overwrites_the_live_extensions_file(script_text):
    assert "cat > /etc/asterisk/extensions.conf" not in script_text
    assert "extensions.d/*.conf" in script_text
    assert 'cmp -s "$DIALPLAN_CANDIDATE" "$DIALPLAN_LIVE"' in script_text
    assert ".talky-inbound.conf.candidate" in script_text
    assert "Live file was not touched" in script_text


def test_managed_dialplan_sends_ringback_before_stasis(managed_dialplan_text):
    block = "\n".join(_context_block(managed_dialplan_text, INBOUND_CONTEXT))
    assert block.index("Ringing()") < block.index("Stasis(")


# ── S0 #2: pjsip.d prerequisites the generator documents ─────────────────────


def _generator_prereq(letter: str) -> str:
    doc = pjsip_config_generator.__doc__ or ""
    m = re.search(
        rf"^\s*\({letter}\)\s(.*?)(?=^\s*\([a-z]\)\s|\Z)", doc, re.DOTALL | re.MULTILINE
    )
    assert m, f"prerequisite ({letter}) is gone from the generator docstring"
    return " ".join(m.group(1).split())


def test_pjsip_conf_includes_the_generated_trunk_directory(script_text):
    """Prerequisite (a): without the include, tenant trunks never load.

    setup-asterisk.sh rewrites /etc/asterisk/pjsip.conf wholesale on every run,
    so a hand-added include is destroyed the next time ops runs the script.
    """
    prereq = _generator_prereq("a")
    assert "#include pjsip.d/*.conf" in prereq, (
        "generator prerequisite (a) no longer names the include this test "
        f"asserts: {prereq!r}"
    )
    body = _heredoc_body(script_text, "/etc/asterisk/pjsip.conf")
    directives = [ln.strip() for ln in body.splitlines()]
    assert "#include pjsip.d/*.conf" in directives, (
        "the generated /etc/asterisk/pjsip.conf has no '#include "
        "pjsip.d/*.conf'. Every per-tenant SIP trunk written by "
        "pjsip_config_generator into /etc/asterisk/pjsip.d/trunk-<id>.conf is "
        "therefore never loaded by Asterisk — a 'pjsip reload' still reports "
        "success, so the trunk silently does not exist."
    )


def test_pjsip_d_directory_is_provisioned_with_documented_ownership(script_text):
    """Prerequisite (b): the setgid dir the generator writes into."""
    prereq = _generator_prereq("b")
    owner_m = re.search(r"``(\w+:\w+)``", prereq)
    mode_m = re.search(r"mode ``(\d{3,4})``", prereq)
    assert owner_m and mode_m, (
        f"cannot read owner/mode out of generator prerequisite (b): {prereq!r}"
    )
    owner, mode = owner_m.group(1), mode_m.group(1)

    assert re.search(r"mkdir\s+(-\w+\s+)*/etc/asterisk/pjsip\.d\b", script_text), (
        "setup-asterisk.sh never creates /etc/asterisk/pjsip.d. The backend "
        "cannot write a tenant trunk file into a directory that does not "
        "exist, so trunk provisioning fails on a freshly provisioned box."
    )
    assert re.search(
        rf"chown\s+(-\w+\s+)*{re.escape(owner)}\s+/etc/asterisk/pjsip\.d\b", script_text
    ), (
        f"/etc/asterisk/pjsip.d must be chowned {owner} (generator "
        "prerequisite (b)); otherwise the asterisk process cannot read the "
        "0640 trunk files the backend writes and the trunk never loads."
    )
    assert re.search(
        rf"chmod\s+(-\w+\s+)*{re.escape(mode)}\s+/etc/asterisk/pjsip\.d\b", script_text
    ), (
        f"/etc/asterisk/pjsip.d must be mode {mode} (setgid, generator "
        "prerequisite (b)) so files the backend creates inherit group "
        "'asterisk'. Without setgid they are group 'admins' and asterisk "
        "cannot read them."
    )


def test_backend_user_joins_the_asterisk_group(script_text):
    """Prerequisite (c): admins must be in the asterisk group.

    The pjsip.d dir is 2770 — group-writable only. If the backend service user
    is not in the 'asterisk' group it cannot create the trunk file at all.
    """
    prereq = _generator_prereq("c")
    assert "asterisk" in prereq and "group" in prereq
    assert re.search(r"usermod\s+(-\w+\s+)*asterisk\s+admins\b", script_text), (
        "setup-asterisk.sh never adds the backend service user 'admins' to "
        "the 'asterisk' group (generator prerequisite (c)). With pjsip.d at "
        "mode 2770 the backend gets EACCES writing any tenant trunk file."
    )
