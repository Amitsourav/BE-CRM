from __future__ import annotations

import uuid
import logging
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.company import Company
from app.models.csv_import import CSVImport
from app.models.lead import Lead
from app.models.lead_source import LeadSource
from app.models.notification import Notification
from app.models.profile import Profile
from app.core.constants import (
    CSVImportStatus, LeadStage, NotificationType, UserRole,
    RESTRICTED_VIEW_ROLES, get_initial_stage_for_brand,
)
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

        # Serialize concurrent imports for the same company so the
        # "check existing phones then insert" dance can't race between two
        # uploads and produce duplicate leads. Advisory lock is cheap, scoped
        # to this tenant, and released explicitly in the finally block.
        lock_key = f"csv_import:{self.company_id}"
        await self.db.execute(
            text("SELECT pg_advisory_lock(hashtext(:key))"), {"key": lock_key}
        )

        try:
            return await self._process_locked(
                csv_import, user, column_mapping, content,
                assigned_agent_id, lead_source_id,
            )
        finally:
            await self.db.execute(
                text("SELECT pg_advisory_unlock(hashtext(:key))"), {"key": lock_key}
            )

    async def _process_locked(
        self,
        csv_import: CSVImport,
        user: Profile,
        column_mapping: dict[str, str],
        content: bytes,
        assigned_agent_id: uuid.UUID | None,
        lead_source_id: uuid.UUID | None,
    ) -> CSVImport:
        headers, rows = parse_csv_content(content, self.settings.csv_max_rows)

        slug_result = await self.db.execute(
            select(Company.slug).where(Company.id == self.company_id)
        )
        initial_stage = get_initial_stage_for_brand(slug_result.scalar_one_or_none()).value

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
                    if not lead_data.get("phone"):
                        errors.append({"row": row_idx, "error": "Missing full_name and phone"})
                        failures += 1
                        continue
                    lead_data["full_name"] = "Lead"

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

                # FMC loan_amount: must be plain numeric Lakhs ("25", "300",
                # "30.5"). Reject anything with letters or suffixes — the
                # team has been bitten by "25cr"/"25lakh" values diverging
                # from the convention. Send the row to errors so the user
                # fixes the file rather than silently dropping the field.
                if "loan_amount" in lead_data:
                    raw = lead_data["loan_amount"]
                    try:
                        float(raw)
                        lead_data["loan_amount"] = raw
                    except ValueError:
                        errors.append({
                            "row": row_idx,
                            "error": (
                                f"Loan Amount must be a number in Lakhs "
                                f"(got '{raw}'). e.g. 25 for 25L, 300 for 3Cr."
                            ),
                        })
                        failures += 1
                        continue
                    # Mirror to the numeric loan_amount_lakh column so the
                    # Kanban budget filter works on imported rows. CSV
                    # values are already numeric-validated above, so the
                    # parser will always return a Decimal here.
                    from app.utils.loan_parser import parse_loan_amount
                    lead_data["loan_amount_lakh"] = parse_loan_amount(raw)

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
            result = await self.db.execute(
                select(Lead.phone).where(
                    Lead.phone.in_(all_phones),
                    Lead.company_id == self.company_id,
                    Lead.is_deleted == False,  # noqa: E712
                )
            )
            existing_phones = {r[0] for r in result.fetchall() if r[0]}

        if all_emails:
            result = await self.db.execute(
                select(Lead.email).where(
                    Lead.email.in_(all_emails),
                    Lead.company_id == self.company_id,
                    Lead.is_deleted == False,  # noqa: E712
                )
            )
            existing_emails = {r[0] for r in result.fetchall() if r[0]}

        # Track phones/emails within this batch to catch intra-file duplicates
        seen_phones: set[str] = set()
        seen_emails: set[str] = set()

        # --- Phase 3: Build lead dicts for true bulk insert ---
        lead_dicts = []
        # Cache of source name → source_id resolved during THIS import so
        # we don't re-query the DB for every row. Pre-seeded with all
        # existing sources for this company.
        source_name_cache: dict[str, uuid.UUID] = {}
        existing_sources = (await self.db.execute(
            select(LeadSource.id, LeadSource.name)
            .where(LeadSource.company_id == self.company_id)
        )).all()
        for sid, sname in existing_sources:
            source_name_cache[sname.strip().lower()] = sid
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

                # Per-row source override. When the CSV has a "source"
                # column (any of its aliases), each lead is tagged with
                # that label — auto-creating a lead_sources row the first
                # time we see a new name in this upload. Falls back to
                # the dropdown's lead_source_id when the cell is empty.
                row_source_name = lead_data.pop("source", None)
                if row_source_name:
                    cleaned = row_source_name.strip()
                    if cleaned:
                        lead_data["lead_source_id"] = await self._get_or_create_source(
                            cleaned, user.id, source_name_cache,
                        )
                    else:
                        lead_data["lead_source_id"] = lead_source_id
                else:
                    lead_data["lead_source_id"] = lead_source_id

                lead_data["company_id"] = self.company_id
                lead_data["current_stage"] = initial_stage
                lead_data["assigned_agent_id"] = assigned_agent_id
                lead_data["csv_import_id"] = csv_import.id
                lead_data["created_by"] = user.id
                lead_dicts.append(lead_data)
                success += 1
            except Exception as e:
                errors.append({"row": row_idx, "error": str(e)})
                failures += 1

        # Reserve a contiguous block of per-company serial numbers for
        # the whole import in ONE atomic UPDATE on company_lead_counters,
        # then distribute them across the rows before bulk insert. Way
        # cheaper than per-row reservation for 1000-row uploads.
        if lead_dicts:
            from sqlalchemy import text as sa_text
            row = (await self.db.execute(
                sa_text(
                    """
                    INSERT INTO company_lead_counters (company_id, next_serial)
                    VALUES (:cid, :inc + 1)
                    ON CONFLICT (company_id) DO UPDATE
                      SET next_serial = company_lead_counters.next_serial + :inc,
                          updated_at = now()
                    RETURNING next_serial - :inc AS start_serial
                    """
                ),
                {"cid": self.company_id, "inc": len(lead_dicts)},
            )).first()
            start_serial = int(row.start_serial)
            for i, d in enumerate(lead_dicts):
                d["serial_no"] = start_serial + i

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

    async def _get_or_create_source(
        self,
        name: str,
        created_by: uuid.UUID,
        cache: dict[str, uuid.UUID],
    ) -> uuid.UUID:
        """Find an existing lead_sources row by name (case-insensitive)
        or create one. Cached in `cache` so the same name within one
        upload reuses the same row without a re-query. New sources are
        marked source_type='csv' so admin can spot CSV-originated
        entries in the Sources page.
        """
        key = name.strip().lower()
        if key in cache:
            return cache[key]
        # Create — race-safe because Phase 3 holds the company advisory
        # lock from process_locked, so two concurrent imports for the same
        # company can't double-create the same source.
        new_source = LeadSource(
            company_id=self.company_id,
            name=name.strip(),
            source_type="csv",
            is_active=True,
        )
        self.db.add(new_source)
        await self.db.flush()
        cache[key] = new_source.id
        return new_source.id

    async def _get_import(self, import_id: uuid.UUID, user: Profile) -> CSVImport:
        result = await self.db.execute(
            select(CSVImport).where(CSVImport.id == import_id, CSVImport.company_id == self.company_id)
        )
        csv_import = result.scalar_one_or_none()
        if not csv_import:
            raise NotFoundError("CSV import not found")
        if user.role in RESTRICTED_VIEW_ROLES and csv_import.uploaded_by != user.id:
            raise ForbiddenError("Not authorized")
        return csv_import
