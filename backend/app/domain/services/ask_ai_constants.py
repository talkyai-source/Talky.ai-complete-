"""
Ask AI pure constants — no app imports.

Kept in a separate module so voice_pipeline_service.py can import these
without pulling in ask_ai_session_config, which imports VoiceSessionConfig
from voice_orchestrator, which in turn imports voice_pipeline_service —
a circular dependency that crashes the server at startup.
"""
from decimal import Decimal
from typing import Optional

# The demo always receives this small fact sheet; the model decides relevance.
# Plans and prices are not in it: they come from the live price list read at
# session start (QA F11-05), so the demo never quotes a remembered price.
TALKY_PRODUCT_INFO = (
    "Talk-Lee is an AI voice-calling platform for businesses: natural-sounding "
    "AI agents that make and answer phone calls — for outbound sales, lead "
    "qualification, appointment booking, customer support, reception, and "
    "follow-up calls.\n"
    "How it works: you create a campaign, upload your own business knowledge, "
    "pick a voice, and add contacts (type them, upload a CSV, or paste a list). "
    "The agents then call or pick up, talk naturally, answer from your "
    "knowledge, capture leads, and book follow-ups.\n"
    "Key features: lifelike voices with different accents plus custom voice "
    "cloning; a per-campaign knowledge base so agents answer accurately from "
    "your own info; smart dialing with timezone-aware calling windows, automatic "
    "retries, and do-not-call / opt-out compliance; and a dashboard with live "
    "call status, recordings, full transcripts, lead tracking, and analytics. "
    "Bring your own phone number."
)

_NO_PRICE_LIST = (
    "Prices: the current plans and prices are on the pricing page. Don't quote "
    "any price, discount or minute allowance; point the caller to that page."
)

_CURRENCY_SYMBOLS = {"usd": "$", "gbp": "£", "eur": "€"}


def _money(amount_minor: int, currency: str, exponent: Optional[int]) -> str:
    places = 2 if exponent is None else int(exponent)
    text = f"{Decimal(int(amount_minor)) / (Decimal(10) ** places):,.{places}f}"
    if places and text.endswith("." + "0" * places):
        text = text[: -(places + 1)]
    symbol = _CURRENCY_SYMBOLS.get(str(currency).lower())
    return f"{symbol}{text}" if symbol else f"{text} {str(currency).upper()}"


def format_price_list(plans: list, payg: Optional[dict]) -> Optional[str]:
    """The live price list in words, from what the public pricing page shows:
    the plan catalogue (`/plans`) and the pay-as-you-go bundles
    (`/plans/payg`). A price checkout can't take is left out. None when
    nothing is on sale."""
    parts = []
    for plan in plans or []:
        for option in plan.get("price_options") or []:
            if not option.get("active", True) or not option.get("checkout_available"):
                continue
            count = int(option.get("interval_count") or 1)
            interval = option.get("interval") or "month"
            per = f"a {interval}" if count == 1 else f"every {count} {interval}s"
            details = []
            if plan.get("minutes"):
                details.append(f"{int(plan['minutes']):,} minutes")
            if plan.get("agents"):
                agents = int(plan["agents"])
                details.append(f"{agents} agent{'' if agents == 1 else 's'}")
            amount = _money(option["amount_minor"], option["currency"], option.get("currency_exponent"))
            parts.append(
                f"{plan['name']} {amount} {per}" + (f" ({', '.join(details)})" if details else "")
            )
    for package in (payg or {}).get("packages") or []:
        amount = _money(package["price_cents"], package["currency"], package.get("currency_exponent"))
        line = f"pay as you go: {int(package['minutes']):,} minutes for {amount}"
        per_minute = package.get("price_per_minute_cents")
        if per_minute and str(package["currency"]).lower() == "usd":
            line += f" ({Decimal(str(per_minute)).normalize():f} cents a minute)"
        parts.append(line)
    if not parts:
        return None
    return "Current prices (the live price list; quote only these): " + "; ".join(parts) + "."


def ask_ai_fact_sheet(price_list: Optional[str]) -> str:
    """The demo's fact sheet with the live price list, or a pointer to the
    pricing page when that list couldn't be read: never a remembered price."""
    return f"{TALKY_PRODUCT_INFO}\n{price_list or _NO_PRICE_LIST}"
