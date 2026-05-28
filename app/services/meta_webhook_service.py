from __future__ import annotations

import uuid
import logging
import httpx
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.lead import Lead
from app.models.lead_source import LeadSource
from app.models.lead_stage_log import LeadStageLog
from app.models.notification import Notification
from app.models.profile import Profile
from app.core.constants import LeadStage, LeadSourceType, UserRole, NotificationType
from app.config import get_settings

logger = logging.getLogger(__name__)


class MetaWebhookService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.settings = get_settings()

    async def process_webhook(self, data: dict) -> None:
        """Process incoming Meta Lead Ads webhook payload."""
        entries = data.get("entry", [])
        for entry in entries:
            changes = entry.get("changes", [])
            for change in changes:
                if change.get("field") == "leadgen":
                    value = change.get("value", {})
                    leadgen_id = value.get("leadgen_id")
                    form_id = value.get("form_id")
                    if leadgen_id:
                        await self._process_lead(leadgen_id, form_id)

    async def _process_lead(self, leadgen_id: str, form_id: str | None) -> None:
        """Fetch lead data from Graph API, look up the form_id in
        meta_form_routing, and either:
          • create the lead locally (target='fmc'), OR
          • forward to the AV backend's internal ingest endpoint (target='av')
          • drop (no mapping → unknown form, possibly spam)
        """
        try:
            lead_data = await self._fetch_lead_from_graph(leadgen_id)
            if not lead_data:
                logger.error("Meta: failed to fetch lead data for %s", leadgen_id)
                return

            # Parse Graph API field_data into a flat dict
            fields = {}
            for f in lead_data.get("field_data", []):
                name = f.get("name", "").lower()
                values = f.get("values", [])
                if values:
                    fields[name] = values[0]

            full_name = fields.get("full_name") or fields.get("name", "Unknown")
            email = fields.get("email")
            phone = fields.get("phone_number") or fields.get("phone")

            # Look up the form's routing. Missing form_id → can't route → drop.
            if not form_id:
                logger.warning("Meta: webhook with no form_id, leadgen=%s — dropping", leadgen_id)
                return
            from app.models.meta_form_routing import MetaFormRouting
            routing = (await self.db.execute(
                select(MetaFormRouting).where(MetaFormRouting.form_id == str(form_id))
            )).scalar_one_or_none()
            if not routing:
                logger.warning(
                    "Meta: unmapped form_id=%s (leadgen=%s, phone=%s) — add a row in meta_form_routing to ingest",
                    form_id, leadgen_id, phone,
                )
                return

            payload = {
                "full_name": full_name,
                "email": email,
                "phone": phone,
                "city": fields.get("city"),
                "state": fields.get("state"),
                "form_id": str(form_id),
                "leadgen_id": str(leadgen_id),
                "source_id": str(routing.source_id) if routing.source_id else None,
                "extra_fields": {k: v for k, v in fields.items() if k not in {"full_name", "name", "email", "phone_number", "phone", "city", "state"}},
            }

            if routing.target == "fmc":
                # Create locally on this DB.
                await self._ingest_lead_local(payload, routing)
            elif routing.target == "av":
                # Forward to AV backend's internal endpoint.
                await self._forward_to_av(payload)
            else:
                logger.error("Meta: unknown target %r for form_id=%s", routing.target, form_id)

        except Exception:
            logger.exception("Meta: error processing leadgen=%s form_id=%s", leadgen_id, form_id)
            await self.db.rollback()

    async def _ingest_lead_local(self, payload: dict, routing) -> None:
        """Create the lead on THIS backend's DB. Uses the company_id of
        the LeadSource pointed to by the routing row — if no source_id,
        falls back to a 'Meta Ads' source per company (auto-creating it
        the first time). Multi-tenant safe: we only ever write to one
        company's DB anyway, but we resolve company_id from the source
        rather than a global default.
        """
        from app.utils.csv_parser import normalize_phone
        from app.services.lead_service import LeadService

        # Resolve company_id from source_id
        company_id = None
        if routing.source_id:
            row = (await self.db.execute(
                select(LeadSource.company_id).where(LeadSource.id == routing.source_id)
            )).first()
            if row:
                company_id = row[0]
        if not company_id:
            logger.error("Meta: routing has no resolvable company_id for source=%s — dropping",
                         routing.source_id)
            return

        svc = LeadService(self.db, company_id)
        phone = normalize_phone(payload["phone"]) if payload.get("phone") else None

        # Dedup at the tenant level
        if phone:
            exists = (await self.db.execute(
                select(Lead.id).where(
                    Lead.company_id == company_id,
                    Lead.phone == phone,
                    Lead.is_deleted == False,  # noqa: E712
                )
            )).first()
            if exists:
                logger.info("Meta: duplicate phone %s in tenant %s — skipping", phone, company_id)
                return

        # Use the standard create_lead path so serial_no, current_stage,
        # initial stage_log, etc. all flow through the same code as form/CSV.
        data = {
            "full_name": payload["full_name"],
            "email": payload.get("email"),
            "phone": phone,
            "city": payload.get("city"),
            "state": payload.get("state"),
            "lead_source_id": routing.source_id,
            "custom_fields": {
                "meta_leadgen_id": payload["leadgen_id"],
                "meta_form_id": payload["form_id"],
                **payload.get("extra_fields", {}),
            },
        }
        # Use the first admin in this tenant as the creator (system actor)
        admin = (await self.db.execute(
            select(Profile).where(Profile.company_id == company_id, Profile.role == "admin").limit(1)
        )).scalar_one_or_none()
        creator_id = admin.id if admin else None
        try:
            lead = await svc.create_lead(data, creator_id, creator_role=None)
            logger.info("Meta: created lead %s (#%s) on tenant %s from form %s",
                        lead.id, lead.serial_no, company_id, payload["form_id"])
        except Exception:
            logger.exception("Meta: local ingest failed for form %s", payload["form_id"])

    async def _forward_to_av(self, payload: dict) -> None:
        """POST the parsed lead to AV backend's internal ingest endpoint."""
        url = (self.settings.av_backend_url or "").rstrip("/")
        secret = self.settings.internal_meta_secret
        if not url or not secret:
            logger.error("Meta: av_backend_url or internal_meta_secret missing — can't forward")
            return
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{url}/api/v1/internal/meta/ingest",
                    json=payload,
                    headers={"X-Internal-Secret": secret},
                )
                if resp.status_code >= 400:
                    logger.error("Meta: AV ingest returned %d: %s", resp.status_code, resp.text[:200])
                else:
                    logger.info("Meta: forwarded leadgen=%s to AV (status=%d)",
                                payload["leadgen_id"], resp.status_code)
        except Exception:
            logger.exception("Meta: failed to forward to AV")

    async def _fetch_lead_from_graph(self, leadgen_id: str) -> dict | None:
        """Fetch lead data from Facebook Graph API."""
        if not self.settings.meta_access_token:
            logger.warning("META_ACCESS_TOKEN not configured, skipping Graph API fetch")
            return None

        url = f"https://graph.facebook.com/v18.0/{leadgen_id}"
        params = {"access_token": self.settings.meta_access_token}

        async with httpx.AsyncClient() as client:
            response = await client.get(url, params=params)
            if response.status_code == 200:
                return response.json()
            logger.error("Graph API error %d: %s", response.status_code, response.text)
            return None

    async def _get_next_agent(self) -> Profile | None:
        """Round-robin: get agent with fewest leads."""
        result = await self.db.execute(
            select(Profile)
            .where(Profile.role == UserRole.PRE_COUNSELLOR, Profile.is_active == True)
            .outerjoin(Lead, Lead.assigned_agent_id == Profile.id)
            .group_by(Profile.id)
            .order_by(func.count(Lead.id).asc())
            .limit(1)
        )
        return result.scalar_one_or_none()
