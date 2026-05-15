from __future__ import annotations

import uuid
import io
from fastapi import APIRouter, Depends, UploadFile, File
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.session import get_db
from app.dependencies import get_current_user, get_current_manager
from app.core.tenant import get_current_company_id
from app.models.profile import Profile
from app.services.csv_import_service import CSVImportService
from app.schemas.csv_import import CSVProcessRequest, CSVImportOut, CSVPreviewOut

router = APIRouter(prefix="/csv", tags=["CSV Import"])

# Store uploaded content temporarily in memory (keyed by import ID)
_upload_cache: dict[str, bytes] = {}


@router.post("/upload", response_model=CSVImportOut, status_code=201)
async def upload_csv(
    file: UploadFile = File(...),
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    content = await file.read()
    service = CSVImportService(db, company_id)
    csv_import = await service.upload(file.filename or "upload.csv", content, current_user.id)
    _upload_cache[str(csv_import.id)] = content
    return csv_import


@router.post("/{import_id}/preview", response_model=CSVPreviewOut)
async def preview_csv(
    import_id: uuid.UUID,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = CSVImportService(db, company_id)
    preview = await service.preview(import_id, current_user)

    content = _upload_cache.get(str(import_id))
    if content:
        from app.utils.csv_parser import parse_csv_content
        headers, rows = parse_csv_content(content)
        preview["preview_rows"] = rows[:5]

    return preview


@router.post("/{import_id}/process", response_model=CSVImportOut)
async def process_csv(
    import_id: uuid.UUID,
    body: CSVProcessRequest,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    content = _upload_cache.get(str(import_id))
    if not content:
        from app.core.exceptions import BadRequestError
        raise BadRequestError("Upload content expired. Please re-upload the file.")

    service = CSVImportService(db, company_id)
    result = await service.process(
        import_id=import_id,
        user=current_user,
        column_mapping=body.column_mapping,
        content=content,
        assigned_agent_id=body.assigned_agent_id,
        lead_source_id=body.lead_source_id,
    )
    _upload_cache.pop(str(import_id), None)
    return result


@router.get("/{import_id}/status", response_model=CSVImportOut)
async def get_status(
    import_id: uuid.UUID,
    current_user: Profile = Depends(get_current_user),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = CSVImportService(db, company_id)
    return await service.get_status(import_id, current_user)


@router.get("/history", response_model=list[CSVImportOut])
async def get_history(
    admin: Profile = Depends(get_current_manager),
    company_id: uuid.UUID = Depends(get_current_company_id),
    db: AsyncSession = Depends(get_db),
):
    service = CSVImportService(db, company_id)
    return await service.get_history()


@router.get("/template")
async def download_template(current_user: Profile = Depends(get_current_user)):
    headers = [
        "Full Name", "Email", "Phone", "Alternate Phone", "Gender",
        "Date of Birth", "City", "State", "Country", "Pincode",
        "Highest Qualification", "Stream", "Passing Year", "College Name",
        "University", "Percentage", "Target Degree", "Target Intake",
        "Preferred Countries", "Preferred Universities", "Notes",
        # FMC-specific. Admitverse leaves this empty on import. Plain
        # number in Lakhs, e.g. "25" for 25L or "300" for 3Cr. The
        # importer rejects non-numeric values like "25cr" or "25lakh".
        "Loan Amount (Lakhs)",
    ]
    content = ",".join(headers) + "\n"
    return StreamingResponse(
        io.BytesIO(content.encode()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=lead_import_template.csv"},
    )
