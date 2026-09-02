"""Phone number normalization helpers -- the ONE home for E.164 handling.

This module is the single place phone/E.164 normalisation lives.  Everything
else in the codebase (contacts import, tenant DID registration, CallGuard,
the DNC list) delegates here through a thin wrapper that keeps its own public
name.

There is deliberately more than one function, because the call sites genuinely
want different things and pretending otherwise is how the DNC/billing
divergence happened in the first place.  Pick by contract:

===========================  ================================================
Function                     Contract
===========================  ================================================
``normalize_phone_number``   CANONICAL.  libphonenumber-backed, region-aware
                             (``default_country``), passes 4-5 digit SIP
                             extensions through, rejects junk with
                             ``ValueError``.  Use this for anything new.
``normalize_phone_for_``     VOICE CAPTURE. Strict libphonenumber validity;
``capture``                  accepts ``+`` E.164 input without a region, but a
                             national-format number requires explicit region
                             context and is never guessed as US.
``normalize_phone_number_``  Same, but never rejects on length/format --
``lenient``                  digits passthrough fallback.  Only for accounts
                             with relaxed phone validation.
``normalize_phone_number_``  DEPRECATED shape kept for ``contacts.py``'s
``legacy``                   legacy exported symbol: min 3 digits, SIP
                             passthrough up to 6 digits, no libphonenumber.
``normalize_e164_digits``    Never raises.  Digits-only + leading ``+``,
                             bare-10-digit means US.  CallGuard's contract --
                             a guard must not explode on a malformed number.
                             ALSO the DNC list's stored form: the write and
                             the guard's read MUST be the same function.
``normalize_e164_``          Never raises.  libphonenumber when it can parse,
``libphonenumber``           else digits + forced leading ``+``, ``""`` for
                             empty.  No longer on any DNC path -- kept for its
                             vanity-number handling and pinned by the
                             equivalence baseline.
``is_strict_e164``           Predicate for ``+[1-9]`` then 6-14 more digits.
                             Use it to REJECT a never-raising normaliser's junk
                             output before it reaches the database.
``validate_e164_strict``     Not a normaliser: whitespace-strip + assert
                             strict ``+[1-9]\\d{6,14}``.  Registration of a
                             tenant DID, where guessing would be wrong.
===========================  ================================================

Known divergences between these are catalogued (and pinned) in
``tests/unit/test_phone_normalizer_equivalence.py``.

DNC INVARIANT: ``dnc_entries.normalized_number`` is written and read with
``normalize_e164_digits`` and nothing else.  Until 2026-08-27 the write path
used ``normalize_e164_libphonenumber``, which stores a bare 10-digit US number
as ``+4155551234`` while CallGuard looks it up as ``+14155551234`` -- so the
row never matched and a number the customer had put on Do-Not-Call stayed
dialable.  Rows written before the fix need
``scripts/backfill_dnc_normalized_numbers.py``.
"""
from __future__ import annotations

import re

_STRICT_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


def normalize_phone_number(phone: str, default_country: str = "US") -> str:
    """
    Normalize phone number to E.164 format.

    Uses libphonenumber when available so non-US numbers normalize correctly.
    Short SIP extensions (4-5 digits) are passed through.
    """
    has_plus = phone.strip().startswith("+")
    cleaned = re.sub(r"[^\d]", "", phone)

    if not cleaned:
        raise ValueError("Invalid phone number")

    if len(cleaned) <= 5:
        if len(cleaned) < 4:
            raise ValueError("Phone number too short (minimum 4 digits for SIP extensions)")
        return cleaned

    if len(cleaned) == 6:
        raise ValueError("Phone number too short (minimum 7 digits for phone numbers)")

    if len(cleaned) > 15:
        raise ValueError("Phone number too long (maximum 15 digits)")

    try:
        import phonenumbers

        region = None if has_plus else (default_country or "US").upper()
        parsed = phonenumbers.parse(phone, region)
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(
                parsed,
                phonenumbers.PhoneNumberFormat.E164,
            )
    except Exception:
        pass

    fallback_country_codes = {
        "GB": "44",
        "DE": "49",
        "AU": "61",
    }
    country = (default_country or "US").upper()
    if country in fallback_country_codes and cleaned.startswith("0"):
        return f"+{fallback_country_codes[country]}{cleaned[1:]}"

    if has_plus:
        return f"+{cleaned}"

    if len(cleaned) == 10:
        return f"+1{cleaned}"

    if len(cleaned) == 11 and cleaned.startswith("1"):
        return f"+{cleaned}"

    return f"+{cleaned}"


