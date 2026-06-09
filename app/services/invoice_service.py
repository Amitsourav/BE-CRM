from __future__ import annotations

import logging
import os
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import select, text as sa_text, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import INDIAN_STATE_CODES, INDIAN_STATE_NAMES
from app.core.exceptions import BadRequestError, NotFoundError, ForbiddenError
from app.models.invoice import Invoice
from app.models.invoice_counter import InvoiceCounter
from app.models.invoice_settings import InvoiceSettings
from app.models.lead import Lead
from app.models.profile import Profile
from app.services.invoice_tax import (
    compute_line_amounts, compute_tax, derive_customer_state_code,
)
from app.services.invoice_pdf import render_invoice_pdf
from app.services.supabase_storage import (
    SupabaseStorageError, upload_pdf, upload_image, signed_url, download_bytes,
)
from app.utils.date_helpers import now_utc

logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────


def compute_financial_year(d: date) -> str:
    """India FY runs April → March. Return 'YYYY-YY' format used on the
    invoice number and counters table.
    """
    if d.month >= 4:
        return f"{d.year}-{(d.year + 1) % 100:02d}"
    return f"{d.year - 1}-{d.year % 100:02d}"


def _invoice_number_to_storage_path(*, company_id: uuid.UUID, fy: str, number: str) -> str:
    """Slash in invoice number isn't valid in storage object keys. Replace
    with dash and slot under per-company/per-FY prefix.
    """
    safe = number.replace("/", "-")
    return f"invoices/{company_id}/{fy}/{safe}.pdf"


# ── Settings service ──────────────────────────────────────────────────


class InvoiceSettingsService:
    def __init__(self, db: AsyncSession, company_id: uuid.UUID):
        self.db = db
        self.company_id = company_id

    async def get(self) -> Optional[InvoiceSettings]:
        return (await self.db.execute(
            select(InvoiceSettings).where(InvoiceSettings.company_id == self.company_id)
        )).scalar_one_or_none()

    async def upsert(self, data: dict) -> InvoiceSettings:
        """Insert or update. state_code is derived from gstin[0:2] —
        we never trust an explicit value because it can drift from the
        GSTIN if admin edits one but not the other.
        """
        gstin = (data.get("gstin") or "").upper().strip()
        if not gstin:
            raise BadRequestError("gstin is required")
        state_code = gstin[:2]
        state_name = data.get("state_name")
        # Optional: cross-check state_name against canonical list. If user
        # supplied a name that doesn't exist in INDIAN_STATE_CODES, still
        # accept (some state names have regional variants) but warn in logs.
        if state_name and state_name not in INDIAN_STATE_CODES:
            logger.info("InvoiceSettings: non-canonical state_name=%r accepted", state_name)
        if not state_name:
            state_name = INDIAN_STATE_NAMES.get(state_code, "Unknown")

        existing = await self.get()
        payload = {
            **data,
            "gstin": gstin,
            "state_code": state_code,
            "state_name": state_name,
        }
        if existing:
            for k, v in payload.items():
                setattr(existing, k, v)
            existing.updated_at = now_utc()
            await self.db.commit()
            await self.db.refresh(existing)
            return existing

        new = InvoiceSettings(company_id=self.company_id, **payload)
        self.db.add(new)
        await self.db.commit()
        await self.db.refresh(new)
        return new

    async def upload_logo(self, content: bytes, ext: str) -> str:
        path = f"assets/{self.company_id}/logo.{ext}"
        await upload_image(path, content, f"image/{ext}")
        settings = await self.get()
        if settings:
            settings.logo_url = path
            settings.updated_at = now_utc()
            await self.db.commit()
        return path

    async def upload_signature(self, content: bytes, ext: str) -> str:
        path = f"assets/{self.company_id}/signature.{ext}"
        await upload_image(path, content, f"image/{ext}")
        settings = await self.get()
        if settings:
            settings.signature_url = path
            settings.updated_at = now_utc()
            await self.db.commit()
        return path


# ── Invoice service ───────────────────────────────────────────────────


