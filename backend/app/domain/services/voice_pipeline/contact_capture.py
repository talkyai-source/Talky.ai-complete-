"""Provider-agnostic email/phone capture state machine.

The machine owns capture, validation, clarification, confirmation and
cancellation.  Voice transports only supply final transcript evidence and the
agent-readback gate; neither cascaded STT nor a realtime provider gets its own
contact semantics.

No function in this module logs caller contact data.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional, Sequence

from app.domain.services.phone_number_normalizer import normalize_phone_for_capture

CaptureKind = Literal["email", "phone"]


class CaptureStatus(str, Enum):
    NEEDS_CLARIFICATION = "needs_clarification"
    INVALID = "invalid"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMED = "confirmed"
    CANCELLED = "cancelled"


MAX_CONFIRMATION_ATTEMPTS = 3
_LOW_CONFIDENCE = 0.50


@dataclass(frozen=True)
class ContactCaptureState:
    kind: CaptureKind
    status: CaptureStatus
    raw_value: Optional[str] = None
    normalized_value: Optional[str] = None
    validation_status: str = CaptureStatus.NEEDS_CLARIFICATION.value
    confirmed_at: Optional[datetime] = None
    attempts: int = 0
    segments: tuple[str, ...] = ()
    clarification_prompt: Optional[str] = None

    def __post_init__(self) -> None:
        # Status is the source of truth; keep the audit string impossible to
        # drift when dataclasses.replace changes a state.
        if self.validation_status != self.status.value:
            object.__setattr__(self, "validation_status", self.status.value)


_CANCEL_RE = re.compile(
    r"\b(never\s*mind|forget\s+it|don'?t\s+(?:save|use|record|take)\s+(?:that|it)|"
    r"do\s+not\s+(?:save|use|record)\s+(?:that|it)|skip\s+(?:that|it))\b",
    re.IGNORECASE,
)
_EMAIL_INTENT_RE = re.compile(
    r"@|\b(?:(?:my|the)\s+)?e-?mail(?:\s+address)?\s+"
    r"(?:is|was|should\s+be)\b|"
    r"\bat(?:\s+the\s+rate|\s+sign)?\b.*\b(?:dot|period)\b",
    re.IGNORECASE,
)
_PHONE_INTENT_RE = re.compile(
    r"\+\s*\d|\b(phone(?:\s+number)?|my\s+number|callback(?:\s+number)?|"
    r"call\s+me|call\s+back|mobile|cell|reach\s+me|"
    r"last\s+\w+\s+digits?|first\s+\w+\s+digits?)\b",
    re.IGNORECASE,
)
_EMAIL_FIELD_RE = re.compile(r"\b(?:e-?mail|email\s+address)\b", re.IGNORECASE)
_PHONE_FIELD_RE = re.compile(
    r"\b(?:phone(?:\s+number)?|callback(?:\s+number)?|mobile|cell)\b",
    re.IGNORECASE,
)
_CORRECTION_INTENT_RE = re.compile(
    r"\b(actually|correction|correct(?:ed)?|change|update|wrong|instead|"
    r"meant|rather)\b",
    re.IGNORECASE,
)
_NATURAL_REJECTION_RE = re.compile(r"\b(?:no|sorry|new)\b", re.IGNORECASE)
_SELF_EMAIL_RE = re.compile(
    r"\b(?:my(?:\s+(?:correct|updated|new))?|"
    r"the\s+(?:correct|updated|new))\s+e-?mail(?:\s+address)?\b",
    re.IGNORECASE,
)
_SELF_PHONE_RE = re.compile(
    r"\b(?:my(?:\s+(?:correct|updated|new))?|"
    r"the\s+(?:correct|updated|new))\s+"
    r"(?:phone(?:\s+number)?|mobile(?:\s+number)?|callback\s+number|number)\b",
    re.IGNORECASE,
)
_BARE_SPOKEN_EMAIL_RE = re.compile(
    r"^\s*[a-z0-9._+\-]+(?:\s+(?:(?:dot|period|underscore|dash|hyphen|plus)\s+"
    r"[a-z0-9]+|zero|one|two|three|four|five|six|seven|eight|nine))*\s+"
    r"at(?:\s+the\s+rate|\s+sign)?\s+[a-z0-9\-]+"
    r"(?:\s+(?:dot|period)\s+[a-z0-9\-]+)+\s*[.!?,;:]*\s*$",
    re.IGNORECASE,
)
_EMAIL_VALID_RE = re.compile(
    r"^[a-z0-9][a-z0-9._+\-]*@[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)+$",
    re.IGNORECASE,
)
_DOMAIN_CORRECTION_RE = re.compile(
    r"\b(?:the\s+)?(?:domain|part\s+after\s+(?:the\s+)?at|after\s+(?:the\s+)?at)"
    r"\s+(?:is\s+actually|should\s+be|is|was)\s+(.+)$",
    re.IGNORECASE,
)
_LOCAL_CORRECTION_RE = re.compile(
    r"\b(?:the\s+)?(?:local\s+part|username|part\s+before\s+(?:the\s+)?at)"
    r"\s+(?:is\s+actually|should\s+be|is|was)\s+(.+)$",
    re.IGNORECASE,
)
_LETTER_CORRECTION_RE = re.compile(
    r"\b(?:the\s+)?(first|second|third|fourth|fifth|sixth|seventh|eighth|"
    r"ninth|tenth|last|\d+(?:st|nd|rd|th)?)\s+(?:letter|character)\s+"
    r"(?:is\s+actually|should\s+be|is|was)\s+([a-z])(?:\s+as\s+in\s+[a-z]+)?\b",
    re.IGNORECASE,
)
_PHONE_SEGMENT_RE = re.compile(
    r"\b(the\s+)?(first|last)\s+(\w+)\s+digits?\s+"
    r"(?:are|is|should\s+be|were)\s+(.+)$",
    re.IGNORECASE,
)
_ORDINALS = {
    "first": 1,
    "second": 2,
    "third": 3,
    "fourth": 4,
    "fifth": 5,
    "sixth": 6,
    "seventh": 7,
    "eighth": 8,
    "ninth": 9,
    "tenth": 10,
}
_COUNTS = {
    **_ORDINALS,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_DIGIT_WORDS = {
    "zero": "0",
    "oh": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
}


def _state(
    kind: CaptureKind,
    status: CaptureStatus,
    *,
    raw: Optional[str] = None,
    normalized: Optional[str] = None,
    attempts: int = 0,
    segments: tuple[str, ...] = (),
    prompt: Optional[str] = None,
    confirmed_at: Optional[datetime] = None,
) -> ContactCaptureState:
    return ContactCaptureState(
        kind=kind,
        status=status,
        raw_value=raw,
        normalized_value=normalized,
        validation_status=status.value,
        confirmed_at=confirmed_at,
        attempts=attempts,
        segments=segments,
        clarification_prompt=prompt,
    )


def _email_segments(value: str) -> tuple[str, str]:
    local, domain = value.lower().split("@", 1)
    return local, domain


def _validated_email(value: str) -> Optional[str]:
    if not value or not _EMAIL_VALID_RE.fullmatch(value):
        return None
    from email_validator import EmailNotValidError, validate_email

    try:
        return validate_email(
            value,
            check_deliverability=False,
        ).normalized.lower()
    except EmailNotValidError:
        return None


def _clean_domain(spoken: str) -> Optional[str]:
    text = str(spoken or "").lower().strip(" .?!,;:")
    text = re.sub(r"\b(?:dot|period)\b", " . ", text)
    text = re.sub(r"\b(?:dash|hyphen)\b", " - ", text)
    text = re.sub(r"\s*\.\s*", ".", text)
    text = re.sub(r"\s*-\s*", "-", text)
    match = re.match(r"[a-z0-9][a-z0-9.\-]*\.[a-z0-9\-]+", text)
    return match.group(0) if match else None


def _clean_local(spoken: str) -> Optional[str]:
    text = str(spoken or "").lower().strip(" .?!,;:")
    text = re.sub(r"\b(?:dot|period)\b", ".", text)
    text = re.sub(r"\bunderscore\b", "_", text)
    text = re.sub(r"\b(?:dash|hyphen)\b", "-", text)
    text = re.sub(r"\s+", "", text)
    return text if re.fullmatch(r"[a-z0-9][a-z0-9._+\-]*", text) else None


def _extract_spelled_email(utterance: str) -> Optional[str]:
    """Assemble an explicitly letter-by-letter local part, conservatively.

    This is the deterministic recovery for the clarification prompt.  It only
    accepts two or more isolated letter/digit units immediately before the
    spoken ``at`` boundary, optionally with phonetic hints (``b as in Bravo``).
    Ordinary multi-word locals remain ambiguous and are never glued together.
    """
    text = str(utterance or "").lower().strip()
    separators = list(
        re.finditer(r"\b(?:at\s+the\s+rate|at\s+sign|at)\b", text)
    )
    unit = r"\b[a-z0-9]\b(?:\s+(?:as\s+in|for)\s+[a-z]+)?"
    for separator in reversed(separators):
        domain = _clean_domain(text[separator.end():])
        if not domain:
            continue
        spelled = re.search(rf"(?P<units>{unit}(?:\s+{unit})+)\s*$", text[:separator.start()])
        if not spelled:
            continue
        # Words in phonetic hints also contain isolated articles in edge cases;
        # capture the leading unit of each spelling group, not every token.
        letters = re.findall(
            r"\b([a-z0-9])\b(?:\s+(?:as\s+in|for)\s+[a-z]+)?",
            spelled.group("units"),
        )
        if len(letters) >= 2:
            return f"{''.join(letters)}@{domain}"
    return None


def _ordinal_index(token: str, length: int) -> Optional[int]:
    low = token.lower()
    if low == "last":
        return length - 1 if length else None
    if low in _ORDINALS:
        value = _ORDINALS[low]
    else:
        match = re.match(r"(\d+)", low)
        value = int(match.group(1)) if match else 0
    index = value - 1
    return index if 0 <= index < length else None


def _digits(spoken: str) -> str:
    tokens = re.findall(r"[a-z]+|\d", str(spoken or "").lower())
    return "".join(_DIGIT_WORDS.get(token, token if token.isdigit() else "") for token in tokens)


def _email_correction(
    previous: ContactCaptureState,
    utterance: str,
) -> Optional[ContactCaptureState]:
    if previous.normalized_value and "@" in previous.normalized_value:
        local, domain = _email_segments(previous.normalized_value)
    elif len(previous.segments) == 2:
        local, domain = previous.segments
    else:
        return None

    letter = _LETTER_CORRECTION_RE.search(utterance)
    if letter:
        index = _ordinal_index(letter.group(1), len(local))
        if index is None:
            return _state(
                "email",
                CaptureStatus.NEEDS_CLARIFICATION,
                raw=utterance,
                normalized=previous.normalized_value,
                segments=(local, domain),
                prompt="Please say which email letter to change.",
            )
        changed = f"{local[:index]}{letter.group(2).lower()}{local[index + 1:]}@{domain}"
        changed = _validated_email(changed)
        if changed is None:
            return _state(
                "email",
                CaptureStatus.INVALID,
                raw=utterance,
                normalized=previous.normalized_value,
                segments=(local, domain),
                prompt="That letter change does not form a valid email; please repeat it.",
            )
        return _state(
            "email",
            CaptureStatus.AWAITING_CONFIRMATION,
            raw=changed,
            normalized=changed,
            segments=_email_segments(changed),
        )

    match = _DOMAIN_CORRECTION_RE.search(utterance)
    if match:
        changed_domain = _clean_domain(match.group(1))
        if changed_domain:
            changed = f"{local}@{changed_domain}"
            changed = _validated_email(changed)
        else:
            changed = None
        if changed:
            return _state(
                "email",
                CaptureStatus.AWAITING_CONFIRMATION,
                raw=changed,
                normalized=changed,
                segments=_email_segments(changed),
            )
        return _state(
            "email",
            CaptureStatus.INVALID,
            raw=utterance,
            normalized=previous.normalized_value,
            segments=(local, domain),
            prompt=(
                "Please say 'the domain is acme dot com', using the correct "
                "domain and ending."
            ),
        )

    match = _LOCAL_CORRECTION_RE.search(utterance)
    if match:
        changed_local = _clean_local(match.group(1))
        if changed_local:
            changed = f"{changed_local}@{domain}"
            changed = _validated_email(changed)
        else:
            changed = None
        if changed:
            return _state(
                "email",
                CaptureStatus.AWAITING_CONFIRMATION,
                raw=changed,
                normalized=changed,
                segments=_email_segments(changed),
            )
        return _state(
            "email",
            CaptureStatus.INVALID,
            raw=utterance,
            normalized=previous.normalized_value,
            segments=(local, domain),
            prompt=(
                "Please say 'the username is', then spell it one character "
                "at a time."
            ),
        )
    return None


def _phone_correction(
    previous: ContactCaptureState,
    utterance: str,
    region: Optional[str],
) -> Optional[ContactCaptureState]:
    base_value = previous.normalized_value or (
        previous.segments[0] if previous.segments else None
    )
    if not base_value:
        return None
    match = _PHONE_SEGMENT_RE.search(utterance)
    if not match:
        return None
    count_token = match.group(3).lower()
    count = _COUNTS.get(count_token)
    if count is None and count_token.isdigit():
        count = int(count_token)
    replacement = _digits(match.group(4))
    digits = re.sub(r"\D", "", base_value)
    if not count or len(replacement) != count or count > len(digits):
        return _state(
            "phone",
            CaptureStatus.NEEDS_CLARIFICATION,
            raw=utterance,
            normalized=previous.normalized_value,
            segments=previous.segments,
            prompt=(
                "Please say 'the first three digits are' or 'the last three "
                "digits are', then give exactly that many replacement digits."
            ),
        )
    if match.group(2).lower() == "first":
        corrected = replacement + digits[count:]
    else:
        corrected = digits[:-count] + replacement
    candidate = f"+{corrected}"
    try:
        normalized = normalize_phone_for_capture(candidate, region)
    except ValueError:
        return _state(
            "phone",
            CaptureStatus.INVALID,
            raw=utterance,
            normalized=previous.normalized_value,
            segments=previous.segments,
            prompt=(
                "That correction is not a valid phone number. Please say 'the "
                "first three digits are' or 'the last three digits are', then "
                "give exactly that many replacement digits."
            ),
        )
    return _state(
        "phone",
        CaptureStatus.AWAITING_CONFIRMATION,
        raw=normalized,
        normalized=normalized,
        segments=(normalized,),
    )


def _extract_normalized(
    kind: CaptureKind,
    utterance: str,
    region: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Return ``(raw_candidate, normalized)`` without guessing boundaries."""
    # Lazy import avoids the scripts package's compatibility exports importing
    # CallState (which itself owns these structured capture records).
    from app.services.scripts.spoken_email_normalizer import (
        extract_email_from_speech,
        extract_phone_from_speech,
    )

    if kind == "email":
        email = extract_email_from_speech(utterance) or _extract_spelled_email(
            utterance
        )
        if email and _EMAIL_VALID_RE.fullmatch(email):
            return email, _validated_email(email)
        return None, None
    raw_phone = extract_phone_from_speech(utterance)
    if not raw_phone:
        # The legacy extractor intentionally rejects any turn containing an
        # email cue so digits in an address are not mistaken for a phone. In
        # the explicit dual-contact mode, recover only a number anchored after
        # an unmistakable phone cue; normalization still goes through the one
        # canonical phonenumbers path below.
        anchored = re.search(
            r"\b(?:phone(?:\s+number)?|my\s+number|callback(?:\s+number)?|"
            r"call\s+me|reach\s+me|mobile|cell)\b[^+\d]{0,20}"
            r"(\+?\d[\d\s().\-]{5,}\d)",
            utterance,
            re.IGNORECASE,
        )
        raw_phone = anchored.group(1) if anchored else None
    if not raw_phone:
        return None, None
    try:
        return raw_phone, normalize_phone_for_capture(raw_phone, region)
    except ValueError:
        return raw_phone, None


