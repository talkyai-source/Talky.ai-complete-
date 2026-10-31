"""Bounded spoken confirmation of an existing action, never a new request parser.

Only explicit action phrases with all caller-controlled values matching the
server proposal qualify. Unknown grammar fails closed; no model judges consent.
"""
from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.scripts.spoken_email_normalizer import (
    natural_email_readback, spoken_digits_to_numerals,
)
from app.domain.services.phone_number_normalizer import normalize_phone_for_capture


def _words(value):
    return " ".join(re.findall(r"[a-z0-9]+", str(value).casefold()))


def _email_words(value):
    # Preserve the significance of email punctuation: "bob smith" must never
    # stand in for "bob.smith" simply because punctuation was stripped.
    for symbol, word in (("@", " at "), (".", " dot "), ("_", " underscore "),
                         ("-", " dash "), ("+", " plus ")):
        value = str(value).replace(symbol, word)
    return _words(value)


def _email_matches(phrase, expected):
    local, separator, domain = expected.partition("@")
    if not separator:
        return False
    spelled = " ".join(_email_words(char) for char in local) + " at " + _email_words(domain)
    aliases = (expected, natural_email_readback(expected), spelled)
    return _email_words(phrase.strip(" .,!")) in {_email_words(x) for x in aliases}


def _phone_matches(phrase, expected):
    converted = spoken_digits_to_numerals(phrase.strip(" .,!"))
    if not re.fullmatch(r"\+?[\d\s().-]+", converted):
        return False
    digits = re.sub(r"\D", "", converted)
    if re.fullmatch(r"\d{4,5}", expected):
        return not converted.startswith("+") and digits == expected
    # Repeating every existing international digit is unambiguous even if ASR
    # omits the word 'plus'. Never infer a country for a national/short number.
    if expected.startswith("+") and not converted.startswith("+") and digits == expected[1:]:
        converted = "+" + digits
    try:
        return normalize_phone_for_capture(converted) == expected
    except ValueError:
        return False


_ONES = ("zero one two three four five six seven eight nine ten eleven twelve "
         "thirteen fourteen fifteen sixteen seventeen eighteen nineteen").split()
_TENS = {20: "twenty", 30: "thirty", 40: "forty", 50: "fifty", 60: "sixty",
         70: "seventy", 80: "eighty", 90: "ninety"}
_ORDINAL = ("zeroth first second third fourth fifth sixth seventh eighth ninth tenth "
            "eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth "
            "eighteenth nineteenth twentieth twenty-first twenty-second twenty-third "
            "twenty-fourth twenty-fifth twenty-sixth twenty-seventh twenty-eighth "
            "twenty-ninth thirtieth thirty-first").split()


def _number(n):
    return _ONES[n] if n < 20 else _TENS[n // 10 * 10] + (" " + _ONES[n % 10] if n % 10 else "")


def _date_matches(phrase, local):
    day, year = local.day, local.year
    suffix = "th" if 10 <= day % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    days = {str(day), f"{day:02d}", f"{day}{suffix}", _number(day), _ORDINAL[day]}
    years = {str(year)}
    if 2000 <= year < 2100:
        years.update({"two thousand " + _number(year % 100), "twenty " + _number(year % 100)})
    month = local.strftime("%B")
    aliases = {f"{d} {month} {y}" for d in days for y in years}
    aliases |= {f"{month} {d} {y}" for d in days for y in years}
    aliases |= {f"the {d} of {month} {y}" for d in days for y in years}
    aliases |= {f"{month} the {d} {y}" for d in days for y in years}
    return _words(phrase) in {_words(x) for x in aliases}


def _time_matches(phrase, local):
    # AM/PM is mandatory; no interpretation of "three", noon or midnight.
    hour = local.hour % 12 or 12
    hours = {str(hour), f"{hour:02d}", _number(hour)}
    minutes = {str(local.minute), f"{local.minute:02d}", _number(local.minute)}
    if local.minute < 10:
        minutes.add("oh " + _number(local.minute))
    clock = {f"{h}:{m}" for h in hours for m in minutes}
    if local.minute == 0:
        clock |= hours | {f"{h} o'clock" for h in hours}
    period = "am" if local.hour < 12 else "pm"
    return _words(phrase) in {_words(f"{c} {p}") for c in clock for p in (period, ".".join(period))}


def _zone_matches(phrase, zone):
    # A city + 'time' names this exact IANA zone; avoid ambiguous abbreviations
    # such as CST/IST and broad regional names such as 'Central time'.
    aliases = {zone, zone.replace("_", " ")}
    if "/" in zone:
        aliases.add(zone.rsplit("/", 1)[1].replace("_", " ") + " time")
    elif zone == "UTC":
        aliases.add("UTC time")
    return _words(phrase) in {_words(x) for x in aliases}


def explicit_action_matches(action, payload, user_text):
    """Recognize a narrow explicit authorization independently of playback."""
    text = str(user_text or "").strip().rstrip(".! ")
    if "?" in text or re.search(r"\b(?:no|not|never|don't|do not|cancel|instead|wrong|stop|wait|change|maybe|if|unless)\b", text, re.I):
        return False
    text = re.sub(r"^(?:(?:yes|i confirm|confirm|go ahead)[,\s]+)?(?:please[,\s]+)?", "", text, flags=re.I)
    if action == "send_email":
        subject = re.escape(str(payload.get("subject") or ""))
        match = re.fullmatch(
            rf"(?:send(?: me)? (?:the |this |that )?(?:email(?: titled {subject})?|(?:requested )?details|information) to|email me (?:at|on))\s+(.+)",
            text, re.I,
        )
        return bool(match and _email_matches(match[1], payload["recipient"]))
    if action == "transfer_call":
        match = re.fullmatch(r"(?:transfer|connect) (?:this call|the call|me) to\s+(.+)", text, re.I)
        if not match:
            return False
        destination = str(payload["destination"])
        return _phone_matches(match[1], destination) if re.search(r"\d", destination) else _words(match[1]) == _words(destination)
    if action == "schedule_callback":
        match = re.fullmatch(
            r"(?:schedule (?:a )?callback|call(?: me)?(?: back)?) (?:to |at |on )?(.+?) on (.+?) at (.+?\s+(?:a\.?\s?m\.?|p\.?\s?m\.?)) (?:in )?(.+)",
            text, re.I,
        )
        if not match:
            return False
        local = datetime.fromisoformat(payload["scheduled_at"]).astimezone(ZoneInfo(payload["timezone"]))
        return (_phone_matches(match[1], payload["phone"]) and _date_matches(match[2], local)
                and _time_matches(match[3], local) and _zone_matches(match[4], payload["timezone"]))
    if action == "submit_form":
        # Configured inbox/subject are server-controlled. The caller explicitly
        # names the form and repeats each captured value, in the readback order.
        name = re.escape(str(payload["name"]))
        fields = list(payload["values"])
        pattern = rf"submit (?:the )?{name}(?: form)? with " + r"(?:\s*;\s*|\s*,\s*|\s+and\s+)".join(
            re.escape(field.replace("_", " ")) + r"\s*:?\s*(.+?)" for field in fields)
        match = re.fullmatch(pattern, text, re.I)
        if not match:
            return False
        for field, phrase in zip(fields, match.groups()):
            expected = payload["values"][field]
            matches = (_email_matches(phrase, expected) if field == "email" else
                       _phone_matches(phrase, expected) if field == "phone" else
                       _words(phrase) == _words(expected))
            if not matches:
                return False
        return True
    return False