def normalize_phone_for_capture(phone: str, region: str | None = None) -> str:
    """Validate a caller-stated number and return strict E.164.

    Voice capture is not allowed to guess a country.  An input already carrying
    ``+<country code>`` is self-describing; every other input requires an
    explicit ISO-3166 region supplied by the call context.  Unlike the legacy
    contact importer helper above, this path has no digits-only fallback: a
    number that libphonenumber cannot prove valid remains unconfirmed.
    """
    raw = str(phone or "").strip()
    if not raw:
        raise ValueError("Phone number is empty")
    has_country_code = raw.startswith("+")
    explicit_region = str(region or "").strip().upper() or None
    if not has_country_code and explicit_region is None:
        raise ValueError(
            "Country or region is required for a phone number without a '+' country code"
        )

    import phonenumbers

    try:
        parsed = phonenumbers.parse(raw, None if has_country_code else explicit_region)
    except phonenumbers.NumberParseException as exc:
        raise ValueError("Invalid phone number") from exc
    if not phonenumbers.is_possible_number(parsed) or not phonenumbers.is_valid_number(parsed):
        raise ValueError("Invalid phone number")
    normalized = phonenumbers.format_number(
        parsed,
        phonenumbers.PhoneNumberFormat.E164,
    )
    if not is_strict_e164(normalized):
        raise ValueError("Invalid E.164 phone number")
    return normalized


def normalize_phone_number_lenient(phone: str) -> str:
    """Lenient normalization that NEVER rejects on length/format.

    For accounts whose phone validation is temporarily relaxed (e.g. adding
    short or odd test numbers). It tries the strict normalizer first, so a
    normal number still comes out as proper E.164; only when the strict path
    rejects the number does it fall back to a digits passthrough (preserving a
    leading +). The single hard rule that remains is "must contain a digit".
    """
    try:
        return normalize_phone_number(phone)
    except ValueError:
        pass  # fall through to the lenient passthrough below

    has_plus = (phone or "").strip().startswith("+")
    digits = re.sub(r"[^\d]", "", phone or "")
    if not digits:
        raise ValueError("Phone number contains no digits")
    return f"+{digits}" if has_plus else digits


def normalize_phone_number_legacy(phone: str) -> str:
    """The pre-consolidation ``contacts.normalize_phone_number`` contract.

    DEPRECATED -- do not add callers; use :func:`normalize_phone_number`.
    It is kept verbatim because ``app.api.v1.endpoints.contacts`` still
    exports the symbol.

    Differs from the canonical normalizer in three ways:
      * minimum is 3 digits, not 4;
      * SIP-extension passthrough runs to 6 digits, not 5 (so ``"123456"``
        is accepted here and rejected canonically);
      * no libphonenumber at all -- so extensions are not stripped
        (``"+1 415 555 1234 ext 22"`` keeps the ``22``) and ``default_country``
        has no equivalent.

    Raises ValueError for empty/None and for digitless input.
    """
    if not phone:
        raise ValueError("Phone number is empty")

    # Remove all non-digit characters except leading +
    has_plus = phone.strip().startswith("+")
    cleaned = re.sub(r"[^\d]", "", phone)

    if not cleaned:
        raise ValueError("Phone number contains no digits")

    # Allow short SIP extensions (3-6 digits) to pass through as-is
    if len(cleaned) <= 6:
        if len(cleaned) < 3:
            raise ValueError("Phone number too short (minimum 3 digits for SIP extensions)")
        return cleaned  # Return raw SIP extension - no E.164 normalization

    if len(cleaned) > 15:
        raise ValueError("Phone number too long (maximum 15 digits)")

    # If already has + and country code, use as-is
    if has_plus:
        return f"+{cleaned}"

    # If 10 digits (US/Canada without country code), add +1
    if len(cleaned) == 10:
        return f"+1{cleaned}"

    # If 11 digits starting with 1 (US/Canada with country code), add +
    if len(cleaned) == 11 and cleaned.startswith("1"):
        return f"+{cleaned}"

    # Otherwise, return with + prefix
    return f"+{cleaned}"


