from __future__ import annotations

import csv
import io
from difflib import SequenceMatcher

# Known lead fields for auto-mapping
LEAD_FIELD_ALIASES: dict[str, list[str]] = {
    "full_name": ["name", "full name", "fullname", "student name", "student_name", "candidate name"],
    "email": ["email", "email address", "email_address", "e-mail", "mail"],
    "phone": ["phone", "phone number", "phone_number", "mobile", "mobile number", "contact", "contact number"],
    "alternate_phone": ["alternate phone", "alt phone", "alternate_phone", "secondary phone", "parent phone"],
    "city": ["city", "location"],
    "state": ["state", "province"],
    "country": ["country", "nation"],
    "pincode": ["pincode", "zip", "zipcode", "zip code", "postal code"],
    "highest_qualification": ["qualification", "highest qualification", "education", "degree"],
    "stream": ["stream", "branch", "specialization", "major"],
    "passing_year": ["passing year", "year of passing", "graduation year", "pass_year"],
    "college_name": ["college", "college name", "institution", "school"],
    "university": ["university", "university name"],
    "percentage": ["percentage", "marks", "cgpa", "gpa", "score"],
    "target_degree": ["target degree", "interested course", "course", "program"],
    "target_intake": ["intake", "target intake", "session", "batch"],
    "gender": ["gender", "sex"],
    "date_of_birth": ["dob", "date of birth", "birth date", "birthday"],
    "notes": ["notes", "remarks", "comments", "additional info"],
    # Per-row source label. If present in the CSV, the importer
    # find-or-creates a lead_sources row by this name and stamps each
    # lead with that source — overriding the global source dropdown
    # picked at import time. Empty cells fall back to the dropdown value.
    "source": [
        "source", "lead source", "lead_source", "source name",
        "source_name", "channel", "lead channel",
    ],
    # FMC-specific: loan amount is always stored in Lakhs as a plain
    # number string ("25", "300", "30.5"). Aliases cover the most common
    # spreadsheet headers users use ("amount", "loan amount", "loan amount (lakhs)").
    # csv_import_service validates the value is numeric-only before insert.
    "loan_amount": [
        "loan amount", "loan amount (lakhs)", "amount", "amount (lakhs)",
        "amount (₹l)", "loan", "loan size",
    ],
    # Admitverse-specific: free-text budget ("50 lakh", "£18,000",
    # "$30,000"). csv_import_service mirrors it to budget_amount +
    # budget_currency via app.utils.budget_parser.
    "budget": [
        "budget", "study budget", "education budget", "budget amount",
        "annual budget", "fees budget",
    ],
}


def parse_csv_content(content: str | bytes, max_rows: int = 5000) -> tuple[list[str], list[dict]]:
    if isinstance(content, bytes):
        # Excel on Windows often saves CSV in cp1252; macOS may save in
        # latin-1; copy-pasted Devanagari/emoji may corrupt sequences. Try
        # UTF-8 (with BOM stripping) first, then common fallbacks, and as
        # a last resort replace undecodable bytes so the upload still
        # parses instead of raising UnicodeDecodeError.
        for enc in ("utf-8-sig", "cp1252", "latin-1"):
            try:
                content = content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            content = content.decode("utf-8", errors="replace")

    reader = csv.DictReader(io.StringIO(content))
    headers = reader.fieldnames or []
    rows = []
    for i, row in enumerate(reader):
        if i >= max_rows:
            break
        rows.append(row)
    return headers, rows


def suggest_column_mapping(headers: list[str]) -> dict[str, str]:
    mapping = {}
    normalized_headers = {h: h.strip().lower().replace("_", " ") for h in headers}

    for field, aliases in LEAD_FIELD_ALIASES.items():
        best_match = None
        best_score = 0.0
        for header, normalized in normalized_headers.items():
            for alias in aliases:
                score = SequenceMatcher(None, normalized, alias).ratio()
                if score > best_score and score >= 0.7:
                    best_score = score
                    best_match = header
        if best_match:
            mapping[best_match] = field

    return mapping


def normalize_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = "".join(c for c in phone if c.isdigit())
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    if len(digits) == 10:
        return f"+91{digits}"
    return phone.strip()