def _raw_evidence(
    kind: CaptureKind,
    utterance: str,
    raw_candidate: Optional[str],
) -> Optional[str]:
    """Return the smallest contact-bearing span suitable for audit storage."""
    if kind == "phone":
        return raw_candidate
    literal = re.search(
        r"[a-z0-9][a-z0-9._+\-]*@[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)+",
        utterance,
        re.IGNORECASE,
    )
    if literal:
        return literal.group(0)
    spoken = re.search(
        r"\b[a-z0-9][a-z0-9._+\-]*\s+"
        r"at(?:\s+the\s+rate|\s+sign)?\s+"
        r"[a-z0-9\-]+(?:\s+(?:dot|period)\s+[a-z0-9\-]+)+",
        utterance,
        re.IGNORECASE,
    )
    return spoken.group(0) if spoken else raw_candidate


def _alternatives_conflict(
    kind: CaptureKind,
    utterance: str,
    alternatives: Sequence[str],
    region: Optional[str],
) -> bool:
    candidates: set[str] = set()
    for text in (utterance, *alternatives):
        _raw, normalized = _extract_normalized(kind, str(text or ""), region)
        if normalized:
            candidates.add(normalized)
    return len(candidates) > 1


def has_capture_intent(kind: CaptureKind, utterance: str) -> bool:
    """Whether this turn explicitly enters/switches a contact capture mode."""
    pattern = _EMAIL_INTENT_RE if kind == "email" else _PHONE_INTENT_RE
    return bool(pattern.search(str(utterance or "")))


