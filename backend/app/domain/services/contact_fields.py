"""The canonical contact model — one definition, read by everything.

goals.md §11. CSV import, row validation, the contact form, the table columns
and the agent's context all need to agree on what a contact IS. When that
definition lives in four places it drifts, and the symptom is a column that
imports fine but never reaches the agent, or a field the form can set that the
table cannot show.

So this module is the only place the field list exists. Everything else asks.

WHY ALIASES ARE PART OF THE DEFINITION
---------------------------------------
Real spreadsheets do not use our column names. They say "Mobile", "Cell",
"Phone Number", "Company Name", "Job Title", "Position". §11 asks for column
mapping during import; the honest version of that is to recognise what people
actually type, and only fall back to asking when we genuinely cannot tell.

An unrecognised column is NEVER dropped — it lands in ``custom_fields`` intact,
because losing a column silently is worse than not understanding it.

VALIDATION IS PER ROW, NOT PER FILE
------------------------------------
§11 asks for row-level validation failures. A 4,000-row import with nine bad
phone numbers should load 3,991 contacts and tell you about the nine, not
reject the file. Every validator here returns a reason string rather than
raising, so the importer can keep going and report.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Callable, Optional

# ── validators ──────────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")
_PHONE_CHARS_RE = re.compile(r"[^\d+]")
_IANA_RE = re.compile(r"^[A-Za-z_]+/[A-Za-z_+\-/]+$")

CONTACT_METHODS = ("phone", "email", "sms", "whatsapp")


def _valid_email(v: str) -> Optional[str]:
    return None if _EMAIL_RE.match(v) else "not a valid email address"


def _valid_phone(v: str) -> Optional[str]:
    digits = _PHONE_CHARS_RE.sub("", v)
    core = digits.lstrip("+")
    if not core.isdigit():
        return "phone number contains no digits"
    # Deliberately permissive: E.164 allows 7-15 digits and national formats
    # vary. Rejecting a real number is worse than accepting an odd one, because
    # the dialler will surface a bad number on the first attempt anyway.
    if not (6 <= len(core) <= 16):
        return f"phone number has {len(core)} digits, expected 6-16"
    return None


def _valid_timezone(v: str) -> Optional[str]:
    if not _IANA_RE.match(v):
        return "expected an IANA timezone such as Europe/London"
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(v)
    except Exception:  # noqa: BLE001 — unknown zone, not a crash
        return f"unknown timezone {v!r}"
    return None


def _valid_contact_method(v: str) -> Optional[str]:
    return (
        None if v.lower() in CONTACT_METHODS
        else f"expected one of {', '.join(CONTACT_METHODS)}"
    )


def _valid_bool(v: str) -> Optional[str]:
    return None if v.strip().lower() in _TRUTHY | _FALSY else "expected yes/no or true/false"


_TRUTHY = {"1", "true", "t", "yes", "y", "do not call", "dnc"}
_FALSY = {"0", "false", "f", "no", "n", ""}


def coerce_bool(v: str) -> bool:
    return v.strip().lower() in _TRUTHY


# ── the model ───────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ContactField:
    key: str
    label: str
    column: Optional[str]          # leads column; None => lives in custom_fields
    field_type: str = "text"
    aliases: tuple[str, ...] = ()
    validator: Optional[Callable[[str], Optional[str]]] = None
    # Whether the agent may be told this on a call. Off for things that are
    # operational rather than conversational — reciting someone's timezone at
    # them is not useful, but calling at a civil hour is.
    agent_usable: bool = True
    max_len: int = 255
    # What this column looks like filled in. It exists HERE, on the field,
    # because the downloadable template is generated from this tuple — a
    # hand-written example row drifts the moment a field is added, and the
    # symptom is a customer filling in a column the importer no longer reads.
    example: str = ""


CONTACT_FIELDS: tuple[ContactField, ...] = (
    ContactField(
        "phone_number", "Phone number", "phone_number", "phone",
        aliases=("phone", "telephone", "tel", "number", "primary phone",
                 "contact number", "msisdn"),
        validator=_valid_phone, max_len=32, example="+447700900123",
    ),
    ContactField(
        "mobile_number", "Mobile", None, "phone",
        aliases=("mobile", "cell", "cellphone", "cell phone", "mobile phone"),
        validator=_valid_phone, max_len=32, example="+447700900124",
    ),
    ContactField(
        "business_number", "Business number", "business_number", "phone",
        aliases=("business phone", "work phone", "office", "office phone",
                 "work number", "landline"),
        validator=_valid_phone, max_len=32, example="+442079460000",
    ),
    ContactField(
        "first_name", "First name", "first_name",
        aliases=("firstname", "given name", "forename", "fname"),
        example="Sian",
    ),
    ContactField(
        "last_name", "Last name", "last_name",
        aliases=("lastname", "surname", "family name", "lname"),
        example="Roberts",
    ),
    # Read/derive-only in the database. Imports and manual forms may supply a
    # full name, but the write paths split it into first_name/last_name so the
    # generated leads.full_name column can never drift from its parts.
    ContactField(
        "full_name", "Full name", None,
        aliases=("fullname", "contact name", "customer name", "lead name"),
        # Deliberately blank in the template: every write path derives it from
        # first_name/last_name, so a filled-in example would teach people to
        # supply a value we then ignore.
        max_len=511, example="",
    ),
    ContactField(
        "email", "Email", "email", "email",
        aliases=("email address", "e-mail", "mail"),
        validator=_valid_email, example="sian.roberts@buildwright.co.uk",
    ),
    ContactField(
        "company_name", "Company", "company_name",
        aliases=("company", "organisation", "organization", "business",
                 "business name", "employer", "account"),
        example="BuildWright Roofing",
    ),
    ContactField(
        "job_title", "Job title", "job_title",
        aliases=("title", "role", "position", "job", "job role"),
        example="Quantity Surveyor",
    ),
    ContactField(
        "best_time_to_call", "Best time to call", "best_time_to_call",
        aliases=("best time", "preferred time", "call time", "availability",
                 "calling hours", "call window", "calling time"),
        max_len=64, example="Mon-Fri 9am-11am",
    ),
    ContactField(
        "timezone", "Timezone", "timezone",
        aliases=("time zone", "tz"),
        validator=_valid_timezone,
        # Operational, not conversational: it decides WHEN we dial, and the
        # agent has no reason to mention it.
        agent_usable=False, max_len=64, example="Europe/London",
    ),
    ContactField(
        "calling_notes", "Calling notes", "calling_notes", "notes",
        aliases=("notes", "note", "call notes", "contact notes", "comments",
                 "remarks", "background"),
        max_len=4000,
        example="Call back after the tender closes",
    ),
    ContactField(
        "preferred_contact_method", "Preferred contact method",
        "preferred_contact_method",
        aliases=("preferred contact", "contact method", "contact preference"),
        validator=_valid_contact_method, agent_usable=False, max_len=32,
        example="phone",
    ),
    ContactField(
        "do_not_call", "Do not call", "do_not_call", "boolean",
        aliases=("dnc", "do-not-call", "opt out", "opted out", "unsubscribe"),
        validator=_valid_bool, example="no",
        # NEVER shown to the agent. A do-not-call contact should not be dialled
        # at all; putting the flag in the prompt would invite the model to
        # mention it, which is the worst possible handling.
        agent_usable=False,
    ),
)

BY_KEY = {f.key: f for f in CONTACT_FIELDS}


def normalise_header(header: str) -> str:
    """Collapse spacing, underscores and hyphens so "E-Mail", "e_mail" and
    "E Mail" are all the same thing."""
    return re.sub(r"[\s_\-]+", " ", (header or "").strip().lower())


# alias -> key, built once.
#
# EVERY key is normalised on the way IN, using the same function lookups use.
# Storing raw aliases here was a real bug: "e-mail" went into the index
# verbatim while a lookup for "E-Mail" normalised to "e mail", so no alias
# containing a hyphen could ever match — silently, because an unmatched header
# just falls through to custom_fields and looks like a column we chose not to
# understand.
_ALIAS_INDEX: dict[str, str] = {}
for _f in CONTACT_FIELDS:
    for _candidate in (_f.key, *_f.aliases):
        _ALIAS_INDEX[normalise_header(_candidate)] = _f.key


def map_column(header: str) -> Optional[str]:
    """Best guess at which canonical field a spreadsheet column means.

    Returns None when we genuinely cannot tell — the importer then keeps the
    column verbatim in custom_fields rather than discarding it, and the UI can
    offer a manual mapping for it.
    """
    return _ALIAS_INDEX.get(normalise_header(header))


def map_headers(headers: list[str]) -> dict[str, Optional[str]]:
    """Header -> canonical key (or None). The shape the import UI needs to
    render its mapping step: every column, with our suggestion beside it."""
    return {h: map_column(h) for h in headers}


@dataclass
class RowIssue:
    row: int
    field: str
    value: str
    reason: str


def validate_row(values: dict[str, str], row_number: int) -> list[RowIssue]:
    """Validate one mapped row. Returns issues; never raises.

    §11 wants row-level failures reported, which only works if a bad row does
    not abort the file.
    """
    issues: list[RowIssue] = []
    for key, raw in values.items():
        f = BY_KEY.get(key)
        if f is None or raw is None:
            continue
        v = str(raw).strip()
        if not v:
            continue
        if len(v) > f.max_len:
            issues.append(RowIssue(row_number, key, v[:40],
                                   f"longer than {f.max_len} characters"))
            continue
        if f.validator:
            why = f.validator(v)
            if why:
                issues.append(RowIssue(row_number, key, v[:40], why))
    return issues


def dedupe_key(values: dict[str, str]) -> Optional[str]:
    """What makes two rows the same person.

    Phone first, because it is what we dial and what the platform keys on.
    Email second, for a row with no number. A name alone is NOT a duplicate
    key — two different Michael Smiths at two companies are two contacts, and
    merging them would silently destroy one.
    """
    phone = (values.get("phone_number") or values.get("mobile_number") or "").strip()
    if phone:
        digits = _PHONE_CHARS_RE.sub("", phone).lstrip("+")
        if digits:
            # Last 9 digits: tolerates +44 / 0044 / 0 prefixes for one number.
            return f"tel:{digits[-9:]}"
    email = (values.get("email") or "").strip().lower()
    return f"mail:{email}" if email else None


def agent_context_fields(values: dict[str, object]) -> dict[str, str]:
    """The subset of a contact the agent may be told about.

    Filtered by ``agent_usable``, so do_not_call, timezone and contact
    preference never reach the prompt. Empty values are dropped rather than
    sent as blanks, which would spend prompt tokens telling the model nothing.
    """
    out: dict[str, str] = {}
    for f in CONTACT_FIELDS:
        if not f.agent_usable:
            continue
        raw = values.get(f.key)
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            out[f.key] = text
    return out


def csv_template_headers() -> list[str]:
    """The download template. Canonical names only — an import using this file
    maps cleanly with no guessing at all."""
    return [f.key for f in CONTACT_FIELDS]


def csv_template_example_row() -> list[str]:
    """One filled-in row, so a customer can see the SHAPE of each column rather
    than guess what "best_time_to_call" wants."""
    return [f.example for f in CONTACT_FIELDS]


TEMPLATE_FILENAME = "talklee-contacts-template.csv"


def csv_template_csv() -> str:
    """The whole downloadable template: header row + one example row.

    Generated from CONTACT_FIELDS, never hand-written, so adding a field to the
    registry updates the template, the importer and the validator in one move.
    ``csv.writer`` rather than ``",".join`` because an example value is allowed
    to contain a comma and must then be quoted, exactly as a real export would.
    """
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(csv_template_headers())
    writer.writerow(csv_template_example_row())
    return buf.getvalue()
