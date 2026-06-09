from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional


_TWO_PLACES = Decimal("0.01")


@dataclass
class TaxBreakup:
    """Result of the GST decision + math. tax_split is the canonical
    routing signal (`cgst_sgst` or `igst`) stored on the invoice row
    so reports can filter without re-deriving from state codes.
    """
    cgst_amount: Decimal
    sgst_amount: Decimal
    igst_amount: Decimal
    total_tax: Decimal
    grand_total: Decimal
    tax_split: str  # 'cgst_sgst' or 'igst'


def _round2(d: Decimal) -> Decimal:
    """Bank-style ROUND_HALF_UP to 2 places (paise precision). Used
    on every Decimal that lands in a money column so JSON round-trips
    don't introduce 0.005 phantom paise.
    """
    return d.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def derive_customer_state_code(
    *, customer_state_code: Optional[str], customer_gstin: Optional[str]
) -> Optional[str]:
    """State code precedence:
      1. explicit `customer_state_code` (allows override even with GSTIN)
      2. derived from `customer_gstin[0:2]` if GSTIN supplied
      3. None  → treated as inter-state (IGST) by `compute_tax`

    Lives separately so the prefill endpoint can use it too without
    pulling in the entire tax math.
    """
    if customer_state_code:
        return customer_state_code
    if customer_gstin and len(customer_gstin) >= 2:
        return customer_gstin[:2]
    return None


def compute_tax(
    *,
    subtotal: Decimal,
    fmc_state_code: str,
    customer_state_code: Optional[str],
    customer_gstin: Optional[str],
    tax_rate: Decimal = Decimal("18.00"),
) -> TaxBreakup:
    """Decide CGST+SGST vs IGST split and compute the rupee amounts.

    Decision rules (matches the requirement spec):
      1. No customer GSTIN → IGST (B2C / unregistered default per FMC policy)
      2. Customer GSTIN + customer_state == fmc_state → CGST+SGST (intra-state)
      3. Otherwise → IGST (inter-state)

    Math:
      • Compute CGST and SGST as `subtotal * (rate/2) / 100`, then
        force `sgst = cgst` to avoid 0.01 rounding drift between halves.
      • Single-rate IGST = `subtotal * rate / 100`.
      • All rounded once at the end via ROUND_HALF_UP to 0.01.
    """
    subtotal = _round2(subtotal)

    # Resolve state code if caller didn't already
    if customer_state_code is None:
        customer_state_code = derive_customer_state_code(
            customer_state_code=None, customer_gstin=customer_gstin,
        )

    # Decide split
    if not customer_gstin:
        split = "igst"
    elif customer_state_code and customer_state_code == fmc_state_code:
        split = "cgst_sgst"
    else:
        split = "igst"

    if split == "cgst_sgst":
        half_rate = tax_rate / Decimal("2")
        cgst = _round2(subtotal * half_rate / Decimal("100"))
        sgst = cgst  # force equal to avoid drift
        igst = Decimal("0.00")
    else:
        igst = _round2(subtotal * tax_rate / Decimal("100"))
        cgst = Decimal("0.00")
        sgst = Decimal("0.00")

    total_tax = _round2(cgst + sgst + igst)
    grand_total = _round2(subtotal + total_tax)
    return TaxBreakup(
        cgst_amount=cgst,
        sgst_amount=sgst,
        igst_amount=igst,
        total_tax=total_tax,
        grand_total=grand_total,
        tax_split=split,
    )


def compute_line_amounts(line_items: list[dict]) -> tuple[list[dict], Decimal]:
    """For each line item, compute `amount = qty * rate` rounded to 2
    places. Returns (line_items_with_amount, subtotal).

    Sums amounts without further rounding so the subtotal precision
    matches the sum of the per-line amounts exactly (no drift).

    Pass-through fields: hsn_sac, lead_id (both optional). The service
    later enriches each line with lead_serial_no by looking up the
    lead in one batched query.
    """
    out: list[dict] = []
    subtotal = Decimal("0.00")
    for li in line_items:
        qty = Decimal(str(li["qty"]))
        rate = Decimal(str(li["rate"]))
        amount = _round2(qty * rate)
        item = {
            "description": str(li["description"]),
            "qty": str(qty),
            "rate": str(rate),
            "amount": str(amount),
        }
        hsn = li.get("hsn_sac")
        if hsn:
            item["hsn_sac"] = str(hsn)
        else:
            item["hsn_sac"] = None
        lid = li.get("lead_id")
        if lid:
            item["lead_id"] = str(lid)
        else:
            item["lead_id"] = None
        out.append(item)
        subtotal += amount
    return out, _round2(subtotal)


# ── Number → words (Indian numbering system) ──────────────────────────


_ONES = [
    "", "One", "Two", "Three", "Four", "Five", "Six", "Seven",
    "Eight", "Nine", "Ten", "Eleven", "Twelve", "Thirteen", "Fourteen",
    "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen",
]
_TENS = [
    "", "", "Twenty", "Thirty", "Forty", "Fifty",
    "Sixty", "Seventy", "Eighty", "Ninety",
]


def _two_digits_to_words(n: int) -> str:
    if n == 0:
        return ""
    if n < 20:
        return _ONES[n]
    return (_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else "")).strip()


def _three_digits_to_words(n: int) -> str:
    out = ""
    if n >= 100:
        out += _ONES[n // 100] + " Hundred"
        if n % 100:
            out += " " + _two_digits_to_words(n % 100)
        return out.strip()
    return _two_digits_to_words(n)


def amount_in_words(amount: Decimal) -> str:
    """Convert ₹ amount into Indian-numbering English words for GST
    invoice. Handles paise. Example: 12390.50 → "Rupees Twelve Thousand
    Three Hundred Ninety and Fifty Paise Only".
    """
    amount = _round2(amount)
    rupees, paise = divmod(int(amount * 100), 100)

    if rupees == 0:
        whole = "Zero"
    else:
        crore, rest = divmod(rupees, 10000000)
        lakh, rest = divmod(rest, 100000)
        thousand, rest = divmod(rest, 1000)
        hundred = rest

        parts = []
        if crore:
            parts.append(_two_digits_to_words(crore) + " Crore")
        if lakh:
            parts.append(_two_digits_to_words(lakh) + " Lakh")
        if thousand:
            parts.append(_two_digits_to_words(thousand) + " Thousand")
        if hundred:
            parts.append(_three_digits_to_words(hundred))
        whole = " ".join(parts).strip() or "Zero"

    text = f"Rupees {whole}"
    if paise:
        text += f" and {_two_digits_to_words(paise)} Paise"
    text += " Only"
    return text