def _has_whole_value_correction_intent(kind: CaptureKind, utterance: str) -> bool:
    """Recognise a correction without turning incidental contacts into writes."""
    if _CORRECTION_INTENT_RE.search(utterance):
        return True
    owner_pattern = _SELF_EMAIL_RE if kind == "email" else _SELF_PHONE_RE
    return bool(
        owner_pattern.search(utterance) and _NATURAL_REJECTION_RE.search(utterance)
    )


def _has_explicit_confirmed_cancellation(
    kind: CaptureKind,
    utterance: str,
) -> bool:
    """Require both cancellation language and the confirmed field's name.

    A bare "never mind" later in the call can refer to any topic and must not
    erase a previously confirmed contact. Naming email/phone makes withdrawal
    unambiguous and lets the persistence layer revoke the caller-stated row.
    """
    field = _EMAIL_FIELD_RE if kind == "email" else _PHONE_FIELD_RE
    return bool(_CANCEL_RE.search(utterance) and field.search(utterance))


def capture_mode_directive(capture: ContactCaptureState) -> Optional[str]:
    """A provider-neutral instruction for the realtime audible response."""
    if capture.status in {
        CaptureStatus.NEEDS_CLARIFICATION,
        CaptureStatus.INVALID,
    }:
        prompt = capture.clarification_prompt or (
            "Ask the caller to repeat only that contact detail."
        )
        return (
            f"BACKEND CONTACT MODE: {capture.status.value} ({capture.kind}). "
            f"{prompt} Do not confirm, save, or use a contact value yet."
        )
    if (
        capture.status is CaptureStatus.AWAITING_CONFIRMATION
        and capture.normalized_value
    ):
        return (
            f"BACKEND CONTACT MODE: awaiting_confirmation ({capture.kind}). "
            f"Read back exactly {capture.normalized_value!r}, ask for a clear yes or no, "
            "and do not claim it is saved or usable yet."
        )
    if capture.status is CaptureStatus.CANCELLED:
        return (
            f"BACKEND CONTACT MODE: cancelled ({capture.kind}). Stop asking for "
            "or using that value, acknowledge once, and move on."
        )
    if capture.status is CaptureStatus.CONFIRMED:
        return (
            f"BACKEND CONTACT MODE: confirmed ({capture.kind}). The caller has "
            "approved the pending value; stop asking for confirmation and continue."
        )
    return None


