"""Phone number normalization helpers."""
from __future__ import annotations

import re


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
