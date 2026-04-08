from __future__ import annotations

import uuid
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.csv_import import CSVImport
from app.models.lead import Lead
from app.models.notification import Notification
from app.models.profile import Profile
from app.core.constants import CSVImportStatus, LeadStage, NotificationType, UserRole
from app.core.exceptions import NotFoundError, BadRequestError, ForbiddenError
from app.utils.csv_parser import parse_csv_content, suggest_column_mapping, normalize_phone
from app.config import get_settings

logger = logging.getLogger(__name__)


class CSVImportService:
    def __init__(self, db: AsyncSession, company_id: uuid.UUID):
        self.db = db
        self.company_id = company_id
        self.settings = get_settings()

    async def upload(self, file_name: str, content: bytes, uploaded_by: uuid.UUID) -> CSVImport:
        # Validate size
        size_mb = len(content) / (1024 * 1024)
        if size_mb > self.settings.csv_max_size_mb:
            raise BadRequestError(f"File too large. Max {self.settings.csv_max_size_mb}MB")

        headers, rows = parse_csv_content(content, self.settings.csv_max_rows)
        if not headers:
            raise BadRequestError("CSV file has no headers")

        suggested_mapping = suggest_column_mapping(headers)

        csv_import = CSVImport(
            company_id=self.company_id,
            uploaded_by=uploaded_by,
            file_name=file_name,
            status=CSVImportStatus.UPLOADED,
            total_rows=len(rows),
            raw_headers=headers,
            column_mapping=suggested_mapping,
        )
        self.db.add(csv_import)
        await self.db.commit()
        await self.db.refresh(csv_import)

        # Store content temporarily (in real app use storage, here we store in memory via preview)
        csv_import._raw_content = content
        csv_import._parsed_rows = rows

        return csv_import

    async def preview(self, import_id: uuid.UUID, user: Profile) -> dict:
        csv_import = await self._get_import(import_id, user)

        return {
            "id": csv_import.id,
            "file_name": csv_import.file_name,
            "total_rows": csv_import.total_rows,
            "raw_headers": csv_import.raw_headers,
            "suggested_mapping": csv_import.column_mapping,
            "preview_rows": [],  # Would need re-parsing or storage
        }

    async def process(
        self,
        import_id: uuid.UUID,
        user: Profile,
        column_mapping: dict[str, str],
        content: bytes,
        assigned_agent_id: uuid.UUID | None = None,
        lead_source_id: uuid.UUID | None = None,
    ) -> CSVImport:
        csv_import = await self._get_import(import_id, user)
        csv_import.status = CSVImportStatus.PROCESSING
        csv_import.column_mapping = column_mapping
        csv_import.assigned_agent_id = assigned_agent_id
        csv_import.lead_source_id = lead_source_id
        await self.db.commit()

        headers, rows = parse_csv_content(content, self.settings.csv_max_rows)

        success = 0
        failures = 0
        duplicates = 0
        errors = []

        # --- Phase 1: Parse all rows and collect phones/emails for batch duplicate check ---
        parsed_rows = []
        for row_idx, row in enumerate(rows, start=2):
            try:
                lead_data = {}
                for csv_col, lead_field in column_mapping.items():
                    value = row.get(csv_col, "").strip()
                    if value:
                        lead_data[lead_field] = value

                if not lead_data.get("full_name"):
                    errors.append({"row": row_idx, "error": "Missing full_name"})
                    failures += 1
                    continue

                if "phone" in lead_data:
                    lead_data["phone"] = normalize_phone(lead_data["phone"])

                # Handle list fields
                for list_field in ("preferred_countries", "preferred_universities"):
                    if list_field in lead_data and isinstance(lead_data[list_field], str):
                        lead_data[list_field] = [v.strip() for v in lead_data[list_field].split(",")]

                # Handle numeric fields
                if "passing_year" in lead_data:
                    try:
                        lead_data["passing_year"] = int(lead_data["passing_year"])
                    except ValueError:
                        del lead_data["passing_year"]

                if "percentage" in lead_data:
                    try:
                        lead_data["percentage"] = float(lead_data["percentage"])
                    except ValueError:
                        del lead_data["percentage"]

                parsed_rows.append((row_idx, lead_data))
            except Exception as e:
                errors.append({"row": row_idx, "error": str(e)})
                failures += 1

        # --- Phase 2: Batch duplicate check (2 queries total instead of 2 per row) ---
        all_phones = {d.get("phone") for _, d in parsed_rows if d.get("phone")}
        all_emails = {d.get("email") for _, d in parsed_rows if d.get("email")}

        existing_phones: set[str] = set()
        existing_emails: set[str] = set()

        if all_phones:
            from sqlalchemy import or_
            result = await self.db.execute(
                select(Lead.phone).where(Lead.phone.in_(all_phones), Lead.company_id == self.company_id)
            )
            existing_phones = {r[0] for r in result.fetchall() if r[0]}

        if all_emails:
            result = await self.db.execute(
                select(Lead.email).where(Lead.email.in_(all_emails), Lead.company_id == self.company_id)
            )
            existing_emails = {r[0] for r in result.fetchall() if r[0]}

        # Track phones/emails within this batch to catch intra-file duplicates
        seen_phones: set[str] = set()
        seen_emails: set[str] = set()

        # --- Phase 3: Build lead dicts for true bulk insert ---
        lead_dicts = []
        for row_idx, lead_data in parsed_rows:
            try:
                phone = lead_data.get("phone")
                email = lead_data.get("email")

                is_dup = False
                if phone and (phone in existing_phones or phone in seen_phones):
                    is_dup = True
                if not is_dup and email and (email in existing_emails or email in seen_emails):
                    is_dup = True

                if is_dup:
                    duplicates += 1
                    continue

                if phone:
                    seen_phones.add(phone)
                if email:
                    seen_emails.add(email)

                lead_data["company_id"] = self.company_id
                lead_data["current_stage"] = LeadStage.LEAD
                lead_data["assigned_agent_id"] = assigned_agent_id
                lead_data["lead_source_id"] = lead_source_id
                lead_data["created_by"] = user.id
                lead_dicts.append(lead_data)
                success += 1
            except Exception as e:
                errors.append({"row": row_idx, "error": str(e)})
                failures += 1

        # True bulk insert in batches of 500 (single INSERT with multiple VALUES)
        from sqlalchemy import insert
        BATCH_SIZE = 500
        for i in range(0, len(lead_dicts), BATCH_SIZE):
            batch = lead_dicts[i:i + BATCH_SIZE]
            if batch:
                await self.db.execute(insert(Lead), batch)

        csv_import.success_count = success
        csv_import.failure_count = failures
        csv_import.duplicate_count = duplicates
        csv_import.error_details = errors
        csv_import.status = CSVImportStatus.COMPLETED

        # Notification
        notif = Notification(
            company_id=self.company_id,
            user_id=user.id,
            type=NotificationType.CSV_IMPORT_COMPLETE,
            title="CSV Import Complete",
            message=f"{csv_import.file_name}: {success} created, {duplicates} duplicates, {failures} failed",
        )
        self.db.add(notif)

        await self.db.commit()
        await self.db.refresh(csv_import)
        return csv_import

    async def get_status(self, import_id: uuid.UUID, user: Profile) -> CSVImport:
        return await self._get_import(import_id, user)

    async def get_history(self) -> list[CSVImport]:
        result = await self.db.execute(
            select(CSVImport)
            .where(CSVImport.company_id == self.company_id)
            .order_by(CSVImport.created_at.desc())
            .limit(100)
        )
        return result.scalars().all()

    async def _get_import(self, import_id: uuid.UUID, user: Profile) -> CSVImport:
        result = await self.db.execute(
            select(CSVImport).where(CSVImport.id == import_id, CSVImport.company_id == self.company_id)
        )
        csv_import = result.scalar_one_or_none()
        if not csv_import:
            raise NotFoundError("CSV import not found")
        if user.role == UserRole.TELECALLER and csv_import.uploaded_by != user.id:
            raise ForbiddenError("Not authorized")
        return csv_import
