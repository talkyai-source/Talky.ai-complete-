"""One canonical vocabulary for the two kinds of address an inbound call can carry.

Talky has always assumed an inbound leg is addressed by a **public telephone
number** in E.164 (``+442046132300``).  Internal PBX **extensions** (``940003``)
are a different kind of address: six digits that are meaningless outside one
carrier's account namespace, that no country code can repair, and that must
never be treated as a phone number.

Before this module the two were conflated: ``inbound_router.normalize_did``
required 7-15 digits, so a call to extension ``940003`` normalised to ``None``
and both the router and admission denied it as ``invalid_did``
(inbound_router.py rejection, inbound_admission.py rejection).  The temptation
is to widen that digit range.  That would be wrong: it would make ``9400031``
normalise to the *phone number* ``+9400031`` and quietly let a typo route a
real call.

So the distinction is made explicit instead of inferred:

* A **DID** is canonically ``+<7-15 digits>`` — exactly what
  ``normalize_did`` always produced.  Unchanged, byte for byte.
* An **extension** is canonically ``ext:<3-8 digits>`` — a tagged form that
  can never collide with a DID, because a DID always starts with ``+``.

**The tag is authority, not syntax.**  Only the reconciler's generated
dialplan emits ``ext:`` — and it only does so for an extension that survived
the reviewed carrier inventory and an active same-tenant binding.  The
preserved catch-all passes the caller's own Request-URI through
``FILTER(0-9+,...)`` (see telephony/asterisk/conf/talky-inbound.conf), so a
caller who escapes a colon into the user part (``sip:ext%3A940003@...``)
cannot forge the tag: the colon is stripped before Asterisk ever reaches
Stasis, the bare digits match no DID, and admission fails closed.

Keeping the canonical form a plain string (rather than a new type threaded
through 3000 lines of admission) is deliberate: every existing caller keeps
working on DIDs, and only the two lookups that must know the difference branch
on :func:`address_kind`.
"""
from __future__ import annotations

import re
from typing import Literal, Optional

EXTENSION_PREFIX = "ext:"

# 3-8 digits. Narrower than a DID on purpose: an internal dial plan that needs
# more than eight digits is a phone number, not an extension.
_EXTENSION_DIGITS_RE = re.compile(r"^[0-9]{3,8}$")

AddressKind = Literal["did", "extension"]


def canonical_extension(digits: str) -> str:
    """Canonical tagged form for an internal extension (``940003`` -> ``ext:940003``)."""

    return f"{EXTENSION_PREFIX}{digits}"


def parse_extension(raw: Optional[str]) -> Optional[str]:
    """Return the bare digits of a *tagged* extension, else ``None``.

    Accepts only the canonical tagged form.  An untagged ``"940003"`` returns
    ``None`` on purpose — see the module docstring on why the tag is authority.
    """

    if not raw:
        return None
    value = str(raw).strip()
    if not value.lower().startswith(EXTENSION_PREFIX):
        return None
    digits = value[len(EXTENSION_PREFIX) :].strip()
    if not _EXTENSION_DIGITS_RE.match(digits):
        return None
    return digits


def is_extension_address(value: Optional[str]) -> bool:
    """True when ``value`` is a canonical tagged extension address."""

    return parse_extension(value) is not None


def address_kind(canonical: Optional[str]) -> Optional[AddressKind]:
    """Classify an already-canonical address, or ``None`` if it is neither."""

    if not canonical:
        return None
    if is_extension_address(canonical):
        return "extension"
    text = str(canonical).strip()
    if text.startswith("+") and text[1:].isdigit() and 7 <= len(text[1:]) <= 15:
        return "did"
    return None


def redaction_label(canonical: Optional[str]) -> str:
    """Log-safe descriptor of the address *kind* (never the address itself)."""

    kind = address_kind(canonical)
    return kind or "invalid"