def normalize_e164_digits(phone_number: str) -> str:
    """Never-raising digit normalisation. CallGuard's contract.

    A pre-flight guard must not blow up on a malformed number -- it has to
    return *something* the downstream validity check can reject cleanly.  So
    this deliberately has no error path:

      * falsy input (including ``None``) -> ``""``;
      * everything non-digit removed, a leading ``+`` preserved;
      * a bare 10-digit number is assumed US/Canada and gets ``+1``;
      * anything else just gets a ``+`` prefix, valid or not.

    NOTE the ``+`` is detected on the RAW string (``phone_number.startswith``),
    not the stripped one, so a leading space hides it.  Preserved verbatim
    from the original.

    This is ALSO the form ``dnc_entries.normalized_number`` is stored in, so
    that the DNC write and this guard's read can never drift apart.  Callers
    that persist the result must gate it on :func:`is_strict_e164` first --
    this function happily returns ``"+"`` for ``"abc"``.
    """
    if not phone_number:
        return ""

    # Remove all non-digit characters except leading +
    has_plus = phone_number.startswith("+")
    digits = re.sub(r"\D", "", phone_number)

    if has_plus:
        return f"+{digits}"

    # Assume US/Canada if no country code
    if len(digits) == 10:
        return f"+1{digits}"

    return f"+{digits}"


def normalize_e164_libphonenumber(raw: str) -> str:
    """Never-raising libphonenumber-first normalisation.

    NOT the DNC stored form any more -- it used to be, and the disagreement
    with :func:`normalize_e164_digits` on a bare 10-digit US number meant DNC
    rows never matched at dial time.  See the DNC INVARIANT in the module
    docstring.  Kept because it is the only helper that resolves vanity
    numbers (``+1-800-FLOWERS`` -> ``+18003569377``).

    Strips cosmetic characters and returns E.164.  Uses libphonenumber when it
    can parse the input with NO default region -- meaning a number without a
    ``+`` and without a country code (a bare 10-digit US number, a UK
    ``07...``) will NOT parse and falls through to the digit fallback.

    Falsy input (including ``None``) -> ``""``.  The fallback keeps digits and
    ``+`` characters and force-prefixes a ``+``, so it can emit strings that
    are not valid E.164 (``"+"``, ``"+00"``).  Preserved verbatim.
    """
    if not raw:
        return ""
    text = raw.strip()
    try:
        import phonenumbers
        parsed = phonenumbers.parse(text, None)
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.E164,
            )
    except Exception:
        pass
    # Fallback: strip everything except digits and a leading +.
    cleaned = "".join(c for c in text if c.isdigit() or c == "+")
    if cleaned and not cleaned.startswith("+"):
        cleaned = "+" + cleaned
    return cleaned


def is_strict_e164(value: str) -> bool:
    """True when ``value`` is already strict E.164 (``+[1-9]`` + 6-14 digits).

    The never-raising normalisers (:func:`normalize_e164_digits`,
    :func:`normalize_e164_libphonenumber`) can return truthy junk -- ``"+"``,
    ``"+00"``, ``"+0000000000"``, a bare SIP extension turned into ``"+1234"``.
    Anything persisting their output to a lookup column must gate on this,
    otherwise the junk sits in the table forever matching nothing.
    """
    return bool(value) and bool(_STRICT_E164_RE.match(value))


def validate_e164_strict(value: str) -> str:
    """Whitespace-strip and ASSERT strict E.164 -- ``+[1-9]`` then 6-14 more.

    Not a normaliser: it never guesses a country code and never repairs the
    input.  Used where guessing would be actively wrong -- registering a
    tenant's own DID, where the number must be exactly what the carrier
    issued.  Raises ``ValueError`` (pydantic field validators re-wrap that as
    a ``ValidationError``) on anything else.
    """
    cleaned = value.strip()
    if not _STRICT_E164_RE.match(cleaned):
        raise ValueError(
            "e164 must start with '+' followed by 7-15 digits (E.164)"
        )
    return cleaned
