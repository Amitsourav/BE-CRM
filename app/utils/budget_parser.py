from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Optional

# ---- Why this exists ----------------------------------------------------
# Admitverse `leads.budget` is free-form text a counsellor types — study
# abroad budgets come in many currencies: "50 lakh", "2 cr", "£18,000",
# "$30,000", "12000 GBP", "₹25,00,000". The Kanban budget filter and
# reports need a real number AND the currency it's in (you can't compare
# 18,000 GBP to 50,00,000 INR on one axis). This parser returns a
# (amount, currency) pair:
#
#     "£18,000"        → (Decimal('18000'),   'GBP')
#     "$30,000"        → (Decimal('30000'),   'USD')
#     "12000 GBP"      → (Decimal('12000'),   'GBP')
#     "50 lakh"        → (Decimal('5000000'), 'INR')   # lakhs → rupees
#     "2 cr"           → (Decimal('20000000'),'INR')
#     "25,00,000"      → (Decimal('2500000'), 'INR')
#     "25"             → (Decimal('2500000'), 'INR')   # bare → lakhs (INR)
#     ""               → (None, None)
#     "n/a"            → (None, None)
#
# Amount is stored in each currency's base unit (rupees for INR, pounds
# for GBP, etc.) so budget_min/max filtering compares like-for-like within
# a currency. Never raises — returns (None, None) when unparseable.
# -------------------------------------------------------------------------

_NULL_TOKENS = {
    "", "na", "n/a", "n.a", "n.a.", "tbd", "tba", "unknown", "?",
    "none", "null", "nil", "-", "--", "depends", "not sure",
    "to be decided", "flexible",
}

# Currency detection: symbol or word → ISO code.
_CURRENCY_TOKENS: list[tuple[str, str]] = [
    ("£", "GBP"), ("gbp", "GBP"), ("pound", "GBP"),
    ("$", "USD"), ("usd", "USD"), ("dollar", "USD"),
    ("€", "EUR"), ("eur", "EUR"), ("euro", "EUR"),
    ("₹", "INR"), ("inr", "INR"), ("rupee", "INR"), ("rs", "INR"),
    ("aud", "AUD"), ("cad", "CAD"),
]

# INR magnitude words → rupee multiplier.
_INR_UNITS: dict[str, Decimal] = {
    "lakh": Decimal("100000"), "lac": Decimal("100000"),
    "l": Decimal("100000"),
    "cr": Decimal("10000000"), "crore": Decimal("10000000"),
    "cror": Decimal("10000000"),
}

# Generic magnitude words for non-INR currencies.
_GENERIC_UNITS: dict[str, Decimal] = {
    "k": Decimal("1000"), "thousand": Decimal("1000"),
    "m": Decimal("1000000"), "mn": Decimal("1000000"),
    "million": Decimal("1000000"),
}

_NUM_UNIT_RE = re.compile(
    r"(?P<num>[\d][\d,\.]*)\s*"
    r"(?P<unit>lakhs?|lacs?|crores?|crs?|cror|thousand|million|mn|[kml])?",
    re.IGNORECASE,
)


def _detect_currency(text: str) -> Optional[str]:
    for token, code in _CURRENCY_TOKENS:
        if token in text:
            return code
    # lakh/cr strongly imply INR even with no explicit symbol
    if re.search(r"\b(lakhs?|lacs?|crores?|crs?|cror)\b", text) or re.search(r"\dl\b", text):
        return "INR"
    return None


def _clean_number(num_str: str) -> Optional[Decimal]:
    cleaned = num_str.replace(",", "")
    if cleaned.count(".") > 1:
        return None
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def parse_budget(value: Optional[str]) -> tuple[Optional[Decimal], Optional[str]]:
    """Parse a free-text budget into (amount, currency_code). Amount is in
    the currency's base unit (rupees for INR). Returns (None, None) for
    empty/unparseable input. Never raises.
    """
    if value is None:
        return None, None
    text = str(value).strip().lower()
    if not text or text in _NULL_TOKENS:
        return None, None

    currency = _detect_currency(text) or "INR"

    # Strip currency symbols/words so the number regex is clean.
    text = text.replace("₹", " ").replace("£", " ").replace("$", " ").replace("€", " ")
    text = re.sub(r"\b(inr|gbp|usd|eur|aud|cad|rupees?|rs|pounds?|dollars?|euros?)\b", " ", text)

    match = _NUM_UNIT_RE.search(text)
    if not match:
        return None, None
    num = _clean_number(match.group("num"))
    if num is None or num < 0:
        return None, None

    unit_raw = (match.group("unit") or "").lower().strip(". ")

    if currency == "INR":
        mult = _INR_UNITS.get(unit_raw)
        if mult is not None:
            amount = num * mult
        else:
            # No INR unit word. Disambiguate by magnitude:
            #   < 1000  → assume lakhs ("25" = 25 lakh = 2,500,000)
            #   >= 1000 → assume the number is already rupees
            amount = (num * Decimal("100000")) if num < Decimal("1000") else num
    else:
        mult = _GENERIC_UNITS.get(unit_raw)
        amount = num * mult if mult is not None else num

    return amount.quantize(Decimal("0.01")), currency