def advance_capture(
    previous: Optional[ContactCaptureState],
    *,
    kind: CaptureKind,
    utterance: str,
    readback_issued: bool = False,
    confirmation_verdict: Optional[str] = None,
    phone_region: Optional[str] = None,
    transcript_confidence: Optional[float] = None,
    transcript_alternatives: Sequence[str] = (),
    explicit_reask: bool = False,
    mode_active: bool = False,
    now: Optional[datetime] = None,
) -> Optional[ContactCaptureState]:
    """Advance one contact field from final transcript evidence.

    ``confidence=None`` is deliberately neutral.  Flux uses ``None`` because it
    does not expose recognition confidence; treating absence as low confidence
    would put every Flux contact into a permanent clarification loop.
    """
    if previous is not None and previous.kind != kind:
        raise ValueError("contact capture kind cannot change mid-state")
    text = str(utterance or "").strip()
    if not text:
        return previous

    # A confirmed fact is sticky. Generic cancellations, a low-confidence
    # repeat, or a later address merely mentioned in conversation cannot
    # demote/replace it. Only an explicit whole-value correction or one of the
    # segment-correction forms may reopen confirmation.
    if previous is not None and previous.status is CaptureStatus.CONFIRMED:
        if _has_explicit_confirmed_cancellation(kind, text):
            return _state(kind, CaptureStatus.CANCELLED, raw=text)
        correction = (
            _email_correction(previous, text)
            if kind == "email"
            else _phone_correction(previous, text, phone_region)
        )
        if correction is not None:
            return correction
        _raw, replacement = _extract_normalized(kind, text, phone_region)
        if not (
            replacement
            and replacement != previous.normalized_value
            and (
                _has_whole_value_correction_intent(kind, text)
                or (
                    kind == "email"
                    and "@" not in text
                    and _BARE_SPOKEN_EMAIL_RE.fullmatch(text)
                )
            )
        ):
            return previous
        previous = None

    if previous is not None and _CANCEL_RE.search(text):
        return _state(kind, CaptureStatus.CANCELLED, raw=text)

    if previous is not None:
        correction = (
            _email_correction(previous, text)
            if kind == "email"
            else _phone_correction(previous, text, phone_region)
        )
        if correction is not None:
            return correction

    raw_candidate, normalized = _extract_normalized(kind, text, phone_region)
    if kind == "phone" and mode_active and raw_candidate is None:
        from app.services.scripts.spoken_email_normalizer import (
            extract_phone_from_speech,
        )

        # The preceding agent turn already established phone mode. Prefixing
        # that trusted context lets the canonical speech parser expand "oh",
        # digit words and double/triple forms without accepting arbitrary bare
        # numbers elsewhere in the conversation.
        raw_candidate = extract_phone_from_speech(f"phone number {text}")
        if raw_candidate:
            try:
                normalized = normalize_phone_for_capture(
                    raw_candidate,
                    phone_region,
                )
            except ValueError:
                normalized = None
    if kind == "phone" and mode_active and raw_candidate is None:
        bare = re.search(r"\+?\d[\d\s().\-]{5,}\d", text)
        if bare:
            raw_candidate = bare.group(0)
            try:
                normalized = normalize_phone_for_capture(
                    raw_candidate,
                    phone_region,
                )
            except ValueError:
                normalized = None
    audit_raw = _raw_evidence(kind, text, raw_candidate)
    has_intent = has_capture_intent(kind, text)

    # Entering capture is explicit. The legacy phone extractor quite
    # reasonably recognises "number" for import-style parsing, but a live
    # conversation also contains project, invoice and reference numbers. A
    # candidate alone must not create a contact mode unless the caller used a
    # contact cue (or supplied a self-describing +E.164 value).
    if previous is None and not has_intent and not mode_active:
        return None

    if normalized:
        segments = _email_segments(normalized) if kind == "email" else (normalized,)
        if _alternatives_conflict(
            kind, text, transcript_alternatives, phone_region
        ):
            return _state(
                kind,
                CaptureStatus.NEEDS_CLARIFICATION,
                raw=audit_raw,
                normalized=normalized,
                segments=segments,
                prompt="I heard two different versions. Please repeat just that contact detail.",
            )
        # None is not below a threshold. Only an actual numeric score can trip
        # this evidence gate; providers without the signal use alternatives or
        # explicit_reask instead.
        try:
            low_confidence = (
                transcript_confidence is not None
                and float(transcript_confidence) < _LOW_CONFIDENCE
            )
        except (TypeError, ValueError):
            low_confidence = False
        if low_confidence or explicit_reask:
            return _state(
                kind,
                CaptureStatus.NEEDS_CLARIFICATION,
                raw=audit_raw,
                normalized=normalized,
                segments=segments,
                prompt="Please repeat that contact detail slowly so I can verify it.",
            )
        if (
            previous is None
            or normalized != previous.normalized_value
            or previous.status in {
                CaptureStatus.NEEDS_CLARIFICATION,
                CaptureStatus.INVALID,
                CaptureStatus.CANCELLED,
            }
        ):
            return _state(
                kind,
                CaptureStatus.AWAITING_CONFIRMATION,
                raw=audit_raw,
                normalized=normalized,
                segments=segments,
            )

    # A phone candidate without '+' and without region is structurally
    # incomplete, not invalid. Ask for country context instead of guessing US.
    if kind == "phone" and raw_candidate and normalized is None:
        missing_region = not raw_candidate.strip().startswith("+") and not phone_region
        return _state(
            kind,
            (
                CaptureStatus.NEEDS_CLARIFICATION
                if missing_region
                else CaptureStatus.INVALID
            ),
            raw=audit_raw,
            prompt=(
                "Please repeat the complete phone number beginning with + and its country code."
                if missing_region
                else "That does not appear to be a valid phone number; please repeat it."
            ),
        )

    if previous is None:
        # Contact-shaped but unparseable input must be visible to the prompt. A
        # multi-word spoken email is ambiguous (spell/segment clarification); a
        # malformed written address is invalid.
        if has_intent:
            status = (
                CaptureStatus.INVALID
                if kind == "email" and ("email" in text.lower() or "@" in text)
                else CaptureStatus.NEEDS_CLARIFICATION
            )
            return _state(
                kind,
                status,
                raw=text,
                prompt=(
                    "Please spell the email one letter at a time, then say at and the domain."
                    if kind == "email"
                    else "Please repeat the phone number one digit at a time."
                ),
            )
        return None

    if (
        previous.status is CaptureStatus.AWAITING_CONFIRMATION
        and readback_issued
    ):
        verdict = str(confirmation_verdict or "unclear").lower()
        if verdict == "affirm":
            stamp = now or datetime.now(timezone.utc)
            return replace(
                previous,
                status=CaptureStatus.CONFIRMED,
                validation_status=CaptureStatus.CONFIRMED.value,
                confirmed_at=stamp,
                clarification_prompt=None,
            )
        if verdict == "reject":
            return replace(
                previous,
                status=CaptureStatus.NEEDS_CLARIFICATION,
                validation_status=CaptureStatus.NEEDS_CLARIFICATION.value,
                normalized_value=None,
                confirmed_at=None,
                attempts=0,
                clarification_prompt="Please correct only the part I got wrong.",
            )
        attempts = previous.attempts + 1
        if attempts >= MAX_CONFIRMATION_ATTEMPTS:
            return replace(
                previous,
                status=CaptureStatus.NEEDS_CLARIFICATION,
                validation_status=CaptureStatus.NEEDS_CLARIFICATION.value,
                confirmed_at=None,
                attempts=attempts,
                clarification_prompt=(
                    "Please ask them to say 'the username is' and spell it slowly, "
                    "one character at a time, or repeat the complete email including "
                    "at and the domain."
                    if kind == "email"
                    else "Please ask for the complete plus-prefixed phone number digit by "
                    "digit, or ask them to say 'the first three digits are' or 'the last "
                    "three digits are'."
                ),
            )
        return replace(previous, attempts=attempts)

    # A pending value may receive incidental words such as "email" in an
    # otherwise ambiguous reply. Once the read-back gate above has had first
    # refusal, do not erase that candidate merely because no new full address
    # could be parsed. For an already confirmed value, an unparseable mention
    # is likewise not evidence of a correction.
    if has_intent and previous.status not in {
        CaptureStatus.CONFIRMED,
        CaptureStatus.AWAITING_CONFIRMATION,
    }:
        return replace(
            previous,
            status=CaptureStatus.NEEDS_CLARIFICATION,
            validation_status=CaptureStatus.NEEDS_CLARIFICATION.value,
            confirmed_at=None,
            clarification_prompt=(
                "Please spell the email one letter at a time, then say at and the domain."
                if kind == "email"
                else "Please repeat the phone number one digit at a time."
            ),
        )

    return previous