class InvoiceService:
    def __init__(self, db: AsyncSession, company_id: uuid.UUID):
        self.db = db
        self.company_id = company_id

    async def _settings(self) -> InvoiceSettings:
        s = (await self.db.execute(
            select(InvoiceSettings).where(InvoiceSettings.company_id == self.company_id)
        )).scalar_one_or_none()
        if not s:
            raise BadRequestError(
                "Invoice settings not configured. PUT /api/v1/invoices/settings first."
            )
        return s

    async def _reserve_invoice_number(
        self, fy: str, prefix: str
    ) -> tuple[str, int]:
        """Atomic counter increment. Returns (formatted_number, sequence).

        Mirrors `_reserve_serial_numbers` pattern in lead_service.py —
        INSERT … ON CONFLICT … RETURNING gives us a row-level lock that
        serializes concurrent invoice creates. Two admins clicking
        Create at the same instant get distinct sequence numbers.
        """
        result = (await self.db.execute(
            sa_text(
                """
                INSERT INTO invoice_counters (company_id, financial_year, next_number)
                VALUES (:cid, :fy, 2)
                ON CONFLICT (company_id, financial_year) DO UPDATE
                  SET next_number = invoice_counters.next_number + 1,
                      updated_at = now()
                RETURNING next_number - 1 AS issued_number
                """
            ),
            {"cid": str(self.company_id), "fy": fy},
        )).first()
        seq = int(result.issued_number)
        return f"{prefix}/{fy}/{seq:03d}", seq

    async def create(self, payload: dict, *, created_by: uuid.UUID) -> Invoice:
        """Issue a new invoice. Phases:
          1. Validate inputs + tax math.
          2. Reserve sequential number atomically.
          3. INSERT invoice row in same transaction (rolls back if anything else fails).
          4. Commit. From here, invoice number is BURNED.
          5. Best-effort: render PDF + upload to Supabase + patch pdf_url.
             Failure here is logged but does not roll back the invoice.
             Admin can re-trigger via POST /invoices/{id}/regenerate-pdf.
        """
        settings = await self._settings()

        # Phase 1 — validate + math
        invoice_date = payload.get("invoice_date") or date.today()
        fy = compute_financial_year(invoice_date)
        customer_gstin = (payload.get("customer_gstin") or None)
        if customer_gstin:
            customer_gstin = customer_gstin.upper().strip()
        customer_state_code = derive_customer_state_code(
            customer_state_code=payload.get("customer_state_code"),
            customer_gstin=customer_gstin,
        )
        customer_state_name = payload.get("customer_state_name")
        if customer_state_code and not customer_state_name:
            customer_state_name = INDIAN_STATE_NAMES.get(customer_state_code, "")

        line_items, subtotal = compute_line_amounts(payload["line_items"])
        if subtotal <= 0:
            raise BadRequestError("Invoice subtotal must be greater than zero")

        tax = compute_tax(
            subtotal=subtotal,
            fmc_state_code=settings.state_code,
            customer_state_code=customer_state_code,
            customer_gstin=customer_gstin,
            tax_rate=Decimal(str(settings.default_tax_rate)),
        )

        # Phase 2 + 3 — number + insert in single transaction
        number, seq = await self._reserve_invoice_number(fy, settings.invoice_prefix)
        invoice = Invoice(
            company_id=self.company_id,
            invoice_number=number,
            financial_year=fy,
            sequence_number=seq,
            invoice_date=invoice_date,
            due_date=payload.get("due_date"),
            status="issued",
            customer_name=payload["customer_name"],
            customer_gstin=customer_gstin,
            customer_state_code=customer_state_code,
            customer_state_name=customer_state_name,
            customer_email=payload.get("customer_email"),
            customer_phone=payload.get("customer_phone"),
            customer_address=payload.get("customer_address"),
            lead_id=payload.get("lead_id"),
            subtotal=tax.grand_total - tax.total_tax,
            cgst_amount=tax.cgst_amount,
            sgst_amount=tax.sgst_amount,
            igst_amount=tax.igst_amount,
            total_tax=tax.total_tax,
            grand_total=tax.grand_total,
            tax_rate=Decimal(str(settings.default_tax_rate)),
            tax_split=tax.tax_split,
            line_items=line_items,
            notes=payload.get("notes"),
            terms=settings.default_terms,
            created_by=created_by,
        )
        self.db.add(invoice)
        await self.db.commit()
        await self.db.refresh(invoice)
        logger.info("Invoice %s issued: company=%s seq=%d total=%s",
                    invoice.invoice_number, self.company_id, seq, invoice.grand_total)

        # Phase 4 — best-effort PDF render + upload (does NOT roll back invoice on failure)
        await self._render_and_store_pdf(invoice, settings)
        return invoice

    async def _render_and_store_pdf(self, invoice: Invoice, settings: InvoiceSettings) -> None:
        try:
            logo_bytes = await download_bytes(settings.logo_url) if settings.logo_url else None
            sig_bytes = await download_bytes(settings.signature_url) if settings.signature_url else None
            pdf_bytes = render_invoice_pdf(
                invoice=invoice, settings=settings,
                logo_bytes=logo_bytes, signature_bytes=sig_bytes,
            )
            path = _invoice_number_to_storage_path(
                company_id=self.company_id, fy=invoice.financial_year, number=invoice.invoice_number,
            )
            await upload_pdf(path, pdf_bytes)
            invoice.pdf_storage_path = path
            invoice.pdf_url = path  # actual download URL re-signed on read
            await self.db.commit()
            await self.db.refresh(invoice)
            logger.info("Invoice %s PDF stored at %s (%d bytes)",
                        invoice.invoice_number, path, len(pdf_bytes))
        except (ValueError, SupabaseStorageError) as e:
            logger.error(
                "Invoice %s PDF render/upload failed (invoice still committed): %s",
                invoice.invoice_number, e,
            )
        except Exception:
            logger.exception(
                "Invoice %s PDF render/upload UNEXPECTED failure", invoice.invoice_number,
            )

    async def list(
        self, *, page: int = 1, page_size: int = 25,
        q: Optional[str] = None, status: Optional[str] = None,
        financial_year: Optional[str] = None,
        date_from: Optional[date] = None, date_to: Optional[date] = None,
    ) -> dict:
        query = select(Invoice).where(Invoice.company_id == self.company_id).order_by(Invoice.invoice_date.desc(), Invoice.sequence_number.desc())
        if q:
            term = f"%{q.strip()}%"
            from sqlalchemy import or_
            query = query.where(or_(
                Invoice.invoice_number.ilike(term),
                Invoice.customer_name.ilike(term),
                Invoice.customer_gstin.ilike(term),
            ))
        if status:
            query = query.where(Invoice.status == status)
        if financial_year:
            query = query.where(Invoice.financial_year == financial_year)
        if date_from:
            query = query.where(Invoice.invoice_date >= date_from)
        if date_to:
            query = query.where(Invoice.invoice_date <= date_to)

        total = (await self.db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
        rows = (await self.db.execute(
            query.limit(page_size).offset((page - 1) * page_size)
        )).scalars().all()
        return {
            "items": rows,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": (total + page_size - 1) // page_size if page_size else 0,
        }

    async def get(self, invoice_id: uuid.UUID) -> Invoice:
        inv = (await self.db.execute(
            select(Invoice).where(
                Invoice.id == invoice_id, Invoice.company_id == self.company_id,
            )
        )).scalar_one_or_none()
        if not inv:
            raise NotFoundError("Invoice not found")
        return inv

    async def download_url(self, invoice_id: uuid.UUID, ttl_seconds: int = 300) -> str:
        inv = await self.get(invoice_id)
        if not inv.pdf_storage_path:
            raise BadRequestError(
                "Invoice PDF not yet generated. Use POST /invoices/{id}/regenerate-pdf."
            )
        return await signed_url(inv.pdf_storage_path, ttl_seconds)

    async def regenerate_pdf(self, invoice_id: uuid.UUID) -> Invoice:
        inv = await self.get(invoice_id)
        settings = await self._settings()
        await self._render_and_store_pdf(inv, settings)
        return inv

    async def update_status(
        self, invoice_id: uuid.UUID, *, new_status: str, void_reason: Optional[str] = None,
    ) -> Invoice:
        if new_status not in ("paid", "void"):
            raise BadRequestError("status must be 'paid' or 'void'")
        inv = await self.get(invoice_id)
        if inv.status == "void":
            raise BadRequestError("Voided invoices cannot be updated")
        if new_status == "void":
            inv.status = "void"
            inv.voided_at = now_utc()
            inv.void_reason = void_reason
        else:
            inv.status = "paid"
        inv.updated_at = now_utc()
        await self.db.commit()
        await self.db.refresh(inv)
        return inv

    async def prefill_from_lead(self, lead_id: uuid.UUID) -> dict:
        """Returns customer block prefilled from a lead. Verifies the
        lead is in this tenant (multi-tenant leak guard).
        """
        lead = (await self.db.execute(
            select(Lead).where(
                Lead.id == lead_id, Lead.company_id == self.company_id, Lead.is_deleted == False,  # noqa: E712
            )
        )).scalar_one_or_none()
        if not lead:
            raise NotFoundError("Lead not found")
        # Derive state code from lead.state if it matches a known name
        state_code = INDIAN_STATE_CODES.get(lead.state or "", None)
        return {
            "customer_name": lead.full_name,
            "customer_email": lead.email,
            "customer_phone": lead.phone,
            "customer_address": ", ".join(p for p in [lead.city, lead.state] if p) or None,
            "customer_state_name": lead.state,
            "customer_state_code": state_code,
            "lead_id": lead.id,
        }
