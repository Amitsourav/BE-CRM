from __future__ import annotations

import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional, List
from pydantic import BaseModel, Field, field_validator


# GSTIN structural regex: 2-digit state code + 5 letters (entity PAN-A) +
# 4 digits (entity PAN-N) + 1 letter (PAN check) + 1 alphanumeric (entity
# code) + Z (constant) + 1 alphanumeric (checksum).
GSTIN_REGEX = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z][Z][0-9A-Z]$")
PAN_REGEX = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


# ── Settings ──────────────────────────────────────────────────────────


class InvoiceSettingsUpsert(BaseModel):
    """Body for PUT /invoices/settings — upsert FMC's billing info.

    state_code is intentionally NOT accepted in input — the service
    derives it from gstin[0:2] so it can't drift. state_name is required
    so we know how to render the Place of Supply human-readably (the
    Indian state code dictionary lives in app.core.constants).
    """
    legal_name: str = Field(min_length=1, max_length=200)
    gstin: str = Field(min_length=15, max_length=15)
    pan: str = Field(min_length=10, max_length=10)
    state_name: str = Field(min_length=1, max_length=50)
    address_line1: str = Field(min_length=1, max_length=200)
    address_line2: Optional[str] = Field(default=None, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    pincode: str = Field(min_length=1, max_length=10)
    email: Optional[str] = Field(default=None, max_length=200)
    phone: Optional[str] = Field(default=None, max_length=20)
    bank_account_name: Optional[str] = Field(default=None, max_length=200)
    bank_account_number: Optional[str] = Field(default=None, max_length=50)
    bank_ifsc: Optional[str] = Field(default=None, max_length=11)
    bank_name: Optional[str] = Field(default=None, max_length=100)
    bank_branch: Optional[str] = Field(default=None, max_length=100)
    invoice_prefix: str = Field(default="FMC", min_length=1, max_length=20)
    default_tax_rate: Decimal = Field(default=Decimal("18.00"), ge=0, le=100)
    default_terms: Optional[str] = None

    @field_validator("gstin")
    @classmethod
    def _validate_gstin(cls, v: str) -> str:
        v = v.strip().upper()
        if not GSTIN_REGEX.match(v):
            raise ValueError("Invalid GSTIN format (expected 22AAAAA0000A1Z5 shape)")
        return v

    @field_validator("pan")
    @classmethod
    def _validate_pan(cls, v: str) -> str:
        v = v.strip().upper()
        if not PAN_REGEX.match(v):
            raise ValueError("Invalid PAN format (expected AAAAA0000A shape)")
        return v


class InvoiceSettingsOut(BaseModel):
    company_id: uuid.UUID
    legal_name: str
    gstin: str
    pan: str
    state_code: str
    state_name: str
    address_line1: str
    address_line2: Optional[str] = None
    city: str
    pincode: str
    email: Optional[str] = None
    phone: Optional[str] = None
    bank_account_name: Optional[str] = None
    bank_account_number: Optional[str] = None
    bank_ifsc: Optional[str] = None
    bank_name: Optional[str] = None
    bank_branch: Optional[str] = None
    logo_url: Optional[str] = None
    signature_url: Optional[str] = None
    invoice_prefix: str
    default_tax_rate: Decimal
    default_terms: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Invoice ───────────────────────────────────────────────────────────


class InvoiceLineItemIn(BaseModel):
    """One line on the invoice. Service computes amount = qty * rate at
    write time; we never trust the client's amount.

    HSN/SAC is OPTIONAL — GST law only mandates it above ₹5 cr
    turnover. FMC at ~₹2 cr is below threshold; admin can leave the
    field blank and the PDF skips the column value for that line.
    """
    description: str = Field(min_length=1, max_length=500)
    hsn_sac: Optional[str] = Field(default=None, max_length=10)
    qty: Decimal = Field(gt=0)
    rate: Decimal = Field(ge=0)


class InvoiceLineItemOut(BaseModel):
    description: str
    hsn_sac: Optional[str] = None
    qty: Decimal
    rate: Decimal
    amount: Decimal


class InvoiceCreate(BaseModel):
    """Body for POST /invoices.

    customer_state_code is optional in input; if not supplied AND
    customer_gstin is, the service derives it from customer_gstin[0:2].
    If neither is supplied, the service treats it as inter-state (IGST).
    """
    customer_name: str = Field(min_length=1, max_length=200)
    customer_gstin: Optional[str] = Field(default=None, min_length=15, max_length=15)
    customer_state_code: Optional[str] = Field(default=None, min_length=2, max_length=2)
    customer_state_name: Optional[str] = Field(default=None, max_length=50)
    customer_email: Optional[str] = Field(default=None, max_length=200)
    customer_phone: Optional[str] = Field(default=None, max_length=20)
    customer_address: Optional[str] = None
    invoice_date: Optional[date] = None  # default today in the service
    due_date: Optional[date] = None
    line_items: List[InvoiceLineItemIn] = Field(min_length=1)
    notes: Optional[str] = None
    lead_id: Optional[uuid.UUID] = None

    @field_validator("customer_gstin")
    @classmethod
    def _validate_customer_gstin(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        v = v.strip().upper()
        if not GSTIN_REGEX.match(v):
            raise ValueError("Invalid customer GSTIN format")
        return v


class InvoiceOut(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    invoice_number: str
    financial_year: str
    sequence_number: int
    invoice_date: date
    due_date: Optional[date] = None
    status: str
    customer_name: str
    customer_gstin: Optional[str] = None
    customer_state_code: Optional[str] = None
    customer_state_name: Optional[str] = None
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_address: Optional[str] = None
    lead_id: Optional[uuid.UUID] = None
    subtotal: Decimal
    cgst_amount: Decimal
    sgst_amount: Decimal
    igst_amount: Decimal
    total_tax: Decimal
    grand_total: Decimal
    tax_rate: Decimal
    tax_split: str
    line_items: List[InvoiceLineItemOut] = []
    pdf_url: Optional[str] = None
    pdf_storage_path: Optional[str] = None
    notes: Optional[str] = None
    terms: Optional[str] = None
    created_by: Optional[uuid.UUID] = None
    voided_at: Optional[datetime] = None
    void_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class InvoiceListOut(BaseModel):
    """Slim shape for list view — omits JSONB line_items + terms + notes
    to keep the payload small on a 200-row page.
    """
    id: uuid.UUID
    invoice_number: str
    invoice_date: date
    customer_name: str
    customer_gstin: Optional[str] = None
    grand_total: Decimal
    status: str
    pdf_url: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class InvoiceStatusUpdate(BaseModel):
    status: str = Field(pattern="^(paid|void)$")
    void_reason: Optional[str] = Field(default=None, max_length=500)


class InvoicePrefillOut(BaseModel):
    """Returned by GET /invoices/prefill/lead/{lead_id}. Customer block
    only — FE merges into the Create Invoice form.
    """
    customer_name: str
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None
    customer_address: Optional[str] = None
    customer_state_name: Optional[str] = None
    customer_state_code: Optional[str] = None
    lead_id: uuid.UUID
