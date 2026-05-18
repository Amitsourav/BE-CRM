from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Optional

# ---- Why this exists ----------------------------------------------------
# `leads.loan_amount` is free-form text the telecaller types — "25 lakh",
# "1.5cr", "5,00,000 INR", "₹2500000", "around 30L". Reports and the
# Kanban budget filter need a real number. This parser converts each of
# those into a Decimal expressed in *lakhs*:
#
#     "25 lakh"         → 25.00
#     "1.5cr"           → 150.00
#     "500000"          → 5.00       (raw rupees → 5 lakh)
#     "5,00,000"        → 5.00
#     "₹25,00,000"      → 25.00
#     "around 30 lakh"  → 30.00      (extracts the first number + unit)
#     ""                → None       (genuinely unknown)
#     "n/a" / "tbd"     → None
#
# Output is None for anything we can't confidently interpret — never
# raises. Callers (CSV import, lead create/update, backfill) treat None
# as "leave the loan_amount_lakh column unchanged".
# -------------------------------------------------------------------------

_NULL_TOKENS = {
    "", "na", "n/a", "n.a", "n.a.", "tbd", "tba", "unknown", "?",
    "none", "null", "nil", "-", "--", "ask brother", "depends",
    "not sure", "to be decided",
}

# Map unit-suffixes (lowercased, stripped of punctuation) to lakh
# multipliers. e.g. 1 crore = 100 lakh; 1 K = 0.01 lakh.
_UNITS: dict[str, Decimal] = {
    "lakh": Decimal("1"),
    "lac":  Decimal("1"),
    "l":    Decimal("1"),
    "cr":   Decimal("100"),
    "crore": Decimal("100"),
    "cror": Decimal("100"),
    "k":    Decimal("0.01"),
    "thousand": Decimal("0.01"),
    "m":    Decimal("10"),
    "mn":   Decimal("10"),
    "million": Decimal("10"),
}

# Pull the *first* number + optional unit out of free text. Catches:
#   "25 lakh", "1.5cr", "₹500000", "around 30 L", "5,00,000 INR"
_NUM_UNIT_RE = re.compile(
    r"(?P<num>[\d][\d,\.]*)\s*(?P<unit>lakhs?|lacs?|crores?|crs?|cror|"
    r"thousand|million|mn|[kmlcr])?",
    re.IGNORECASE,
)


def _clean_number(num_str: str) -> Optional[Decimal]:
    # Indian comma grouping like "5,00,000" or US "500,000" — strip all
    # commas, keep one decimal point. "1.5" stays "1.5".
    cleaned = num_str.replace(",", "")
    if cleaned.count(".") > 1:
        return None
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None


def parse_loan_amount(value: Optional[str]) -> Optional[Decimal]:
    """Convert a free-text loan amount into lakhs. Returns None when
    the input is empty, a known null-token, or cannot be parsed
    confidently. Never raises.

    Examples:
        parse_loan_amount("25 lakh")    → Decimal('25')
        parse_loan_amount("1.5cr")      → Decimal('150')
        parse_loan_amount("500000")     → Decimal('5')
        parse_loan_amount("")           → None
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text or text in _NULL_TOKENS:
        return None
    # Strip currency symbols + standalone "INR"/"Rs"/"Rupees" tokens
    text = text.replace("₹", " ").replace("rs.", " ").replace("rs ", " ")
    text = re.sub(r"\b(inr|rupees?|rs)\b", " ", text)

    match = _NUM_UNIT_RE.search(text)
    if not match:
        return None
    num = _clean_number(match.group("num"))
    if num is None or num < 0:
        return None

    unit_raw = (match.group("unit") or "").lower().strip(". ")
    multiplier = _UNITS.get(unit_raw)

    if multiplier is not None:
        result = num * multiplier
    else:
        # No explicit unit. Disambiguate by magnitude:
        #   < 1000           → assume lakhs ("25" = 25 lakh)
        #   1000 – 999999    → assume rupees ("500000" = 5 lakh)
        #   >= 1,000,000     → assume rupees ("25000000" = 250 lakh)
        # This matches how counsellors actually write values in your data.
        if num < Decimal("1000"):
            result = num
        else:
            result = num / Decimal("100000")

    # Sanity cap: anything > 10,000 lakh (₹100 cr) is almost certainly a
    # data-entry slip ("50000000" typed where they meant "500000"). We
    # still record it — better to keep the data and let the user fix it
    # than silently drop. But round to 2 decimals.
    return result.quantize(Decimal("0.01"))
