from __future__ import annotations

import uuid
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query, UploadFile, File, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.tenant import get_current_company_id
from app.db.session import get_db
from app.dependencies import get_current_admin
from app.models.profile import Profile
from app.schemas.common import PaginatedResponse
from app.schemas.invoice import (
    InvoiceCreate, InvoiceListOut, InvoiceOut, InvoicePrefillOut,
    InvoiceSettingsOut, InvoiceSettingsUpsert, InvoiceStatusUpdate,
)
from app.services.invoice_service import InvoiceService, InvoiceSettingsService

router = APIRouter(prefix="/invoices", tags=["Invoices"])


# ── Settings (one row per tenant) ─────────────────────────────────────


@router.get("/settings", response_model=Optional[InvoiceSettingsOut])
async def get_invoice_settings(
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Fetch this tenant's invoice settings. Returns null if not yet
    configured — FE shows an empty form in that case.
    """
    svc = InvoiceSettingsService(db, company_id)
    return await svc.get()


@router.put("/settings", response_model=InvoiceSettingsOut)
async def upsert_invoice_settings(
    body: InvoiceSettingsUpsert,
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Save or update FMC's billing info. state_code is auto-derived
    from GSTIN[0:2] — body's state_code (if any) is ignored.
    """
    svc = InvoiceSettingsService(db, company_id)
    return await svc.upsert(body.model_dump(exclude_unset=True))


@router.post("/settings/logo", response_model=dict)
async def upload_settings_logo(
    file: UploadFile = File(...),
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Upload company logo (PNG / JPEG). Stored at
    assets/<company_id>/logo.<ext>; persisted on invoice_settings.logo_url.
    """
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "Logo must be an image")
    ext = (file.filename or "").rsplit(".", 1)[-1].lower() or "png"
    if ext not in ("png", "jpg", "jpeg"):
        ext = "png"
    content = await file.read()
    svc = InvoiceSettingsService(db, company_id)
    path = await svc.upload_logo(content, ext)
    return {"logo_url": path}


@router.post("/settings/signature", response_model=dict)
async def upload_settings_signature(
    file: UploadFile = File(...),
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Upload authorized-signatory signature image (PNG / JPEG). Stored
    at assets/<company_id>/signature.<ext>.
    """
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(400, "Signature must be an image")
    ext = (file.filename or "").rsplit(".", 1)[-1].lower() or "png"
    if ext not in ("png", "jpg", "jpeg"):
        ext = "png"
    content = await file.read()
    svc = InvoiceSettingsService(db, company_id)
    path = await svc.upload_signature(content, ext)
    return {"signature_url": path}


# ── Invoices ──────────────────────────────────────────────────────────


@router.post("", response_model=InvoiceOut, status_code=201)
async def create_invoice(
    body: InvoiceCreate,
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Issue a new invoice. Reserves sequential number, computes tax,
    inserts row, then best-effort renders + uploads PDF. If PDF fails
    (e.g. storage outage), the invoice is still committed — admin can
    retry the PDF via POST /invoices/{id}/regenerate-pdf.
    """
    svc = InvoiceService(db, company_id)
    return await svc.create(body.model_dump(exclude_unset=False), created_by=admin.id)


@router.get("", response_model=PaginatedResponse[InvoiceListOut])
async def list_invoices(
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    q: Optional[str] = Query(None, description="Search by invoice #, customer name, customer GSTIN"),
    status: Optional[str] = Query(None, pattern="^(draft|issued|paid|void)$"),
    financial_year: Optional[str] = Query(None, description="e.g. 2025-26"),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
):
    svc = InvoiceService(db, company_id)
    return await svc.list(
        page=page, page_size=page_size, q=q, status=status,
        financial_year=financial_year, date_from=date_from, date_to=date_to,
    )


@router.get("/prefill/lead/{lead_id}", response_model=InvoicePrefillOut)
async def prefill_invoice_from_lead(
    lead_id: uuid.UUID,
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Returns customer-block fields pre-populated from a lead. Verifies
    the lead is in this tenant. FE merges into the Create Invoice form.
    """
    svc = InvoiceService(db, company_id)
    return await svc.prefill_from_lead(lead_id)


@router.get("/{invoice_id}", response_model=InvoiceOut)
async def get_invoice(
    invoice_id: uuid.UUID,
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    svc = InvoiceService(db, company_id)
    return await svc.get(invoice_id)


@router.get("/{invoice_id}/download", response_model=dict)
async def download_invoice(
    invoice_id: uuid.UUID,
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Fresh signed URL (5-min TTL) for the invoice PDF. Re-minted per
    call so leaked URLs expire fast.
    """
    svc = InvoiceService(db, company_id)
    url = await svc.download_url(invoice_id)
    return {"url": url}


@router.post("/{invoice_id}/regenerate-pdf", response_model=InvoiceOut)
async def regenerate_invoice_pdf(
    invoice_id: uuid.UUID,
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Re-render and re-upload the PDF for an existing invoice. Use
    after settings (e.g. logo) change, or if the original PDF render
    failed due to a storage outage. Invoice number + content unchanged.
    """
    svc = InvoiceService(db, company_id)
    return await svc.regenerate_pdf(invoice_id)


@router.patch("/{invoice_id}/status", response_model=InvoiceOut)
async def update_invoice_status(
    invoice_id: uuid.UUID,
    body: InvoiceStatusUpdate,
    admin: Profile = Depends(get_current_admin),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    """Move invoice to 'paid' or 'void'. Void is irreversible — number
    is never recycled. void_reason is optional but recommended.
    """
    svc = InvoiceService(db, company_id)
    return await svc.update_status(
        invoice_id, new_status=body.status, void_reason=body.void_reason,
    )
