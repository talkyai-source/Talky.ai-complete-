"""Shared phone normalization contracts.

Dial destinations require libphonenumber validity in the selected country.
Campaign/import selection falls back to tenants.calling_rules.default_country
and never guesses US (D2-5); only DNC keys keep an explicit US default.
Extensions never become part of a dial destination or DNC key.
DNC writes and CallGuard reads use the same strict parser; malformed guard
inputs become an empty, undialable key. Existing canonical E.164 keys remain
unchanged. Historical malformed DNC rows still require owner reconciliation.

Voice capture-as-said, DID registration and legacy digits/lock keys retain
separate contracts. There is no lenient intake path (D2-6).
"""
from __future__ import annotations

import re

_STRICT_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


def normalize_phone_number(phone: str, default_country: str | None = "US") -> str:
    """Return a valid dial destination, excluding any extension; never guess.

    ``default_country=None`` means no country is known: a number written with
    its ``+`` country code still normalises, a national one is refused (D2-5).
    """
    country = validate_country(default_country) if default_country else None
    return normalize_phone_for_capture(phone, region=country)


def phone_number_error(raw: str, country: str | None) -> str:
    """The row-level message for a refused number: what the customer must fix."""
    if not country and not str(raw or "").strip().startswith("+"):
        return "Add the country code (for example +44) or choose a country for this number"
    return "Invalid phone number for the selected country"


def validate_country(value: str) -> str:
    """Validate an explicit ISO alpha-2 libphonenumber region."""
    import phonenumbers

    country = str(value or "").strip().upper()
    if len(country) != 2 or country not in phonenumbers.SUPPORTED_REGIONS:
        raise ValueError("default_country must be a supported ISO alpha-2 country")
    return country


def campaign_country(campaign=None, account_rules=None, requested=None, *, fallback=None) -> str | None:
    """Explicit import override, campaign country, account calling rules.

    No country is guessed (D2-5): with none of those set the result is
    ``fallback``, which is ``None`` for contact intake. Only callers that must
    stay consistent with an existing US-default key (DNC writes, the legacy
    repair tool) name ``fallback="US"`` explicitly.
    """
    config = (campaign or {}).get("script_config") or {}
    if not isinstance(config, dict):
        config = {}
    slots = config.get("campaign_slots") or {}
    if not isinstance(slots, dict):
        slots = {}
    rules = account_rules if isinstance(account_rules, dict) else {}
    chosen = (
        requested or config.get("default_country_code")
        or slots.get("default_country_code") or rules.get("default_country") or fallback
    )
    return validate_country(chosen) if chosen else None


def resolve_default_country(db_client, tenant_id, campaign=None, requested=None) -> str | None:
    """Read the existing account JSON only when no explicit/campaign choice exists."""
    config = (campaign or {}).get("script_config") or {}
    slots = config.get("campaign_slots") if isinstance(config, dict) else None
    if requested or (isinstance(config, dict) and config.get("default_country_code")) or (
        isinstance(slots, dict) and slots.get("default_country_code")
    ):
        return campaign_country(campaign, requested=requested)
    # tenants.id IS the tenant boundary (there is no tenants.tenant_id column).
    response = db_client.table("tenants").select("calling_rules").eq("id", str(tenant_id)).execute()
    if getattr(response, "error", None):
        raise RuntimeError("Unable to load account country")
    rows = response.data or []
    return campaign_country(campaign, account_rules=rows[0].get("calling_rules") if rows else None)


def normalize_suppression_key(raw: str, default_country: str = "US") -> str:
    """The shared DNC write/read key; invalid destinations remain undialable."""
    try:
        return normalize_phone_number(raw, default_country)
    except ValueError:
        return ""


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


_AS_SAID_RE = re.compile(r"^\+?\d{6,15}$")


def normalize_phone_as_said(phone: str) -> str:
    """A caller-stated number exactly as said, for every country.

    The voice agent records numbers generically (owner decision 2026-10-14):
    spaces, dashes, dots and brackets are removed and nothing is added -- no
    country code is inferred from the line or campaign, none is required, and
    no national numbering plan is applied. "081010188" stays "081010188";
    "+92 312 0750496" becomes "+923120750496".
    """
    compact = re.sub(r"[\s().\-]", "", str(phone or ""))
    if not _AS_SAID_RE.match(compact):
        raise ValueError("A phone number is 6 to 15 digits, with an optional leading '+'")
    return compact


def region_of_e164(number: str | None) -> str | None:
    """ISO-3166 region of a valid E.164 number, or None.

    Lets voice capture read a caller's national-format number in the country of
    the line the call is on, which is evidence about the caller, not a guess.
    """
    if not number or not is_strict_e164(str(number)):
        return None
    import phonenumbers

    try:
        parsed = phonenumbers.parse(str(number), None)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.region_code_for_number(parsed) or None


def normalize_e164_digits(phone_number: str) -> str:
    """Never-raising digit normalisation. CallGuard's contract.

    A pre-flight guard must not blow up on a malformed number -- it has to
    return *something* the downstream validity check can reject cleanly.  So
    this deliberately has no error path:

      * falsy input (including ``None``) -> ``""``;
      * everything non-digit removed, a leading ``+`` preserved; a bracketed
        trunk ``(0)`` after a leading ``+`` is dropped (``+44 (0)20...``);
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
    if has_plus:
        # "+44 (0)20 4613 2300": the bracketed 0 is the national trunk digit,
        # not part of the E.164 number. Keeping it stored "+4402046132300"
        # while the lead (libphonenumber) is dialled as "+442046132300", so the
        # DNC row never matched. Dropped only inside brackets after a "+".
        phone_number = re.sub(r"\(\s*0\s*\)", "", phone_number)
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
