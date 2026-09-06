from __future__ import annotations

import asyncio
import time
import uuid
import logging
from datetime import date
from decimal import Decimal
from sqlalchemy import select, func, or_, update
from sqlalchemy.ext.asyncio import AsyncSession


# In-memory TTL cache for the Kanban endpoint. Keyed by
# (company_id, user_id, hash_of_filter_args). The Kanban does not need
# to be real-time — a 15-second staleness window means a counsellor who
# clicks Pipeline twice in quick succession gets an instant second
# paint, while edits propagate within 15s. The cache is per-process;
# multiple Railway workers each maintain their own (acceptable since
# misses just hit the DB again).
_KANBAN_CACHE_TTL_S = 15.0
_kanban_cache: dict[tuple, tuple[float, dict]] = {}


def _kanban_cache_get(key: tuple) -> dict | None:
    hit = _kanban_cache.get(key)
    if not hit:
        return None
    expires_at, payload = hit
    if time.monotonic() > expires_at:
        _kanban_cache.pop(key, None)
        return None
    return payload


def _kanban_cache_set(key: tuple, payload: dict) -> None:
    _kanban_cache[key] = (time.monotonic() + _KANBAN_CACHE_TTL_S, payload)
    # Cheap LRU-ish eviction so this never grows unbounded under load.
    if len(_kanban_cache) > 256:
        # Drop the oldest 32 entries
        for k in list(_kanban_cache.keys())[:32]:
            _kanban_cache.pop(k, None)


def invalidate_kanban_cache_for_company(company_id) -> None:
    """Drop every cached /by-stage payload for a tenant. Call after any
    write that changes a lead's display state — stage transitions,
    task completions, due_date changes — so the next Kanban refresh
    sees fresh data instead of waiting out the 15s TTL.

    company_id is the first element of the cache key (see list_leads_by_stage).
    """
    keys = [k for k in _kanban_cache.keys() if k and k[0] == company_id]
    for k in keys:
        _kanban_cache.pop(k, None)
from sqlalchemy.orm import selectinload
from app.models.lead import Lead
from app.core.constants import LeadSourceType
from app.models.lead_source import LeadSource
from app.models.profile import Profile
from app.models.lead_stage_log import LeadStageLog
from app.models.task import Task
from app.models.call_attempt import CallAttempt
from app.models.campaign_lead import CampaignLead
from app.models.company import Company
from app.core.exceptions import (
    NotFoundError, ForbiddenError, BadRequestError, DuplicateLeadError,
)
from app.core.constants import (
    UserRole, LeadStage, RESTRICTED_VIEW_ROLES,
    TaskType, TaskStatus,
    get_initial_stage_for_brand,
    get_stages_for_pipeline,
)
from app.utils.pagination import paginate
from app.utils.date_helpers import now_utc

logger = logging.getLogger(__name__)


def _pin_closure_month(data: dict) -> None:
    """Snap `expected_closure_month` to the 1st of its month.

    It is a MONTH, and storing a real date keeps date_trunc and range
    filters working without string parsing. Pinning on write means the
    stored value never implies a precision the field does not have — a
    target of "some time in July" must not read as 17-Jul.
    """
    v = data.get("expected_closure_month")
    if v is not None and getattr(v, "day", 1) != 1:
        data["expected_closure_month"] = v.replace(day=1)


async def reserve_serial_numbers(
    db: AsyncSession, company_id: uuid.UUID, count: int = 1,
) -> int:
    """Atomically reserve `count` consecutive lead serials for a tenant.

    Returns the FIRST serial in the range; the caller owns
    [start, start+count). Backed by company_lead_counters, where the
    ON CONFLICT DO UPDATE takes a row lock, so concurrent reservations
    queue instead of colliding.

    The GREATEST(...) is a self-heal, and it is load-bearing. The counter
    is only authoritative while every insert goes through this function;
    anything that writes leads.serial_no directly — a migration, a
    one-off script, an edit in the Supabase table editor — leaves the
    counter behind the table. FMC hit exactly that on 2026-09-01: six
    rows landed at 9674-9679 while the counter stayed at 9674.

    That failure does not recover on its own. The reservation and the
    INSERT share one transaction, so the unique violation on
    (company_id, serial_no) rolls the counter increment back too, and the
    next attempt reserves the same doomed number. FMC created zero leads
    for a day — every form, import, Meta ingest and website conversion
    500'd — until the counter was dragged forward by hand.

    Clamping to max(serial_no)+1 makes the very next call repair it. The
    subquery is an index-only scan on uniq_leads_serial_per_company, so
    the cost is a few microseconds against a reservation that is already
    doing a write.
    """
    from sqlalchemy import text as sa_text
    result = (await db.execute(
        sa_text(
            """
            INSERT INTO company_lead_counters (company_id, next_serial)
            VALUES (
                :cid,
                COALESCE(
                    (SELECT max(serial_no) + 1 FROM leads WHERE company_id = :cid),
                    1
                ) + :inc
            )
            ON CONFLICT (company_id) DO UPDATE
              SET next_serial = GREATEST(
                    company_lead_counters.next_serial,
                    COALESCE(
                        (SELECT max(serial_no) + 1 FROM leads WHERE company_id = :cid),
                        1
                    )
                  ) + :inc,
                  updated_at = now()
            RETURNING next_serial - :inc AS start_serial
            """
        ),
        {"cid": company_id, "inc": count},
    )).first()
    return int(result.start_serial)


class LeadService:
    def __init__(self, db: AsyncSession, company_id: uuid.UUID):
        self.db = db
        self.company_id = company_id
        self._slug: str | None = None

    async def _get_slug(self) -> str:
        """Tenant brand slug (lowercased), cached per service instance.
        Used to brand-gate FMC-only vs Admitverse-only features."""
        if self._slug is None:
            result = await self.db.execute(
                select(Company.slug).where(Company.id == self.company_id)
            )
            self._slug = (result.scalar_one_or_none() or "").lower()
        return self._slug

    async def _ensure_callback_task(
        self,
        lead: Lead,
        due_date,
        actor_id: uuid.UUID,
    ) -> bool:
        """Auto-create a follow-up Task when a lead's callback date is set.

        Telecallers were setting `lead.due_date` directly via PUT /leads/{id}
        (the lead-edit form) and call_service.log_call wasn't getting hit, so
        no task surfaced on their Tasks page. This helper materialises the
        task on any path that sets due_date.

        Idempotent — if a pending CALL task already exists for this lead at
        the same due_date, do nothing. The actor_id is used as a fallback
        assignee when the lead has no assigned agent yet.
        Returns True when a task was created.
        """
        if not due_date:
            return False
        existing = await self.db.execute(
            select(Task.id).where(
                Task.lead_id == lead.id,
                Task.company_id == self.company_id,
                Task.task_type == TaskType.CALL.value,
                Task.due_date == due_date,
                Task.status.in_([
                    TaskStatus.PENDING.value, TaskStatus.IN_PROGRESS.value,
                    TaskStatus.OVERDUE.value,
                ]),
            )
        )
        if existing.scalar_one_or_none():
            return False

        # Prefer the actual lead owner over the actor: Counsellor first
        # (assigned_agent_id), then Pre-Counsellor (pre_counsellor_id),
        # then fall back to whoever triggered this create. Previously
        # admin-uploaded CSVs with no assigned_agent_id pinned every
        # callback task to the admin — the pre_counsellor who owned
        # the lead couldn't act on it.
        assignee = lead.assigned_agent_id or getattr(lead, "pre_counsellor_id", None) or actor_id
        title = f"Callback: {lead.full_name}"
        self.db.add(Task(
            company_id=self.company_id,
            lead_id=lead.id,
            assigned_to=assignee,
            created_by=actor_id,
            task_type=TaskType.CALL.value,
            title=title,
            description=None,
            status=TaskStatus.PENDING.value,
            due_date=due_date,
        ))
        return True

    async def _reserve_serial_numbers(self, count: int = 1) -> int:
        """Atomically reserve `count` consecutive serial numbers for this
        tenant. Returns the FIRST serial in the reserved range — caller
        uses [start, start+count) for the leads it's about to insert.

        See `reserve_serial_numbers` for the locking and self-healing
        rules; this is the LeadService-bound wrapper.
        """
        return await reserve_serial_numbers(self.db, self.company_id, count)

    async def _require_source(
        self, data: dict, created_by: uuid.UUID, fallback: str | None,
    ) -> None:
        """Every lead must say where it came from.

        Source was optional until 2026-09-06 and 34 students carrying
        Rs 5.97 crore of disbursement — 38% of the book — ended up with
        none. "Unattributed" was the largest channel on the source
        scorecard, which made the question "which channel makes money"
        unanswerable. A blank in a spreadsheet is visible; a NULL in a
        database is not, so the database has to refuse it.

        An automated caller cannot pick a source, so it names its channel
        and gets a row created on demand. A person gets a 400 — they can
        see the dropdown.
        """
        sid = data.get("lead_source_id")
        if sid:
            exists = (await self.db.execute(
                select(LeadSource.id).where(
                    LeadSource.id == sid,
                    LeadSource.company_id == self.company_id,
                )
            )).scalar_one_or_none()
            if exists is None:
                # Also closes a cross-tenant hole: nothing checked that
                # the id belonged to this company.
                raise BadRequestError(
                    "That lead source does not exist on this account."
                )
            return
        if not fallback:
            raise BadRequestError(
                "A lead source is required. Pick where this lead came "
                "from — without it the lead is invisible to every "
                "channel report."
            )
        # The channel decides the type, so the Sources page can still
        # tell a WhatsApp channel from a website form.
        kind = {
            "WhatsApp": LeadSourceType.WHATSAPP.value,
            "Meta Ads": LeadSourceType.META_ADS.value,
            "Website": LeadSourceType.WEBSITE.value,
        }.get(fallback, LeadSourceType.MANUAL.value)
        data["lead_source_id"] = await self.resolve_source(
            fallback, created_by, source_type=kind,
        )

    async def resolve_source(
        self, name: str, created_by: uuid.UUID | None = None,
        source_type: str = LeadSourceType.MANUAL.value,
    ) -> uuid.UUID:
        """A lead_sources id for `name`, creating the row if new.

        Case-insensitive, so "WhatsApp" and "whatsapp" stay one channel
        rather than splitting the scorecard in two.

        `source_type` is a DB enum — passing anything outside
        `LeadSourceType` fails at the insert, not at the call, so callers
        must use the enum's values.
        """
        if source_type not in {t.value for t in LeadSourceType}:
            raise BadRequestError(
                f"'{source_type}' is not a lead source type. Use one of "
                f"{sorted(t.value for t in LeadSourceType)}."
            )
        clean = name.strip()
        row = (await self.db.execute(
            select(LeadSource.id).where(
                LeadSource.company_id == self.company_id,
                func.lower(LeadSource.name) == clean.lower(),
            )
        )).scalar_one_or_none()
        if row:
            return row
        src = LeadSource(
            company_id=self.company_id, name=clean,
            source_type=source_type, is_active=True,
        )
        self.db.add(src)
        await self.db.flush()
        return src.id

    async def create_lead(
        self, data: dict, created_by: uuid.UUID,
        creator_role: str | None = None,
        source_fallback: str | None = None,
    ) -> Lead:
        """Create a lead. A source is mandatory — see `_require_source`.

        `source_fallback` names the channel for automated callers
        (webhooks, the website form, Meta). A human-facing caller passes
        nothing and gets a 400 instead, because a person can pick the
        right source and a webhook cannot.
        """
        data["company_id"] = self.company_id
        await self._require_source(data, created_by, source_fallback)
        _pin_closure_month(data)

        # Auto-own rule (FMC, May 2026):
        #   • Pre-Counsellor creates a lead → set pre_counsellor_id = self
        #     so it shows on their queue immediately. Counsellor slot
        #     stays empty until admin routes the lead.
        #   • Manager creates a lead → set assigned_agent_id = self if
        #     the form didn't already pick one (Manager owns it as
        #     Counsellor).
        #   • Admin's flow is untouched — admin picks the assignee in
        #     the form, or deliberately leaves it null for manual
        #     assignment from the lead list later.
        if creator_role == UserRole.PRE_COUNSELLOR.value:
            data.setdefault("pre_counsellor_id", created_by)
        elif creator_role == UserRole.MANAGER.value:
            data.setdefault("assigned_agent_id", created_by)

        # Normalize phone to +91 format so dedup catches "7004428198" vs
        # "+917004428198" vs "7004 428 198" as the same number.
        if data.get("phone"):
            from app.utils.csv_parser import normalize_phone
            data["phone"] = normalize_phone(data["phone"])

        # Duplicate check on phone and email — same rule the CSV importer
        # applies. Without this, the Add Lead form was creating duplicates
        # (e.g. "amit"/7004428198 vs "Amit"/7004428198 living side-by-side
        # in different stages). Per-tenant scoped (company_id) and skips
        # soft-deleted rows.
        # The 400 carries the existing lead's id (see DuplicateLeadError).
        # Without it an API client that loses a create response has no way
        # back to the row it just hit: the only alternative is /leads/search,
        # which is a substring ILIKE and can match several leads — or none,
        # when the caller's role can't see the one that collided.
        if data.get("phone"):
            # Matched on the 10 national digits, not the stored string —
            # see phone_match_clause. Comparing the normalised incoming
            # value against the raw column missed every row saved in a
            # non-canonical format, so "+917004428198" sailed past an
            # existing "7004428198" and created a second lead.
            from app.utils.csv_parser import phone_match_clause
            existing = (await self.db.execute(
                select(Lead.id, Lead.full_name).where(
                    Lead.company_id == self.company_id,
                    phone_match_clause(Lead.phone, data["phone"]),
                    Lead.is_deleted == False,  # noqa: E712
                )
            )).first()
            if existing:
                raise DuplicateLeadError(
                    field="phone",
                    value=data["phone"],
                    existing_id=existing.id,
                    existing_name=existing.full_name,
                )
        if data.get("email"):
            # Compared case-insensitively to match the actual constraint,
            # `uniq_leads_email_active ON leads (company_id, lower(email))`.
            # An exact-match check let "Foo@x.com" past the service gate when
            # "foo@x.com" already existed, so the collision surfaced from the
            # index as an uncaught IntegrityError → 500 (internals leaked by
            # the generic handler) instead of this readable 400. A 500 is
            # also indistinguishable from an outage to a retrying client,
            # which turns one bad email into a retry loop.
            existing = (await self.db.execute(
                select(Lead.id, Lead.full_name).where(
                    Lead.company_id == self.company_id,
                    func.lower(Lead.email) == data["email"].lower(),
                    Lead.is_deleted == False,  # noqa: E712
                )
            )).first()
            if existing:
                raise DuplicateLeadError(
                    field="email",
                    value=data["email"],
                    existing_id=existing.id,
                    existing_name=existing.full_name,
                )

        slug = await self._get_slug()
        initial_stage = get_initial_stage_for_brand(slug)
        data.setdefault("current_stage", initial_stage.value)

        # Mirror loan_amount → loan_amount_lakh (numeric, in lakhs) so the
        # Kanban budget filter can compare numbers without parsing text
        # in the query. Display column stays untouched.
        if data.get("loan_amount") is not None:
            from app.utils.loan_parser import parse_loan_amount
            data["loan_amount_lakh"] = parse_loan_amount(data["loan_amount"])

        # Admitverse: mirror budget → budget_amount + budget_currency
        # (multi-currency) so the AV Kanban budget filter compares numbers.
        if data.get("budget") is not None:
            from app.utils.budget_parser import parse_budget
            amount, currency = parse_budget(data["budget"])
            data["budget_amount"] = amount
            if currency:
                data["budget_currency"] = currency

        # AV's study-abroad checklist has 8 docs vs FMC's 6 — set the
        # per-brand default on create (leaves FMC's server_default of 6).
        if slug == "admitverse" and not data.get("docs_required"):
            from app.core.constants import AV_DOC_CHECKLIST
            data["docs_required"] = len(AV_DOC_CHECKLIST)

        # Reserve a per-tenant serial number so the lead shows up as
        # #N on the Kanban card and lead detail page. Won't overwrite
        # serial_no if the caller already set one (e.g. a future
        # admin restore flow).
        if "serial_no" not in data or data.get("serial_no") is None:
            data["serial_no"] = await self._reserve_serial_numbers(1)

        lead = Lead(**data, created_by=created_by)
        self.db.add(lead)
        await self.db.flush()

        # Create initial stage log
        stage_log = LeadStageLog(
            company_id=self.company_id,
            lead_id=lead.id,
            from_stage=None,
            to_stage=initial_stage.value,
            changed_by=created_by,
        )
        self.db.add(stage_log)

        # If the lead is created with a due_date already set, auto-queue a
        # callback task so it shows on the assignee's Tasks page.
        if lead.due_date:
            await self._ensure_callback_task(lead, lead.due_date, created_by)

        await self.db.commit()
        await self.db.refresh(lead)
        return lead

    async def get_lead(self, lead_id: uuid.UUID, user: Profile) -> Lead:
        result = await self.db.execute(
            select(Lead).where(
                Lead.id == lead_id,
                Lead.company_id == self.company_id,
                Lead.is_deleted == False,
            )
        )
        lead = result.scalar_one_or_none()
        if not lead:
            raise NotFoundError("Lead not found")
        if user.role in RESTRICTED_VIEW_ROLES and lead.assigned_agent_id != user.id and lead.pre_counsellor_id != user.id:
            raise ForbiddenError("Not authorized to view this lead")
        return lead

    async def update_lead(self, lead_id: uuid.UUID, data: dict, user: Profile) -> Lead:
        lead = await self.get_lead(lead_id, user)
        _pin_closure_month(data)
        prev_due_date = lead.due_date
        prev_stage = lead.current_stage

        # If current_stage is being changed, route through StageMachine
        # so transition rules, lost_reason gating, notes requirements,
        # AND the LeadStageLog timeline entry all happen. Skipping this
        # was the bug that let the FE drag-drop into Lost without a
        # remark, no timeline trace, and no validation.
        new_stage = data.pop("current_stage", None)
        transition_notes = data.pop("conversation_notes", None)
        transition_agenda = data.pop("agent_agenda", None)
        transition_lost_reason = data.pop("lost_reason", None)
        transition_due_date = data.get("due_date")  # peek; let normal path also apply it

        if new_stage and new_stage != prev_stage:
            from app.services.stage_machine import StageMachine
            machine = StageMachine(self.db, self.company_id)
            await machine.transition(
                lead_id=lead.id,
                to_stage=new_stage,
                user=user,
                conversation_notes=transition_notes,
                agent_agenda=transition_agenda,
                due_date=transition_due_date,
                lost_reason=transition_lost_reason,
            )
            # StageMachine.transition() commits internally — re-fetch so
            # we apply the rest of the user's edits to the latest row.
            lead = await self.get_lead(lead_id, user)
            prev_due_date = lead.due_date  # avoid double-creating the callback task

        # DNP-N change (FMC, May 2026): when the user manually moves a
        # lead's DNP attempt counter via the Kanban card's "DNP-1..DNP-6"
        # dropdown, we require a note explaining the change — same UX as
        # a stage transition. The note lands on the lead's remarks
        # timeline so it shows up in the activity feed.
        # Skipped when dnp_count was auto-incremented inside StageMachine
        # (which re-fetched the lead, so lead.dnp_count == new value
        # already and this check is a no-op).
        new_dnp = data.get("dnp_count")
        if new_dnp is not None and new_dnp != (lead.dnp_count or 0):
            note = (transition_notes or "").strip()
            if not note:
                raise BadRequestError(
                    f"A note is required when changing DNP attempt count "
                    f"(DNP-{lead.dnp_count or 0} → DNP-{new_dnp})."
                )
            from app.models.lead_remark import LeadRemark
            self.db.add(LeadRemark(
                company_id=self.company_id,
                lead_id=lead.id,
                author_id=user.id,
                author_role=user.role,
                body=f"DNP-{lead.dnp_count or 0} → DNP-{new_dnp}: {note}",
            ))

        # Validate bank_name against the canonical FMC bank list (FMC only).
        # Same rationale as lost_reason — free text was producing case/spelling
        # variants that broke reporting (sbi / SBI / Unicred / UniCred).
        if "bank_name" in data and data["bank_name"]:
            slug = await self._get_slug()
            if slug != "admitverse":
                from app.services.bank_registry import get_bank_names
                if data["bank_name"] not in await get_bank_names(self.db):
                    raise BadRequestError(
                        f"bank_name must be one of the canonical FMC banks "
                        f"(got '{data['bank_name']}'). See GET /leads/banks."
                    )

        # Mirror loan_amount → loan_amount_lakh on update too, same reason
        # as create_lead. If loan_amount is being explicitly cleared
        # (set to None or empty string), wipe the numeric mirror as well.
        if "loan_amount" in data:
            from app.utils.loan_parser import parse_loan_amount
            data["loan_amount_lakh"] = parse_loan_amount(data["loan_amount"])

        # Mirror budget → budget_amount + budget_currency on update (AV).
        if "budget" in data:
            from app.utils.budget_parser import parse_budget
            amount, currency = parse_budget(data["budget"])
            data["budget_amount"] = amount
            data["budget_currency"] = currency or "INR"

        # Filter submitted_docs to the brand's checklist keys + dedupe.
        # Without this, FE bugs or stale clients could push junk values
        # into the array (trailing whitespace, dupes, removed keys). AV
        # uses the study-abroad keys; FMC uses the loan-doc keys.
        if "submitted_docs" in data and data["submitted_docs"] is not None:
            from app.core.constants import get_doc_keys_for_brand
            doc_keys = get_doc_keys_for_brand(await self._get_slug())
            cleaned = []
            seen = set()
            for k in data["submitted_docs"]:
                k = (k or "").strip().lower()
                if k and k in doc_keys and k not in seen:
                    cleaned.append(k)
                    seen.add(k)
            data["submitted_docs"] = cleaned
            # Auto-sync the counter so existing widgets keep working.
            data["docs_submitted"] = len(cleaned)

        # Phone/email edits go through the same normalisation and duplicate
        # gate as create. Until this existed, PUT /leads/{id} bypassed both,
        # and the lead-edit form could break the one-lead-per-phone rule the
        # rest of the system assumes:
        #
        #   • "07004428198" was stored verbatim. It never collided with the
        #     existing "+917004428198" (different strings, so the partial
        #     unique index saw no conflict) and quietly became a SECOND live
        #     lead for the same person — the dedup silently defeated, no error.
        #   • An exact-match edit did hit the index, but as an uncaught
        #     IntegrityError → 500 with the internals leaked by the generic
        #     handler, rather than a readable 400.
        #
        # Email is compared case-insensitively to match the actual index
        # (`lower(email)`); create still compares exactly, so a create with
        # differing case can still 500 — flagged separately, not changed here.
        if "phone" in data and data["phone"]:
            from app.utils.csv_parser import normalize_phone
            data["phone"] = normalize_phone(data["phone"])

        for _field in ("phone", "email"):
            if _field not in data or not data[_field]:
                continue  # clearing a value can't collide with anything
            if _field == "phone":
                from app.utils.csv_parser import phone_match_clause
                predicate = phone_match_clause(Lead.phone, data["phone"])
            else:
                predicate = func.lower(Lead.email) == data["email"].lower()
            clash = (await self.db.execute(
                select(Lead.id, Lead.full_name).where(
                    Lead.company_id == self.company_id,
                    predicate,
                    # Excluding self: re-saving a lead without touching its
                    # phone must not 400 against its own row.
                    Lead.id != lead.id,
                    Lead.is_deleted == False,  # noqa: E712
                )
            )).first()
            if clash:
                raise DuplicateLeadError(
                    field=_field,
                    value=data[_field],
                    existing_id=clash.id,
                    existing_name=clash.full_name,
                )

        # A lead on the AI board whose stage is advanced past what that
        # board shows would vanish — no column to render it in. That is
        # exactly the dead-end that stranded 1,575 leads at stage 'lead'.
        # Promote it to the normal board instead: advancing a lead into
        # loan processing IS a counsellor taking it over.
        from app.core.constants import AI_PIPELINE_STAGE_VALUES, PIPELINE_AI, PIPELINE_NORMAL
        if (
            getattr(lead, "pipeline", PIPELINE_NORMAL) == PIPELINE_AI
            and lead.current_stage not in AI_PIPELINE_STAGE_VALUES
        ):
            logger.info(
                "LEAD %s auto-promoted to the normal pipeline (stage %s is "
                "outside the AI board)", lead.id, lead.current_stage,
            )
            lead.pipeline = PIPELINE_NORMAL

        for key, value in data.items():
            setattr(lead, key, value)

        # If due_date was set or changed in this update (and not already
        # handled by the transition above), queue a callback task. This
        # is the path telecallers use ("Edit Lead" → schedule next call)
        # without changing the stage.
        new_due_date = lead.due_date
        if new_due_date and new_due_date != prev_due_date:
            await self._ensure_callback_task(lead, new_due_date, user.id)

        await self.db.commit()
        await self.db.refresh(lead)
        return lead

    async def delete_lead(self, lead_id: uuid.UUID) -> None:
        """Soft delete — sets is_deleted=True and deleted_at timestamp."""
        result = await self.db.execute(
            select(Lead).where(
                Lead.id == lead_id,
                Lead.company_id == self.company_id,
                Lead.is_deleted == False,
            )
        )
        lead = result.scalar_one_or_none()
        if not lead:
            raise NotFoundError("Lead not found")
        lead.is_deleted = True
        lead.deleted_at = now_utc()
        await self.db.commit()

    async def list_leads(
        self,
        user: Profile,
        page: int = 1,
        page_size: int = 25,
        stage: str | None = None,
        agent_id: uuid.UUID | None = None,
        source_id: uuid.UUID | None = None,
        csv_import_id: uuid.UUID | None = None,
        campaign_id: uuid.UUID | None = None,
        tags: list[str] | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        # Admin-only segment slice: campaign | unassigned | counsellor | pre_counsellor.
        # Mirrors the /by-stage filter so the Leads list page can show
        # the same "needs distribution" view. Restricted-view roles
        # always see only their own leads regardless of this param.
        lead_segment: str | None = None,
    ) -> dict:
        query = select(Lead).where(Lead.company_id == self.company_id, Lead.is_deleted == False).order_by(Lead.created_at.desc())

        if user.role in RESTRICTED_VIEW_ROLES:
            # Restricted viewers see leads where they're either the Counsellor
            # or the Pre Counsellor (FMC two-step model).
            query = query.where(or_(Lead.assigned_agent_id == user.id, Lead.pre_counsellor_id == user.id))
        elif agent_id:
            # Admin/manager filtering by "agent" — match either role on FMC.
            query = query.where(or_(Lead.assigned_agent_id == agent_id, Lead.pre_counsellor_id == agent_id))

        if stage:
            query = query.where(Lead.current_stage == stage)
        if source_id:
            query = query.where(Lead.lead_source_id == source_id)
        if csv_import_id:
            query = query.where(Lead.csv_import_id == csv_import_id)
        if campaign_id:
            # JOIN with campaign_leads — every lead enrolled in a campaign
            # has a campaign_leads row. distinct() guards the rare case a
            # lead got enrolled twice (shouldn't happen given the unique
            # constraint, but defensive against historical dirty data).
            query = query.join(
                CampaignLead, CampaignLead.lead_id == Lead.id
            ).where(CampaignLead.campaign_id == campaign_id).distinct()
        if tags:
            query = query.where(Lead.tags.overlap(tags))
        if date_from:
            query = query.where(func.date(Lead.created_at) >= date_from)
        if date_to:
            query = query.where(func.date(Lead.created_at) <= date_to)
        # Segment filter — same semantics as /by-stage. "unassigned"
        # excludes campaign leads (those are owned by the AI agent).
        if lead_segment == "unassigned":
            query = query.where(
                Lead.assigned_agent_id.is_(None),
                Lead.pre_counsellor_id.is_(None),
                ~Lead.id.in_(
                    select(CampaignLead.lead_id)
                    .where(CampaignLead.company_id == self.company_id)
                ),
            )
        elif lead_segment == "counsellor":
            query = query.where(Lead.assigned_agent_id.isnot(None))
        elif lead_segment == "pre_counsellor":
            query = query.where(Lead.pre_counsellor_id.isnot(None))
        elif lead_segment == "campaign":
            query = query.where(Lead.id.in_(
                select(CampaignLead.lead_id)
                .where(CampaignLead.company_id == self.company_id)
            ))
        elif lead_segment == "normal":
            # "Normal" (non-AI) leads — never enrolled in any campaign.
            query = query.where(~Lead.id.in_(
                select(CampaignLead.lead_id)
                .where(CampaignLead.company_id == self.company_id)
            ))

        page_data = await paginate(self.db, query, page, page_size)
        # Same enrichment the Kanban /by-stage endpoint applies. Without
        # this, GET /leads?... returns assigned_agent_name=null and
        # task_count=0 even when the lead has data — which makes the
        # FE Kanban (still using /leads list) render empty rows for
        # the agent + badges. 5 batched aggregate queries; bounded by
        # page_size (default 25) so cost stays small.
        await self._enrich_cards(page_data["items"])
        return page_data

    def _apply_kanban_filters(
        self,
        query,
        *,
        q: str | None = None,
        source_id: uuid.UUID | None = None,
        loan_min: float | None = None,
        loan_max: float | None = None,
        bank_name: str | None = None,
        bank_status: str | None = None,
        target_country: str | None = None,
        target_intake: str | None = None,
        tags: list[str] | None = None,
        created_from=None,
        created_to=None,
        due_from=None,
        due_to=None,
        dnp_min: int | None = None,
        dnp_max: int | None = None,
        application_status: str | None = None,
        university: str | None = None,
        budget_min: float | None = None,
        budget_max: float | None = None,
        budget_currency: str = "INR",
        slug: str | None = None,
        important_only: bool = False,
        lead_segment: str | None = None,
    ):
        """Apply Kanban filter set to a query. The visibility gate
        (assigned_agent_id / pre_counsellor_id ANDed in the caller) is
        deliberately NOT touched here — these filters compose on top.
        Same helper feeds both the items query and the counts query so
        the column counters stay consistent with the cards rendered.
        """
        if q:
            term = f"%{q.strip()}%"
            query = query.where(or_(
                Lead.full_name.ilike(term),
                Lead.phone.ilike(term),
                Lead.email.ilike(term),
            ))
        if source_id is not None:
            query = query.where(Lead.lead_source_id == source_id)
        is_av = (slug or "").lower() == "admitverse"
        # FMC-only filters (loan / bank). Ignored on Admitverse — those
        # columns are always NULL there, so applying them would wrongly
        # empty the board.
        if not is_av:
            if loan_min is not None:
                query = query.where(Lead.loan_amount_lakh >= loan_min)
            if loan_max is not None:
                query = query.where(Lead.loan_amount_lakh <= loan_max)
            if bank_name:
                query = query.where(Lead.bank_name == bank_name)
            if bank_status:
                query = query.where(Lead.bank_status == bank_status)
        # Admitverse-only filters (application + budget).
        else:
            if application_status:
                query = query.where(Lead.application_status == application_status)
            if university:
                query = query.where(Lead.primary_university.ilike(f"%{university.strip()}%"))
            if budget_min is not None or budget_max is not None:
                query = query.where(Lead.budget_currency == (budget_currency or "INR"))
                if budget_min is not None:
                    query = query.where(Lead.budget_amount >= budget_min)
                if budget_max is not None:
                    query = query.where(Lead.budget_amount <= budget_max)
        if target_country:
            # preferred_countries is text[] — `any` checks membership.
            query = query.where(Lead.preferred_countries.any(target_country))
        if target_intake:
            query = query.where(Lead.target_intake == target_intake)
        if tags:
            # tags is text[] — `overlap` is "any tag in the filter matches",
            # which is the standard "OR-of-tags" UX. Use `contains` if you
            # ever want "AND-of-tags" instead. Explicit TEXT[] cast or
            # Postgres complains "text[] && varchar[] — no operator".
            from sqlalchemy import cast
            from sqlalchemy.dialects.postgresql import ARRAY
            from sqlalchemy import Text
            query = query.where(Lead.tags.overlap(cast(tags, ARRAY(Text))))
        if created_from is not None:
            query = query.where(Lead.created_at >= created_from)
        if created_to is not None:
            query = query.where(Lead.created_at <= created_to)
        if due_from is not None:
            query = query.where(Lead.due_date >= due_from)
        if due_to is not None:
            query = query.where(Lead.due_date <= due_to)
        if not is_av:
            if dnp_min is not None:
                query = query.where(Lead.dnp_count >= dnp_min)
            if dnp_max is not None:
                query = query.where(Lead.dnp_count <= dnp_max)
        if important_only:
            query = query.where(Lead.is_important == True)  # noqa: E712
        # Admin-facing "segment" filter: slices the pipeline by who owns
        # the leads. FE shows this dropdown only to admin since restricted
        # roles can already only see their own leads.
        if lead_segment == "unassigned":
            # "Truly unassigned" — no human owner AND not in any AI
            # campaign. Leads in a campaign are effectively assigned to
            # the AI agent, so they don't belong in the admin's
            # "needs distribution" pile.
            query = query.where(
                Lead.assigned_agent_id.is_(None),
                Lead.pre_counsellor_id.is_(None),
                ~Lead.id.in_(
                    select(CampaignLead.lead_id)
                    .where(CampaignLead.company_id == self.company_id)
                ),
            )
        elif lead_segment == "counsellor":
            # Lead has been routed to a Counsellor (assigned_agent_id set).
            query = query.where(Lead.assigned_agent_id.isnot(None))
        elif lead_segment == "pre_counsellor":
            # Lead has been picked up by a Pre-Counsellor.
            query = query.where(Lead.pre_counsellor_id.isnot(None))
        elif lead_segment == "campaign":
            # Lead has at least one campaign_leads row — i.e. it's
            # currently in or has been part of an AI / drip campaign.
            query = query.where(Lead.id.in_(
                select(CampaignLead.lead_id)
                .where(CampaignLead.company_id == self.company_id)
            ))
        elif lead_segment == "normal":
            # "Normal" (non-AI) leads — never enrolled in any campaign.
            # The inverse of "campaign"; lets the pipeline show only
            # human-worked leads, hiding AI-calling ones.
            query = query.where(~Lead.id.in_(
                select(CampaignLead.lead_id)
                .where(CampaignLead.company_id == self.company_id)
            ))
        return query

    async def list_leads_by_stage(
        self,
        user: Profile,
        agent_id: uuid.UUID | None = None,
        campaign_id: uuid.UUID | None = None,
        per_stage_limit: int = 50,
        # Filter set added May 2026 for the FMC pipeline page. All
        # optional; FE drops them when not in use. Filters apply to BOTH
        # the items query and the count query so column counts stay in
        # sync with the rendered cards.
        q: str | None = None,
        source_id: uuid.UUID | None = None,
        loan_min: float | None = None,
        loan_max: float | None = None,
        bank_name: str | None = None,
        bank_status: str | None = None,
        target_country: str | None = None,
        target_intake: str | None = None,
        tags: list[str] | None = None,
        created_from=None,
        created_to=None,
        due_from=None,
        due_to=None,
        dnp_min: int | None = None,
        dnp_max: int | None = None,
        # Admitverse-only filters (ignored on FMC).
        application_status: str | None = None,
        university: str | None = None,
        budget_min: float | None = None,
        budget_max: float | None = None,
        budget_currency: str = "INR",
        important_only: bool = False,
        # Admin-only segment slice: campaign | unassigned | counsellor | pre_counsellor.
        # FE only shows this dropdown to admin users; non-admins can't
        # see other people's leads anyway via the visibility gate.
        lead_segment: str | None = None,
        # Which board: 'ai' (campaign leads) or 'normal' (counsellor-worked).
        # None = both, preserving the pre-Aug-2026 behaviour for any caller
        # that hasn't been updated.
        pipeline: str | None = None,
        # Sort: created_desc (default), loan_asc/desc (FMC), budget_asc/desc (AV).
        # Affects only the per-column row order; counts are unchanged.
        sort_by: str = "created_desc",
        # "Show more" support. stage restricts the response to ONE column
        # and offset skips that many rows within it, so the board can pull
        # cards 51-100 of Created without refetching every other column.
        # Both default to the whole-board behaviour every existing caller
        # already gets. See the note above the rn window for why offset is
        # only meaningful alongside stage.
        stage: str | None = None,
        offset: int = 0,
    ) -> dict:
        """Fetch leads grouped by stage in one round trip.

        The Kanban board previously fired one /leads request per stage
        column — 19 round trips for Admitverse, each carrying a separate
        COUNT and SELECT. This walks the table once, partitions by
        current_stage on the frontend, and caps each stage at
        per_stage_limit so we don't ship thousands of cards for a long-tail
        stage. A second tiny query collects total counts so the Kanban can
        show "+N more" if a column is truncated.
        """
        # Cache hit short-circuit. Keyed by the requester (so per-user
        # visibility is preserved), the company, and every filter/scope
        # arg. 15-second TTL — short enough that edits propagate quickly,
        # long enough that the second/third Pipeline click is instant.
        # tags is converted to a frozen tuple so it's hashable.
        slug = await self._get_slug()
        cache_key = (
            self.company_id, user.id, user.role,
            agent_id, campaign_id, per_stage_limit,
            q, source_id, loan_min, loan_max,
            bank_name, bank_status, target_country, target_intake,
            tuple(tags) if tags else None,
            created_from, created_to, due_from, due_to,
            dnp_min, dnp_max,
            application_status, university, budget_min, budget_max, budget_currency,
            important_only,
            lead_segment,
            # MUST be in the key: the AI and normal boards differ only by
            # this argument, so leaving it out made switching boards
            # within the 15s TTL serve the other board's payload —
            # including its stage columns.
            pipeline,
            sort_by,
            # MUST be in the key for the same reason pipeline is: page 2 of
            # Created and the full board differ only by these two args, so
            # leaving them out would serve one as the other inside the 15s
            # TTL — the board would replace itself with a single column.
            stage,
            offset,
        )
        cached = _kanban_cache_get(cache_key)
        if cached is not None:
            return cached

        # Per-stage row cap via a window function: rank rows within their
        # stage by the chosen sort order and keep the top N per stage.
        # One scan, one round trip.
        from sqlalchemy import literal_column, asc, desc, nullslast
        from sqlalchemy.sql import over

        # Resolve sort_by → ORDER BY expression. For loan-amount sorts we
        # put NULLs last in both directions; without nullslast the "Low
        # to High" view would lead with all the unknown-budget leads at
        # the top, which is exactly the noise telecallers want to avoid.
        if sort_by == "loan_asc":
            window_order = (nullslast(Lead.loan_amount_lakh.asc()), Lead.created_at.desc())
        elif sort_by == "loan_desc":
            window_order = (nullslast(Lead.loan_amount_lakh.desc()), Lead.created_at.desc())
        elif sort_by == "budget_asc":
            window_order = (nullslast(Lead.budget_amount.asc()), Lead.created_at.desc())
        elif sort_by == "budget_desc":
            window_order = (nullslast(Lead.budget_amount.desc()), Lead.created_at.desc())
        else:  # "created_desc" (default) — original behavior
            window_order = (Lead.created_at.desc(),)

        rn = func.row_number().over(
            partition_by=Lead.current_stage,
            order_by=window_order,
        ).label("rn")

        base = select(Lead, rn).where(
            Lead.company_id == self.company_id,
            Lead.is_deleted == False,  # noqa: E712
        )
        if user.role in RESTRICTED_VIEW_ROLES:
            base = base.where(or_(Lead.assigned_agent_id == user.id, Lead.pre_counsellor_id == user.id))
        elif agent_id:
            base = base.where(or_(Lead.assigned_agent_id == agent_id, Lead.pre_counsellor_id == agent_id))
        if campaign_id:
            # Kanban scoped to a single campaign. Window function still
            # partitions by stage and caps at per_stage_limit — so the FE
            # shows the most-recent N leads from THIS campaign per column.
            base = base.join(
                CampaignLead, CampaignLead.lead_id == Lead.id
            ).where(CampaignLead.campaign_id == campaign_id)

        # Apply the Kanban filter set on top of the visibility + scope WHEREs.
        base = self._apply_kanban_filters(
            base,
            q=q, source_id=source_id,
            loan_min=loan_min, loan_max=loan_max,
            bank_name=bank_name, bank_status=bank_status,
            target_country=target_country, target_intake=target_intake,
            tags=tags,
            created_from=created_from, created_to=created_to,
            due_from=due_from, due_to=due_to,
            dnp_min=dnp_min, dnp_max=dnp_max,
            application_status=application_status, university=university,
            budget_min=budget_min, budget_max=budget_max, budget_currency=budget_currency,
            slug=slug,
            important_only=important_only,
            lead_segment=lead_segment,
        )
        if pipeline:
            base = base.where(Lead.pipeline == pipeline)
        if stage:
            # Single-column mode. Filtering here rather than after the
            # window keeps the row_number() sequence dense within the one
            # stage we care about, which is what makes offset arithmetic
            # correct — ranking all stages and then discarding the others
            # would still number them 1..N per partition, but we'd scan
            # the whole board to return one column.
            base = base.where(Lead.current_stage == stage)

        sub = base.subquery()
        # Outer ORDER BY matches the window function order so the result
        # set arrives in the right per-card order (stage clustering kept
        # via current_stage, then rn). Without this, Postgres returns
        # rows in physical/hash-join order — fine for created_at DESC by
        # accident, but visibly wrong for loan_asc / loan_desc.
        result = await self.db.execute(
            select(Lead)
            .join(sub, Lead.id == sub.c.id)
            .where(sub.c.rn > offset, sub.c.rn <= offset + per_stage_limit)
            .order_by(Lead.current_stage, sub.c.rn)
        )
        rows = result.scalars().all()

        items_by_stage: dict[str, list[Lead]] = {}
        for lead in rows:
            items_by_stage.setdefault(lead.current_stage, []).append(lead)

        # Total counts per stage (for "+N more" labels). One query.
        count_query = select(Lead.current_stage, func.count()).where(
            Lead.company_id == self.company_id,
            Lead.is_deleted == False,  # noqa: E712
        )
        if user.role in RESTRICTED_VIEW_ROLES:
            count_query = count_query.where(or_(Lead.assigned_agent_id == user.id, Lead.pre_counsellor_id == user.id))
        elif agent_id:
            count_query = count_query.where(or_(Lead.assigned_agent_id == agent_id, Lead.pre_counsellor_id == agent_id))
        if campaign_id:
            count_query = count_query.join(
                CampaignLead, CampaignLead.lead_id == Lead.id
            ).where(CampaignLead.campaign_id == campaign_id)
        # Same filter helper feeds the count query so the column headers
        # always reflect the rendered card set. Drift here = "Qualified ·
        # 23" header with only 4 cards inside, which is the bug we're
        # explicitly preventing.
        count_query = self._apply_kanban_filters(
            count_query,
            q=q, source_id=source_id,
            loan_min=loan_min, loan_max=loan_max,
            bank_name=bank_name, bank_status=bank_status,
            target_country=target_country, target_intake=target_intake,
            tags=tags,
            created_from=created_from, created_to=created_to,
            due_from=due_from, due_to=due_to,
            dnp_min=dnp_min, dnp_max=dnp_max,
            application_status=application_status, university=university,
            budget_min=budget_min, budget_max=budget_max, budget_currency=budget_currency,
            slug=slug,
            important_only=important_only,
            lead_segment=lead_segment,
        )
        if pipeline:
            # Same filter as the items query above — drift here means a
            # "Qualified · 23" header sitting above 4 cards.
            count_query = count_query.where(Lead.pipeline == pipeline)
        if stage:
            count_query = count_query.where(Lead.current_stage == stage)
        count_query = count_query.group_by(Lead.current_stage)
        count_rows = (await self.db.execute(count_query)).all()
        counts_by_stage = {st: cnt for st, cnt in count_rows}
        if stage:
            # GROUP BY yields no row for a stage with zero matches, but the
            # board needs the key present to compute `remaining` and hide
            # its "Show more" button. Absent would read as undefined on the
            # FE and keep the button up forever.
            counts_by_stage.setdefault(stage, 0)
            items_by_stage.setdefault(stage, [])
        total = sum(counts_by_stage.values())

        # Enrichment for the FMC-enhanced tile. Five extra batched
        # queries — same constant cost regardless of how many leads
        # are on screen, so a 600-card Kanban load stays at ~7 SQL
        # round trips instead of devolving into N+1.
        await self._enrich_cards(rows)

        payload = {
            # Column list for this board. The AI board is a short set;
            # without this the FE would have to know the split itself and
            # would drift from the backend.
            # Single-column mode reports just that column, so a "Show more"
            # response can never be mistaken for a full board payload and
            # re-render the page down to one column.
            "stages": [stage] if stage else [
                st.value for st in get_stages_for_pipeline(pipeline, slug)
            ],
            "pipeline": pipeline,
            "items_by_stage": items_by_stage,
            "counts_by_stage": counts_by_stage,
            "total": total,
        }
        _kanban_cache_set(cache_key, payload)
        return payload

    async def _enrich_cards(self, leads: list[Lead]) -> None:
        """Decorate each Lead with the activity rollups + agent display
        name the enhanced FMC tile renders. Sets transient attributes
        — Pydantic's from_attributes mode picks them up when building
        LeadCardOut. SQLAlchemy doesn't persist them.

        Five batched queries:
          1. assigned_agent_id  → agent name + role
          2. lead_id            → pending+overdue task count
          3. lead_id            → manual call (call_type='live') count
          4. lead_id            → stage-log-with-remark count
          5. lead_id            → has_active_ai_campaign (set membership)
        """
        if not leads:
            return

        lead_ids = [l.id for l in leads]
        # Union assigned agents + pre-counsellors into one profile lookup so
        # the FMC tile can render both names without an extra round trip.
        profile_ids = list(
            {l.assigned_agent_id for l in leads if l.assigned_agent_id}
            | {l.pre_counsellor_id for l in leads if getattr(l, "pre_counsellor_id", None)}
        )

        # OPTIMIZATION: combine the 4 count queries (tasks / live-calls /
        # stage-log-notes / lead-banks) into ONE UNION ALL query plus
        # AI-call signals into another, so the Kanban refresh costs
        # 5 round-trips total instead of 9. Sequential on self.db (one
        # connection avoids pgbouncer session-mode limits in production).
        from app.models.lead_remark import LeadRemark
        from app.models.lead_bank import LeadBank
        from sqlalchemy import literal, union_all

        # 1. Agent name + role lookup (small, fast — only ~5-30 profile IDs)
        agent_map: dict[uuid.UUID, tuple[str, str]] = {}
        if profile_ids:
            rows = (await self.db.execute(
                select(Profile.id, Profile.full_name, Profile.role)
                .where(Profile.id.in_(profile_ids))
            )).all()
            agent_map = {r.id: (r.full_name, r.role) for r in rows}

        # 1b. Source name lookup so the Kanban / lead detail can render
        # the human label ("WhatsApp Campaign") instead of an opaque UUID.
        # One query for all unique source IDs in the current result set.
        source_ids = list({l.lead_source_id for l in leads if getattr(l, "lead_source_id", None)})
        source_name_map: dict[uuid.UUID, str] = {}
        if source_ids:
            rows = (await self.db.execute(
                select(LeadSource.id, LeadSource.name)
                .where(LeadSource.id.in_(source_ids))
            )).all()
            source_name_map = {r.id: r.name for r in rows}

        # 2. Unified counts query — 4 aggregations in one round-trip.
        # 'kind' discriminator splits the buckets in Python.
        task_q = (
            select(literal("task").label("kind"), Task.lead_id, func.count().label("n"))
            .where(
                Task.company_id == self.company_id,
                Task.lead_id.in_(lead_ids),
                Task.status.in_([TaskStatus.PENDING.value, TaskStatus.OVERDUE.value]),
            )
            .group_by(Task.lead_id)
        )
        call_q = (
            select(literal("call").label("kind"), CallAttempt.lead_id, func.count().label("n"))
            .where(
                CallAttempt.company_id == self.company_id,
                CallAttempt.lead_id.in_(lead_ids),
                CallAttempt.call_type == "live",
            )
            .group_by(CallAttempt.lead_id)
        )
        notes_q = (
            select(literal("notes").label("kind"), LeadStageLog.lead_id, func.count().label("n"))
            .where(
                LeadStageLog.company_id == self.company_id,
                LeadStageLog.lead_id.in_(lead_ids),
                LeadStageLog.conversation_notes.isnot(None),
                func.length(LeadStageLog.conversation_notes) > 0,
            )
            .group_by(LeadStageLog.lead_id)
        )
        union_counts = union_all(task_q, call_q, notes_q)
        count_rows = (await self.db.execute(union_counts)).all()
        task_count_map: dict[uuid.UUID, int] = {}
        call_count_map: dict[uuid.UUID, int] = {}
        notes_count_map: dict[uuid.UUID, int] = {}
        for kind, lid, n in count_rows:
            if kind == "task": task_count_map[lid] = n
            elif kind == "call": call_count_map[lid] = n
            elif kind == "notes": notes_count_map[lid] = n

        # 3. Latest remark per lead (chronological feed)
        latest_remarks = (await self.db.execute(
            select(
                LeadRemark.lead_id, LeadRemark.body, LeadRemark.created_at,
                LeadRemark.author_id, LeadRemark.author_role,
                Profile.full_name.label("author_name"),
            )
            .outerjoin(Profile, Profile.id == LeadRemark.author_id)
            .where(
                LeadRemark.company_id == self.company_id,
                LeadRemark.lead_id.in_(lead_ids),
            )
            .order_by(LeadRemark.lead_id, LeadRemark.created_at.desc())
            .distinct(LeadRemark.lead_id)
        )).all()

        # 4. Latest stage-log note per lead (merged with remarks below)
        latest_stagelog_notes = (await self.db.execute(
            select(
                LeadStageLog.lead_id, LeadStageLog.conversation_notes,
                LeadStageLog.created_at, LeadStageLog.changed_by,
                Profile.full_name.label("author_name"),
                Profile.role.label("author_role"),
            )
            .outerjoin(Profile, Profile.id == LeadStageLog.changed_by)
            .where(
                LeadStageLog.company_id == self.company_id,
                LeadStageLog.lead_id.in_(lead_ids),
                LeadStageLog.conversation_notes.isnot(None),
                func.length(LeadStageLog.conversation_notes) > 0,
            )
            .order_by(LeadStageLog.lead_id, LeadStageLog.created_at.desc())
            .distinct(LeadStageLog.lead_id)
        )).all()

        # 5. All bank entries (count derived in Python — saves a round-trip
        # vs the previous count + entries pair).
        all_banks = (await self.db.execute(
            select(LeadBank)
            .where(LeadBank.company_id == self.company_id, LeadBank.lead_id.in_(lead_ids))
        )).scalars().all()

        # 6. AI signals (active campaign + ai_campaign call history) combined
        # via UNION so it's one round-trip instead of two. Result rows are
        # just lead_ids; we drop them into a set.
        active_q = (
            select(literal("camp").label("kind"), CampaignLead.lead_id).distinct()
            .where(
                CampaignLead.company_id == self.company_id,
                CampaignLead.lead_id.in_(lead_ids),
                CampaignLead.status.in_(["pending", "queued", "calling"]),
            )
        )
        ai_q = (
            select(literal("ai").label("kind"), CallAttempt.lead_id).distinct()
            .where(
                CallAttempt.company_id == self.company_id,
                CallAttempt.lead_id.in_(lead_ids),
                CallAttempt.call_type.in_(["ai", "ai_campaign"]),
            )
        )
        ai_signal_rows = (await self.db.execute(union_all(active_q, ai_q))).all()
        active_rows = [(r[1],) for r in ai_signal_rows if r[0] == "camp"]
        ai_call_rows = [(r[1],) for r in ai_signal_rows if r[0] == "ai"]

        # Merge: take whichever is newer per lead. Body is truncated to
        # 150 chars for the Kanban tile — the full note is fetched only
        # when the user opens the lead detail page. This cuts the
        # Kanban payload by ~30-50% on FMC (notes were up to 5000 chars).
        def _truncate(body: str | None, limit: int = 150) -> str | None:
            if not body:
                return body
            return body if len(body) <= limit else body[:limit].rstrip() + "…"

        latest_note_map: dict[uuid.UUID, dict] = {}
        for r in latest_remarks:
            latest_note_map[r.lead_id] = {
                "body": _truncate(r.body),
                "author_name": r.author_name,
                "author_role": r.author_role or "",
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "_created_at_raw": r.created_at,
            }
        for s in latest_stagelog_notes:
            existing = latest_note_map.get(s.lead_id)
            if not existing or (s.created_at and existing["_created_at_raw"] and s.created_at > existing["_created_at_raw"]):
                latest_note_map[s.lead_id] = {
                    "body": _truncate(s.conversation_notes),
                    "author_name": s.author_name,
                    "author_role": s.author_role or "",
                    "created_at": s.created_at.isoformat() if s.created_at else None,
                    "_created_at_raw": s.created_at,
                }
        # Strip the helper key before serialisation
        for v in latest_note_map.values():
            v.pop("_created_at_raw", None)

        # Bank rollups computed in Python from the all_banks rowset
        # fetched above (saves one round-trip vs the previous separate
        # count + entries queries). Order = status priority desc, then
        # created_at asc as tie-break (stable as new banks are added).
        priority = self._BANK_STATUS_PRIORITY  # local alias
        banks_by_lead: dict[uuid.UUID, list] = {}
        for b in all_banks:
            banks_by_lead.setdefault(b.lead_id, []).append(b)
        bank_count_map = {lid: len(v) for lid, v in banks_by_lead.items()}
        top_banks_map: dict[uuid.UUID, list[dict]] = {}
        for lid, entries in banks_by_lead.items():
            entries.sort(key=lambda e: (-priority.get(e.bank_status, 0), e.created_at))
            top_banks_map[lid] = [
                {
                    "id": str(e.id),
                    "bank_name": e.bank_name,
                    "bank_status": e.bank_status,
                }
                for e in entries[:2]
            ]

        # Admitverse per-university application rollups (analog of the bank
        # rollups above). Only queried for AV — FMC leads never have
        # application rows, so FMC pays zero extra round-trips.
        app_count_map: dict[uuid.UUID, int] = {}
        top_apps_map: dict[uuid.UUID, list[dict]] = {}
        if (await self._get_slug()) == "admitverse":
            from app.models.lead_application import LeadApplication
            from app.core.constants import APPLICATION_STATUS_PRIORITY
            all_apps = (await self.db.execute(
                select(LeadApplication).where(
                    LeadApplication.company_id == self.company_id,
                    LeadApplication.lead_id.in_(lead_ids),
                )
            )).scalars().all()
            apps_by_lead: dict[uuid.UUID, list] = {}
            for a in all_apps:
                apps_by_lead.setdefault(a.lead_id, []).append(a)
            app_count_map = {lid: len(v) for lid, v in apps_by_lead.items()}
            for lid, entries in apps_by_lead.items():
                entries.sort(key=lambda e: (-APPLICATION_STATUS_PRIORITY.get(e.application_status, 0), e.created_at))
                top_apps_map[lid] = [
                    {
                        "id": str(e.id),
                        "university_name": e.university_name,
                        "program": e.program,
                        "application_status": e.application_status,
                    }
                    for e in entries[:2]
                ]

        # AI-call watermark: lead has an active campaign row OR an ai/ai_campaign
        # call_attempt. Without the second arm, the watermark vanished as soon as
        # a campaign finished even though the lead clearly had been AI-contacted.
        active_campaign_set = {r[0] for r in active_rows} | {r[0] for r in ai_call_rows if r[0]}

        # Decorate each Lead instance with the rollups. Setattr is fine
        # — these are not mapped columns; SQLAlchemy ignores them on
        # commit. Pydantic from_attributes reads them when serializing.
        for lead in leads:
            agent = agent_map.get(lead.assigned_agent_id) if lead.assigned_agent_id else None
            lead.assigned_agent_name = agent[0] if agent else None
            lead.assigned_agent_role = agent[1] if agent else None
            pre = agent_map.get(lead.pre_counsellor_id) if getattr(lead, "pre_counsellor_id", None) else None
            lead.pre_counsellor_name = pre[0] if pre else None
            lead.task_count = task_count_map.get(lead.id, 0)
            lead.call_count = call_count_map.get(lead.id, 0)
            lead.notes_count = notes_count_map.get(lead.id, 0)
            lead.bank_count = bank_count_map.get(lead.id, 0)
            lead.top_banks = top_banks_map.get(lead.id, [])
            lead.application_count = app_count_map.get(lead.id, 0)
            lead.top_applications = top_apps_map.get(lead.id, [])
            lead.latest_note = latest_note_map.get(lead.id)
            lead.has_active_ai_campaign = lead.id in active_campaign_set
            lead.source_name = source_name_map.get(lead.lead_source_id) if lead.lead_source_id else None

    async def search_leads(self, q: str, user: Profile, page: int = 1, page_size: int = 25) -> dict:
        query = select(Lead).where(
            Lead.company_id == self.company_id,
            Lead.is_deleted == False,
            or_(
                Lead.full_name.ilike(f"%{q}%"),
                Lead.email.ilike(f"%{q}%"),
                Lead.phone.ilike(f"%{q}%"),
            )
        ).order_by(Lead.created_at.desc())

        if user.role in RESTRICTED_VIEW_ROLES:
            query = query.where(or_(Lead.assigned_agent_id == user.id, Lead.pre_counsellor_id == user.id))

        return await paginate(self.db, query, page, page_size)

    # ─── Multi-bank tracking ────────────────────────────────────────────
    # Status priority for auto-syncing lead.bank_name / lead.bank_status
    # to the "best" entry across the lead's banks.
    _BANK_STATUS_PRIORITY = {
        "disbursed": 7, "pf_paid": 6, "sanctioned": 5, "loan_login": 4,
        "under_review": 3, "docs_reviewed": 2, "applied": 1,
        # Below 'applied' deliberately. A lender that rejected the file
        # must never be lifted onto lead.bank_name as the lead's primary
        # bank while another lender is still working on it.
        "lost": 0,
    }
    _BANK_VALID_STATUSES = set(_BANK_STATUS_PRIORITY.keys())

    async def _resync_primary_bank(self, lead: Lead) -> None:
        """After any add/update/delete on lead_banks, refresh lead.bank_name
        and lead.bank_status to point at the highest-priority entry. Falls
        back to NULL if the lead has no entries.
        """
        from app.models.lead_bank import LeadBank
        rows = (await self.db.execute(
            select(LeadBank).where(LeadBank.lead_id == lead.id)
        )).scalars().all()
        if not rows:
            lead.bank_name = None
            lead.bank_status = None
            return
        best = max(rows, key=lambda r: (self._BANK_STATUS_PRIORITY.get(r.bank_status, 0), r.updated_at))
        lead.bank_name = best.bank_name
        lead.bank_status = best.bank_status

    async def list_banks(self, lead_id: uuid.UUID, user: Profile) -> list:
        """Return all bank entries for a lead, ordered by created_at desc."""
        from app.models.lead_bank import LeadBank
        await self._require_fmc_banks()
        await self.get_lead(lead_id, user)
        rows = (await self.db.execute(
            select(LeadBank)
            .where(LeadBank.lead_id == lead_id, LeadBank.company_id == self.company_id)
            .order_by(LeadBank.created_at.desc())
        )).scalars().all()
        return list(rows)

    async def add_bank(self, lead_id: uuid.UUID, bank_name: str, bank_status: str, notes: str | None, user: Profile):
        """Add a bank entry to a lead. Bank name must be in the canonical
        FMC list; status must be a valid bank_status enum value; a lead
        can't have the same bank twice (DB unique constraint backstops
        the service check)."""
        from app.models.lead_bank import LeadBank
        from app.services.bank_registry import get_bank_names
        if await self._get_slug() == "admitverse":
            raise BadRequestError(
                "Bank tracking is not available for this tenant. "
                "Use university applications (/leads/{id}/applications) instead."
            )
        if bank_name not in await get_bank_names(self.db):
            raise BadRequestError(
                f"bank_name must be one of the canonical FMC banks (got '{bank_name}'). See GET /leads/banks."
            )
        if bank_status not in self._BANK_VALID_STATUSES:
            raise BadRequestError(
                f"bank_status must be one of {sorted(self._BANK_VALID_STATUSES)} (got '{bank_status}')."
            )

        lead = await self.get_lead(lead_id, user)

        # Pre-check for dup (cleaner error than catching the IntegrityError)
        existing = (await self.db.execute(
            select(LeadBank.id).where(
                LeadBank.lead_id == lead_id,
                LeadBank.bank_name == bank_name,
            )
        )).scalar_one_or_none()
        if existing:
            raise BadRequestError(
                f"This lead already has an entry for '{bank_name}'. "
                f"Use PATCH /leads/{{id}}/banks/{{entry_id}} to update its status."
            )

        entry = LeadBank(
            company_id=self.company_id,
            lead_id=lead_id,
            bank_name=bank_name,
            bank_status=bank_status,
            notes=notes,
        )
        self.db.add(entry)
        await self.db.flush()
        await self._resync_primary_bank(lead)
        await self.db.commit()
        await self.db.refresh(entry)
        return entry

    async def update_bank_entry(self, lead_id: uuid.UUID, entry_id: uuid.UUID, payload: dict, user: Profile):
        """Update a lead-bank entry. Accepts bank_status, notes, and the
        9 sanction-detail fields (application_id, sanction_date,
        loan_amount, roi, tenure_months, pf_amount, first_tranche_amount,
        no_of_tranches, pf_status). Sanction-detail writes are gated:
        the bank must already be sanctioned-or-later, otherwise we 400
        ('record sanction status first').
        """
        from app.models.lead_bank import LeadBank
        from app.utils.date_helpers import now_utc

        if await self._get_slug() == "admitverse":
            raise BadRequestError(
                "Bank tracking is not available for this tenant. "
                "Use university applications (/leads/{id}/applications) instead."
            )
        lead = await self.get_lead(lead_id, user)
        entry = (await self.db.execute(
            select(LeadBank).where(
                LeadBank.id == entry_id,
                LeadBank.lead_id == lead_id,
                LeadBank.company_id == self.company_id,
            )
        )).scalar_one_or_none()
        if not entry:
            raise NotFoundError("Bank entry not found")

        # Lakhs in, rupees out — the column stores rupees. Normalised up
        # front so every check below sees a single field, and a caller
        # sending lakhs can never write a lakh figure into a rupee column.
        if payload.get("loan_amount_lakh") is not None:
            from app.core.constants import LAKH_IN_RUPEES
            payload["loan_amount"] = (
                Decimal(payload["loan_amount_lakh"]) * LAKH_IN_RUPEES
            ).quantize(Decimal("0.01"))
        payload.pop("loan_amount_lakh", None)
        # Same conversion for the disbursed figure, which is a different
        # number from loan_amount: loan_amount is what the lender
        # SANCTIONED, this is what it actually released. Commission is
        # earned on the second, so conflating them would misstate every
        # figure downstream.
        if payload.get("disbursed_amount_lakh") is not None:
            from app.core.constants import LAKH_IN_RUPEES
            payload["disbursed_amount"] = (
                Decimal(payload["disbursed_amount_lakh"]) * LAKH_IN_RUPEES
            ).quantize(Decimal("0.01"))
        payload.pop("disbursed_amount_lakh", None)

        # Apply bank_status first so the gate below sees the new value
        # (FE often sends status change + sanction details in one PATCH).
        # The disbursement row is built AFTER the sanction fields land,
        # so it reads a fully-updated entry, but validated up here so a
        # missing amount fails before anything is written.
        _pending_disbursement = None
        new_status = payload.get("bank_status")
        if new_status is not None:
            if new_status not in self._BANK_VALID_STATUSES:
                raise BadRequestError(
                    f"bank_status must be one of {sorted(self._BANK_VALID_STATUSES)} (got '{new_status}')."
                )
            # A file that has released money is not a file we lost.
            #
            # Marking one lost used to leave every tranche behind, still
            # counting toward disbursement, commission and what a lender
            # owes. It happened three times in two days on FMC's book —
            # an Avanse file closed with Rs 13 L of disbursement still
            # attached, and two more besides — and each time the figures
            # simply did not move, so nobody noticed.
            #
            # Refused rather than cascaded: deleting someone's money as a
            # side effect of a dropdown is worse than making them say what
            # they meant. If the tranche was wrong, remove it first; if it
            # was real, the file is not lost.
            if new_status == "lost" and entry.bank_status != "lost":
                from app.services.commission_service import CommissionService
                if await CommissionService(
                    self.db, self.company_id
                ).has_disbursement(entry.id):
                    raise BadRequestError(
                        f"'{entry.bank_name}' has disbursements recorded "
                        f"against it, so it cannot be marked lost — the "
                        f"money would keep counting toward commission with "
                        f"no live file behind it. Delete the tranches first "
                        f"if they were recorded in error, or leave the file "
                        f"as it is if the lender really did release funds."
                    )
            # PF paid means a fee was paid against a specific sanctioned
            # amount, so the amount is part of the claim, not an optional
            # extra. Accepted from this payload OR already on the row, so
            # correcting a typo in the status doesn't force re-entry of a
            # figure that is already there.
            if new_status == "pf_paid" and payload.get("loan_amount") is None \
                    and entry.loan_amount is None:
                raise BadRequestError(
                    "loan_amount_lakh is required when setting a bank to "
                    "'pf_paid' — record the amount this lender sanctioned."
                )
            # Sanctioned is where gross theoretical revenue comes from:
            # the lender's rate applied to the amount it approved. Neither
            # the amount nor the date can be reconstructed later, and the
            # cost of not asking is already visible — of the 79 files that
            # reached this status before today, 48 carry no amount and 77
            # carry no date, so the figure would silently understate by
            # more than half while looking complete.
            #
            # Accepted from the payload OR already on the row, so
            # correcting an unrelated field on a sanctioned file does not
            # demand the figures again.
            if new_status == "sanctioned":
                if payload.get("loan_amount") is None and entry.loan_amount is None:
                    raise BadRequestError(
                        "loan_amount_lakh is required when setting a bank to "
                        "'sanctioned' — record the amount this lender approved."
                    )
                if payload.get("sanction_date") is None and entry.sanction_date is None:
                    raise BadRequestError(
                        "sanction_date is required when setting a bank to "
                        "'sanctioned' — gross theoretical revenue is reported "
                        "by month and cannot be without it."
                    )
            # Disbursed is where FMC actually earns: commission is a
            # percentage of what came out, on the date it came out. Both
            # are required because neither can be reconstructed later —
            # the 34 rows that reached this status before today have 17
            # amounts and zero dates between them, which is precisely why
            # the commission ledger had to live on a spreadsheet.
            #
            # Skipped when the file already has a tranche recorded, so
            # re-saving a cell that is already disbursed doesn't demand
            # the figures a second time.
            if new_status == "disbursed" and entry.bank_status != "disbursed":
                from app.services.commission_service import CommissionService
                _commission = CommissionService(self.db, self.company_id)
                if not await _commission.has_disbursement(entry.id):
                    if payload.get("disbursed_amount") is None or payload.get("disbursed_on") is None:
                        raise BadRequestError(
                            "disbursed_amount_lakh and disbursed_on are both "
                            "required when setting a bank to 'disbursed' — "
                            "the commission is a percentage of what was "
                            "released, on the date it was released."
                        )
                    _pending_disbursement = {
                        "disbursed_amount": payload["disbursed_amount"],
                        "disbursed_on": payload["disbursed_on"],
                        "utr_reference": payload.get("utr_reference"),
                        "rate_override": payload.get("commission_rate"),
                    }
            entry.bank_status = new_status
        if "notes" in payload and payload["notes"] is not None:
            entry.notes = payload["notes"]

        # Sanction detail fields. Gate: only writable once the bank is at
        # sanctioned or beyond (pf_paid / disbursed). Otherwise the data
        # is meaningless — there's no sanction to record yet.
        sanction_fields = (
            "application_id", "sanction_date", "loan_amount", "roi",
            "tenure_months", "pf_amount", "first_tranche_amount",
            "no_of_tranches", "pf_status",
        )
        has_sanction_update = any(payload.get(f) is not None for f in sanction_fields)
        if has_sanction_update:
            if entry.bank_status not in {"sanctioned", "pf_paid", "disbursed"}:
                raise BadRequestError(
                    "Sanction details can only be entered once the bank is "
                    "in sanctioned status or later. Move bank_status to "
                    "'sanctioned' first."
                )
            if payload.get("pf_status") is not None and payload["pf_status"] not in {"paid", "pending"}:
                raise BadRequestError("pf_status must be 'paid' or 'pending'.")
            for f in sanction_fields:
                if f in payload and payload[f] is not None:
                    setattr(entry, f, payload[f])

        # Connector payout. NOT gated on sanction status — an agreement
        # with whoever supplied the lead exists from the moment the deal
        # is agreed, and can be recorded before the bank confirms
        # anything. Deliberately not checked against commission earned:
        # the payout is owed on the SANCTION while commission accrues per
        # tranche, so Aftar is legitimately owed Rs 4,870 against Rs 3,185
        # earned so far. The exception register reports that; it is not
        # an error.
        for f in ("payout_to", "payout_due", "payout_paid"):
            if f in payload and payload[f] is not None:
                setattr(entry, f, payload[f])
        if (entry.payout_due or entry.payout_paid) and not (entry.payout_to or "").strip():
            # Money leaving the book that nobody can be asked about.
            raise BadRequestError(
                "Say who the payout goes to. A share of commission with "
                "no name against it cannot be chased or reconciled."
            )

        # Snapshot the lender's commission rate onto the file the first
        # time it reaches sanctioned-or-later. Gross theoretical revenue
        # is computed from this, not from the lender's current rate, so
        # renegotiating a lender later cannot restate what earlier files
        # were theoretically worth.
        #
        # Only if absent: an existing snapshot is history and must not be
        # overwritten by a subsequent edit. An explicit commission_rate in
        # the payload does override it, which is how a file negotiated
        # off-schedule gets corrected.
        if entry.bank_status in {"sanctioned", "pf_paid", "disbursed"}:
            if payload.get("commission_rate") is not None:
                entry.commission_rate = Decimal(payload["commission_rate"])
            elif entry.commission_rate is None:
                from app.services.bank_registry import get_commission_rate
                entry.commission_rate = await get_commission_rate(
                    self.db, entry.bank_name
                )

        entry.updated_at = now_utc()
        await self.db.flush()

        # Same transaction as the status change: a file that says
        # "disbursed" with no money behind it is the state this whole
        # feature exists to eliminate, so the two must land together.
        if _pending_disbursement:
            from app.services.commission_service import CommissionService
            await CommissionService(self.db, self.company_id).record_disbursement(
                entry=entry, user=user, source="bank_grid", **_pending_disbursement,
            )

        await self._resync_primary_bank(lead)
        await self.db.commit()
        await self.db.refresh(entry)
        return entry

    async def delete_bank_entry(self, lead_id: uuid.UUID, entry_id: uuid.UUID, user: Profile) -> None:
        from app.models.lead_bank import LeadBank
        if await self._get_slug() == "admitverse":
            raise BadRequestError(
                "Bank tracking is not available for this tenant. "
                "Use university applications (/leads/{id}/applications) instead."
            )
        lead = await self.get_lead(lead_id, user)
        entry = (await self.db.execute(
            select(LeadBank).where(
                LeadBank.id == entry_id,
                LeadBank.lead_id == lead_id,
                LeadBank.company_id == self.company_id,
            )
        )).scalar_one_or_none()
        if not entry:
            raise NotFoundError("Bank entry not found")
        await self.db.delete(entry)
        await self.db.flush()
        await self._resync_primary_bank(lead)
        await self.db.commit()

    # ── Admitverse per-university application tracking ─────────────────
    # Analog of the FMC bank methods above. Brand-gated to Admitverse.
    _APPLICATION_OFFER_FIELDS = (
        "application_ref", "offer_date", "tuition_fee", "scholarship_amount",
        "deposit_amount", "deposit_paid_date", "cas_number", "visa_status",
    )

    async def _require_admitverse(self) -> None:
        if await self._get_slug() != "admitverse":
            raise BadRequestError(
                "University application tracking is only available for "
                "Admitverse. FMC uses bank tracking (/leads/{id}/banks)."
            )

    async def _resync_primary_application(self, lead: Lead) -> None:
        """Refresh lead.primary_university + lead.application_status to point
        at the highest-priority application entry. NULL if no entries."""
        from app.models.lead_application import LeadApplication
        from app.core.constants import APPLICATION_STATUS_PRIORITY
        rows = (await self.db.execute(
            select(LeadApplication).where(LeadApplication.lead_id == lead.id)
        )).scalars().all()
        if not rows:
            lead.primary_university = None
            lead.application_status = None
            return
        best = max(
            rows,
            key=lambda r: (APPLICATION_STATUS_PRIORITY.get(r.application_status, 0), r.updated_at),
        )
        lead.primary_university = best.university_name
        lead.application_status = best.application_status

    async def list_applications(self, lead_id: uuid.UUID, user: Profile) -> list:
        """All application entries for a lead, newest first."""
        from app.models.lead_application import LeadApplication
        await self.get_lead(lead_id, user)
        rows = (await self.db.execute(
            select(LeadApplication)
            .where(LeadApplication.lead_id == lead_id, LeadApplication.company_id == self.company_id)
            .order_by(LeadApplication.created_at.desc())
        )).scalars().all()
        return list(rows)

    async def add_application(self, lead_id: uuid.UUID, payload: dict, user: Profile):
        """Add a university-application entry. university_name is free text;
        status must be a valid application_status; a lead can't have the
        same (university, program) twice (DB unique backstops the check)."""
        from app.models.lead_application import LeadApplication
        from app.core.constants import APPLICATION_STATUS_VALUES
        await self._require_admitverse()

        university_name = (payload.get("university_name") or "").strip()
        if not university_name:
            raise BadRequestError("university_name is required.")
        status = payload.get("application_status") or "applied"
        if status not in APPLICATION_STATUS_VALUES:
            raise BadRequestError(
                f"application_status must be one of {list(APPLICATION_STATUS_VALUES)} (got '{status}')."
            )

        lead = await self.get_lead(lead_id, user)
        program = payload.get("program")

        existing = (await self.db.execute(
            select(LeadApplication.id).where(
                LeadApplication.lead_id == lead_id,
                LeadApplication.university_name == university_name,
                LeadApplication.program.is_(program) if program is None
                else LeadApplication.program == program,
            )
        )).scalar_one_or_none()
        if existing:
            raise BadRequestError(
                f"This lead already has an application for '{university_name}'"
                f"{f' ({program})' if program else ''}. Use PATCH to update it."
            )

        entry = LeadApplication(
            company_id=self.company_id,
            lead_id=lead_id,
            university_name=university_name,
            program=program,
            intake=payload.get("intake"),
            country=payload.get("country"),
            application_status=status,
            notes=payload.get("notes"),
        )
        self.db.add(entry)
        await self.db.flush()
        await self._resync_primary_application(lead)
        await self.db.commit()
        await self.db.refresh(entry)
        return entry

    async def update_application_entry(self, lead_id: uuid.UUID, entry_id: uuid.UUID, payload: dict, user: Profile):
        """Update an application entry. Offer-detail fields (offer_date,
        tuition_fee, deposit, CAS, visa) are only writable once the
        application reaches an offer-or-later status."""
        from app.models.lead_application import LeadApplication
        from app.core.constants import (
            APPLICATION_STATUS_VALUES, APPLICATION_OFFER_STATUSES, VISA_STATUS_VALUES,
        )
        from app.utils.date_helpers import now_utc
        await self._require_admitverse()

        lead = await self.get_lead(lead_id, user)
        entry = (await self.db.execute(
            select(LeadApplication).where(
                LeadApplication.id == entry_id,
                LeadApplication.lead_id == lead_id,
                LeadApplication.company_id == self.company_id,
            )
        )).scalar_one_or_none()
        if not entry:
            raise NotFoundError("Application entry not found")

        # Apply status first so the offer-gate below sees the new value.
        new_status = payload.get("application_status")
        if new_status is not None:
            if new_status not in APPLICATION_STATUS_VALUES:
                raise BadRequestError(
                    f"application_status must be one of {list(APPLICATION_STATUS_VALUES)} (got '{new_status}')."
                )
            entry.application_status = new_status
        for plain in ("program", "intake", "country", "notes"):
            if plain in payload and payload[plain] is not None:
                setattr(entry, plain, payload[plain])

        has_offer_update = any(payload.get(f) is not None for f in self._APPLICATION_OFFER_FIELDS)
        if has_offer_update:
            if entry.application_status not in APPLICATION_OFFER_STATUSES:
                raise BadRequestError(
                    "Offer/admission details can only be entered once the "
                    "application reaches an offer status. Move "
                    "application_status to 'offer_received' or later first."
                )
            if payload.get("visa_status") is not None and payload["visa_status"] not in VISA_STATUS_VALUES:
                raise BadRequestError(f"visa_status must be one of {list(VISA_STATUS_VALUES)}.")
            for f in self._APPLICATION_OFFER_FIELDS:
                if f in payload and payload[f] is not None:
                    setattr(entry, f, payload[f])

        entry.updated_at = now_utc()
        await self.db.flush()
        await self._resync_primary_application(lead)
        await self.db.commit()
        await self.db.refresh(entry)
        return entry

    async def delete_application_entry(self, lead_id: uuid.UUID, entry_id: uuid.UUID, user: Profile) -> None:
        from app.models.lead_application import LeadApplication
        await self._require_admitverse()
        lead = await self.get_lead(lead_id, user)
        entry = (await self.db.execute(
            select(LeadApplication).where(
                LeadApplication.id == entry_id,
                LeadApplication.lead_id == lead_id,
                LeadApplication.company_id == self.company_id,
            )
        )).scalar_one_or_none()
        if not entry:
            raise NotFoundError("Application entry not found")
        await self.db.delete(entry)
        await self.db.flush()
        await self._resync_primary_application(lead)
        await self.db.commit()

    async def add_remark(self, lead_id: uuid.UUID, body: str, user: Profile) -> dict:
        """Add a free-form remark to a lead. Access gated by get_lead
        (which enforces the assigned-agent / pre-counsellor / admin rules).
        Returns a dict matching LeadRemarkOut shape, with enriched author_name.
        """
        from app.models.lead_remark import LeadRemark
        # get_lead enforces permission — re-use it.
        await self.get_lead(lead_id, user)

        remark = LeadRemark(
            company_id=self.company_id,
            lead_id=lead_id,
            author_id=user.id,
            author_role=user.role,
            body=body,
        )
        self.db.add(remark)
        await self.db.flush()
        await self.db.commit()
        return {
            "id": remark.id,
            "lead_id": remark.lead_id,
            "author_id": remark.author_id,
            "author_name": user.full_name,
            "author_role": remark.author_role,
            "body": remark.body,
            "created_at": remark.created_at,
        }

    async def list_remarks(self, lead_id: uuid.UUID, user: Profile) -> list[dict]:
        """List all remarks on a lead, newest first. Access gated by
        get_lead so a restricted user can't read remarks on leads they
        don't own. Author names are enriched with one batched profile
        lookup.
        """
        from app.models.lead_remark import LeadRemark
        await self.get_lead(lead_id, user)

        rows = (await self.db.execute(
            select(LeadRemark)
            .where(
                LeadRemark.lead_id == lead_id,
                LeadRemark.company_id == self.company_id,
            )
            .order_by(LeadRemark.created_at.desc())
        )).scalars().all()

        author_ids = list({r.author_id for r in rows if r.author_id})
        names: dict[uuid.UUID, str] = {}
        if author_ids:
            name_rows = (await self.db.execute(
                select(Profile.id, Profile.full_name).where(Profile.id.in_(author_ids))
            )).all()
            names = {row.id: row.full_name for row in name_rows}

        return [{
            "id": r.id,
            "lead_id": r.lead_id,
            "author_id": r.author_id,
            "author_name": names.get(r.author_id) if r.author_id else None,
            "author_role": r.author_role,
            "body": r.body,
            "created_at": r.created_at,
        } for r in rows]

    async def assign_lead(self, lead_id: uuid.UUID, agent_id: uuid.UUID) -> Lead:
        # Verify agent exists and belongs to same company
        result = await self.db.execute(
            select(Profile).where(
                Profile.id == agent_id,
                Profile.company_id == self.company_id,
                Profile.is_active == True,
            )
        )
        if not result.scalar_one_or_none():
            raise BadRequestError("Agent not found or inactive")

        result = await self.db.execute(
            select(Lead).where(Lead.id == lead_id, Lead.company_id == self.company_id, Lead.is_deleted == False)
        )
        lead = result.scalar_one_or_none()
        if not lead:
            raise NotFoundError("Lead not found")

        lead.assigned_agent_id = agent_id
        await self.db.commit()
        await self.db.refresh(lead)
        return lead

    async def reassign_lead(
        self,
        lead_id: uuid.UUID,
        *,
        actor: Profile,
        updates: dict,  # subset of {"assigned_agent_id": uuid|None, "pre_counsellor_id": uuid|None}
        reason: str | None = None,
    ) -> Lead:
        """Reassign Counsellor and/or Pre-Counsellor on a single lead.

        `updates` keys MUST come from a model_dump(exclude_unset=True)
        on LeadReassign — so a missing key means "don't touch this
        field" and an explicit None means "clear this field".

        Validates each new user belongs to this tenant and is active.
        Logs a lead_remarks entry capturing the before/after for audit.
        """
        from app.models.lead_remark import LeadRemark

        lead = (await self.db.execute(
            select(Lead).where(
                Lead.id == lead_id,
                Lead.company_id == self.company_id,
                Lead.is_deleted == False,  # noqa: E712
            )
        )).scalar_one_or_none()
        if not lead:
            raise NotFoundError("Lead not found")

        # Validate any provided user IDs belong to this tenant
        user_ids_to_check = {v for v in updates.values() if v is not None}
        if user_ids_to_check:
            rows = (await self.db.execute(
                select(Profile.id, Profile.full_name, Profile.role).where(
                    Profile.id.in_(user_ids_to_check),
                    Profile.company_id == self.company_id,
                    Profile.is_active == True,  # noqa: E712
                )
            )).all()
            found = {r.id for r in rows}
            missing = user_ids_to_check - found
            if missing:
                raise BadRequestError(
                    f"User(s) not found or inactive in this tenant: {sorted(str(x) for x in missing)}"
                )
            name_map = {r.id: r.full_name for r in rows}
        else:
            name_map = {}

        before_agent = lead.assigned_agent_id
        before_pre = lead.pre_counsellor_id

        # Apply only the keys the caller explicitly sent (exclude_unset)
        if "assigned_agent_id" in updates:
            lead.assigned_agent_id = updates["assigned_agent_id"]
        if "pre_counsellor_id" in updates:
            lead.pre_counsellor_id = updates["pre_counsellor_id"]

        # Build a human-readable audit line for the remarks timeline
        changes = []
        if "assigned_agent_id" in updates and before_agent != lead.assigned_agent_id:
            old = name_map.get(before_agent, "—") if before_agent else "—"
            # before_agent might not be in name_map (it's not in the new-IDs lookup); fall back to a DB lookup
            if before_agent and before_agent not in name_map:
                row = (await self.db.execute(
                    select(Profile.full_name).where(Profile.id == before_agent)
                )).first()
                old = row[0] if row else "—"
            new = name_map.get(lead.assigned_agent_id, "—") if lead.assigned_agent_id else "—"
            changes.append(f"Counsellor: {old} → {new}")
        if "pre_counsellor_id" in updates and before_pre != lead.pre_counsellor_id:
            old = "—"
            if before_pre:
                row = (await self.db.execute(
                    select(Profile.full_name).where(Profile.id == before_pre)
                )).first()
                old = row[0] if row else "—"
            new = name_map.get(lead.pre_counsellor_id, "—") if lead.pre_counsellor_id else "—"
            changes.append(f"Pre-Counsellor: {old} → {new}")

        if not changes:
            # No actual change requested — short-circuit so we don't
            # pollute the timeline with no-op remarks.
            return lead

        body = "Reassigned — " + "; ".join(changes)
        if reason:
            body += f". Reason: {reason}"
        self.db.add(LeadRemark(
            company_id=self.company_id,
            lead_id=lead.id,
            author_id=actor.id,
            author_role=actor.role,
            body=body,
        ))

        await self.db.commit()
        await self.db.refresh(lead)
        return lead

    async def set_important(self, lead_id: uuid.UUID, value: bool, user: Profile) -> Lead:
        """Toggle the is_important flag on a lead.

        Doesn't move the lead between Kanban columns — Important is a
        flag, not a stage. Same access rules as other lead writes:
        admin/manager can star anything they can see; telecallers only
        their own assigned leads.
        """
        lead = await self.get_lead(lead_id, user)
        lead.is_important = bool(value)
        await self.db.commit()
        await self.db.refresh(lead)
        return lead

    async def distribute_by_range(
        self,
        ranges: list[dict],
        unassigned_only: bool = True,
        stage: str | None = None,
        order_by: str = "created_at_desc",
    ) -> dict:
        """Distribute leads to agents by row position.

        Walks the leads (filtered and ordered as requested) and assigns
        rows [from_pos..to_pos] of each range to the corresponding
        agent_id. Row positions are 1-indexed against the filtered list,
        not the DB id.

        Each range dict: {"from_pos": int, "to_pos": int, "agent_id": UUID}

        Validates:
        - All agent_ids exist in this company and are active
        - Ranges are well-formed (from_pos <= to_pos)
        - Ranges don't overlap (so a single lead never lands in two
          buckets — keep things deterministic)

        Returns: {
            "total_assigned": int,
            "eligible_count": int,
            "ranges": [{from_pos, to_pos, agent_id, agent_name, assigned_count}]
        }
        """
        if not ranges:
            raise BadRequestError("ranges cannot be empty")

        # ── 1. Validate range shape and overlaps ──
        sorted_ranges = sorted(ranges, key=lambda r: r["from_pos"])
        prev_to = 0
        for r in sorted_ranges:
            if r["from_pos"] > r["to_pos"]:
                raise BadRequestError(
                    f"Invalid range: from={r['from_pos']} > to={r['to_pos']}"
                )
            if r["from_pos"] <= prev_to:
                raise BadRequestError(
                    f"Range from={r['from_pos']} overlaps a previous range "
                    f"(ended at {prev_to}). Ranges must be disjoint."
                )
            prev_to = r["to_pos"]

        # ── 2. Validate every agent exists in this company and is active ──
        agent_ids = {r["agent_id"] for r in ranges}
        agent_rows = (await self.db.execute(
            select(Profile.id, Profile.full_name).where(
                Profile.id.in_(agent_ids),
                Profile.company_id == self.company_id,
                Profile.is_active == True,  # noqa: E712
            )
        )).all()
        agent_name_by_id = {row.id: row.full_name for row in agent_rows}
        missing = agent_ids - set(agent_name_by_id.keys())
        if missing:
            raise BadRequestError(
                f"Unknown / inactive agent ids: {sorted(str(x) for x in missing)}"
            )

        # ── 3. Fetch eligible lead ids in the requested order ──
        q = select(Lead.id).where(
            Lead.company_id == self.company_id,
            Lead.is_deleted == False,  # noqa: E712
        )
        if unassigned_only:
            q = q.where(Lead.assigned_agent_id.is_(None))
        if stage:
            q = q.where(Lead.current_stage == stage)
        if order_by == "created_at_asc":
            q = q.order_by(Lead.created_at.asc())
        else:
            q = q.order_by(Lead.created_at.desc())

        result = await self.db.execute(q)
        all_ids: list[uuid.UUID] = [row[0] for row in result.fetchall()]
        eligible_count = len(all_ids)

        # ── 4. Apply each range as one UPDATE ──
        results = []
        total_assigned = 0
        for r in ranges:
            from_pos = r["from_pos"]
            to_pos = r["to_pos"]
            # Convert 1-indexed inclusive to 0-indexed slice.
            slice_ids = all_ids[from_pos - 1: to_pos]
            assigned = 0
            if slice_ids:
                stmt = (
                    update(Lead)
                    .where(
                        Lead.id.in_(slice_ids),
                        Lead.company_id == self.company_id,
                        Lead.is_deleted == False,  # noqa: E712
                    )
                    .values(assigned_agent_id=r["agent_id"])
                )
                upd = await self.db.execute(stmt)
                assigned = upd.rowcount or 0
                total_assigned += assigned
            results.append({
                "from_pos": from_pos,
                "to_pos": to_pos,
                "agent_id": r["agent_id"],
                "agent_name": agent_name_by_id.get(r["agent_id"]),
                "assigned_count": assigned,
            })

        await self.db.commit()
        return {
            "total_assigned": total_assigned,
            "eligible_count": eligible_count,
            "ranges": results,
        }

    async def bulk_assign(self, lead_ids: list[uuid.UUID], agent_id: uuid.UUID) -> int:
        # Verify agent exists and belongs to same company
        result = await self.db.execute(
            select(Profile).where(
                Profile.id == agent_id,
                Profile.company_id == self.company_id,
                Profile.is_active == True,
            )
        )
        if not result.scalar_one_or_none():
            raise BadRequestError("Agent not found or inactive")

        stmt = (
            update(Lead)
            .where(Lead.id.in_(lead_ids), Lead.company_id == self.company_id, Lead.is_deleted == False)
            .values(assigned_agent_id=agent_id)
        )
        result = await self.db.execute(stmt)
        await self.db.commit()
        return result.rowcount

    async def get_timeline(self, lead_id: uuid.UUID, user: Profile) -> list[LeadStageLog]:
        await self.get_lead(lead_id, user)  # Auth check
        result = await self.db.execute(
            select(LeadStageLog)
            .where(LeadStageLog.lead_id == lead_id, LeadStageLog.company_id == self.company_id)
            .order_by(LeadStageLog.created_at.desc())
        )
        return result.scalars().all()

    # ─── Bank shares (WhatsApp phase 2) ─────────────────────────────────
    #
    # "This lead was shared with this bank" lives on lead_banks, the table
    # that already models exactly one row per (lead, bank). These methods
    # only ever touch the share-provenance columns and the conversation —
    # bank_status is the bank's decision and is never written here.

    async def _require_fmc_banks(self) -> None:
        """Bank shares are a FundMyCampus concept — refuse on Admitverse.

        One codebase serves both brands, so every bank-share endpoint has
        to say so itself. Applied to the READS as well as the writes: the
        grid's columns are the FMC lender list, and an AV user hitting it
        would get a board of Indian lender columns that mean nothing for
        study abroad. AV's equivalent is university applications.
        """
        if await self._get_slug() == "admitverse":
            raise BadRequestError(
                "Bank tracking is not available for this tenant. "
                "Use university applications (/leads/{id}/applications) instead."
            )

    async def _get_lead_bank(self, lead_id: uuid.UUID, bank_name: str):
        from app.models.lead_bank import LeadBank
        return (await self.db.execute(
            select(LeadBank).where(
                LeadBank.lead_id == lead_id,
                LeadBank.company_id == self.company_id,
                LeadBank.bank_name == bank_name,
            )
        )).scalar_one_or_none()

    async def record_bank_share(
        self, lead_id: uuid.UUID, payload: dict, user: Profile,
    ) -> tuple[object, bool]:
        """Record that a lead's file went to a bank. Returns (row, created).

        Idempotent on (lead, bank), as the caller re-sends whenever the
        lead is shared into the group again. A repeat keeps the ORIGINAL
        shared_at — the question the grid answers is "when did this file
        first reach this bank" — and the repeat is expected to be logged
        as a message instead.
        """
        from app.models.lead_bank import LeadBank
        from app.services.bank_registry import get_bank_names

        await self._require_fmc_banks()

        bank_name = payload["bank_name"]
        if bank_name not in await get_bank_names(self.db):
            raise BadRequestError(
                f"bank_name must be one of the canonical FMC banks "
                f"(got '{bank_name}'). See GET /leads/banks."
            )

        lead = await self.get_lead(lead_id, user)

        shared_by = payload.get("shared_by")
        if shared_by is not None:
            # Validated rather than trusted: an id from another tenant
            # would otherwise fail at the FK as a 500.
            ok = (await self.db.execute(
                select(Profile.id).where(
                    Profile.id == shared_by,
                    Profile.company_id == self.company_id,
                )
            )).scalar_one_or_none()
            if ok is None:
                raise BadRequestError(
                    f"shared_by {shared_by} is not a user in this company. "
                    f"See GET /users."
                )

        existing = await self._get_lead_bank(lead_id, bank_name)
        if existing is not None:
            # Fill in provenance only where it is genuinely missing (a row
            # created through the UI, or backfilled). Never overwrite.
            touched = False
            if existing.shared_at is None and payload.get("shared_at"):
                existing.shared_at = payload["shared_at"]
                touched = True
            if existing.shared_by is None and shared_by:
                existing.shared_by = shared_by
                touched = True
            if existing.source is None and payload.get("source"):
                existing.source = payload["source"]
                touched = True
            if existing.wa_group_id is None and payload.get("wa_group_id"):
                existing.wa_group_id = payload["wa_group_id"]
                touched = True
            if touched:
                await self.db.commit()
                await self.db.refresh(existing)
            return existing, False

        entry = LeadBank(
            company_id=self.company_id,
            lead_id=lead_id,
            bank_name=bank_name,
            # Schema default. Sharing a file IS applying to that bank, and
            # 'applied' is the lowest rung — this never advances a status
            # that already exists, because that path returns above.
            bank_status="applied",
            shared_at=payload.get("shared_at") or now_utc(),
            shared_by=shared_by,
            source=payload.get("source") or "whatsapp",
            wa_group_id=payload.get("wa_group_id"),
        )
        self.db.add(entry)
        await self.db.flush()
        # Keep the Kanban tile's primary bank consistent with the table,
        # exactly as the manual add_bank path does.
        await self._resync_primary_bank(lead)
        await self.db.commit()
        await self.db.refresh(entry)
        return entry, True

    async def add_bank_message(
        self, lead_id: uuid.UUID, bank_name: str, payload: dict, user: Profile,
    ) -> tuple[object, bool]:
        """Append a message to a (lead, bank) conversation.

        Returns (row, created). Idempotent on wa_message_id so WhatsApp
        redelivery is a no-op rather than a duplicate line in the thread.
        """
        from app.models.lead_bank_message import LeadBankMessage

        await self._require_fmc_banks()
        await self.get_lead(lead_id, user)
        entry = await self._get_lead_bank(lead_id, bank_name)
        if entry is None:
            raise NotFoundError(
                f"This lead has not been shared with '{bank_name}'. "
                f"POST /leads/{{id}}/bank-shares first."
            )

        wa_id = payload.get("wa_message_id")
        if wa_id:
            dupe = (await self.db.execute(
                select(LeadBankMessage).where(
                    LeadBankMessage.wa_message_id == wa_id
                )
            )).scalar_one_or_none()
            if dupe is not None:
                return dupe, False

        msg = LeadBankMessage(
            company_id=self.company_id,
            lead_bank_id=entry.id,
            body=payload["body"],
            sender_phone=payload.get("sender_phone"),
            sender_name=payload.get("sender_name"),
            is_our_team=bool(payload.get("is_our_team")),
            wa_message_id=wa_id,
        )
        if payload.get("created_at"):
            msg.created_at = payload["created_at"]
        self.db.add(msg)
        try:
            await self.db.commit()
        except Exception:
            # Lost the race on the partial unique index — treat the
            # winner's row as the result, same as the pre-check would.
            await self.db.rollback()
            if wa_id:
                dupe = (await self.db.execute(
                    select(LeadBankMessage).where(
                        LeadBankMessage.wa_message_id == wa_id
                    )
                )).scalar_one_or_none()
                if dupe is not None:
                    return dupe, False
            raise
        await self.db.refresh(msg)
        return msg, True

    async def _share_rollup(self, lead_bank_ids: list[uuid.UUID]) -> dict:
        """message_count / last_message_at / preview per lead_bank id,
        in one query rather than one per cell."""
        from app.models.lead_bank_message import LeadBankMessage
        if not lead_bank_ids:
            return {}
        rows = (await self.db.execute(
            select(
                LeadBankMessage.lead_bank_id,
                func.count().label("n"),
                func.max(LeadBankMessage.created_at).label("last_at"),
            )
            .where(LeadBankMessage.lead_bank_id.in_(lead_bank_ids))
            .group_by(LeadBankMessage.lead_bank_id)
        )).all()
        roll = {r[0]: {"message_count": r[1], "last_message_at": r[2]} for r in rows}

        # Newest body per pair, for the grid's inline preview.
        if roll:
            latest = (await self.db.execute(
                select(LeadBankMessage.lead_bank_id, LeadBankMessage.body,
                       LeadBankMessage.created_at)
                .where(LeadBankMessage.lead_bank_id.in_(list(roll)))
                .order_by(LeadBankMessage.lead_bank_id,
                          LeadBankMessage.created_at.desc())
            )).all()
            seen = set()
            for lb_id, body, _ in latest:
                if lb_id in seen:
                    continue
                seen.add(lb_id)
                roll[lb_id]["last_message_preview"] = (body or "")[:120]
        return roll

    async def list_bank_shares(self, lead_id: uuid.UUID, user: Profile) -> list[dict]:
        """Every bank this lead has been shared with, with rollups."""
        from app.models.lead_bank import LeadBank
        await self._require_fmc_banks()
        await self.get_lead(lead_id, user)
        rows = (await self.db.execute(
            select(LeadBank)
            .where(LeadBank.lead_id == lead_id, LeadBank.company_id == self.company_id)
            .order_by(LeadBank.shared_at.desc().nullslast())
        )).scalars().all()
        roll = await self._share_rollup([r.id for r in rows])
        return [self._share_to_dict(r, roll.get(r.id, {})) for r in rows]

    def _share_to_dict(self, row, rollup: dict) -> dict:
        return {
            "id": row.id,
            "lead_id": row.lead_id,
            "bank_name": row.bank_name,
            "bank_status": row.bank_status,
            "shared_at": row.shared_at,
            "shared_by": row.shared_by,
            "shared_by_name": row.sharer.full_name if row.sharer else None,
            "source": row.source,
            "wa_group_id": row.wa_group_id,
            "message_count": rollup.get("message_count", 0),
            "last_message_at": rollup.get("last_message_at"),
            "created_at": row.created_at,
        }

    async def get_bank_share_detail(
        self, lead_id: uuid.UUID, bank_name: str, user: Profile,
    ) -> dict:
        """One share plus its full conversation — the hover payload."""
        from app.models.lead_bank_message import LeadBankMessage
        await self._require_fmc_banks()
        await self.get_lead(lead_id, user)
        entry = await self._get_lead_bank(lead_id, bank_name)
        if entry is None:
            raise NotFoundError(f"This lead has not been shared with '{bank_name}'.")
        msgs = (await self.db.execute(
            select(LeadBankMessage)
            .where(LeadBankMessage.lead_bank_id == entry.id)
            .order_by(LeadBankMessage.created_at)
        )).scalars().all()
        roll = await self._share_rollup([entry.id])
        out = self._share_to_dict(entry, roll.get(entry.id, {}))
        out["messages"] = msgs
        return out

    async def bank_share_grid(
        self, user: Profile, page: int = 1, page_size: int = 25,
        stage: list[str] | None = None,
        agent_id: list[uuid.UUID] | None = None,
        bank_name: list[str] | None = None, shared_only: bool = False,
        q: str | None = None,
    ) -> dict:
        """Leads with all their bank shares, in one round trip.

        Three queries total regardless of page size — leads, their shares,
        message rollups — because a request per cell (19 banks x page) is
        unusable against a database with this latency.
        """
        from app.models.lead_bank import LeadBank
        from app.services.bank_registry import get_all_bank_names

        await self._require_fmc_banks()

        # A multi-select that has just been cleared still tends to send the
        # param with an empty value (?current_stage=), which would other-
        # wise filter on a stage literally named "" and return an empty
        # grid that looks like a broken page. Blanks mean "no filter".
        stage = [s.strip() for s in (stage or []) if s and s.strip()]
        bank_name = [b.strip() for b in (bank_name or []) if b and b.strip()]
        agent_id = [a for a in (agent_id or []) if a]

        base = select(Lead).where(
            Lead.company_id == self.company_id,
            Lead.is_deleted == False,  # noqa: E712
        )
        if user.role in RESTRICTED_VIEW_ROLES:
            base = base.where(or_(
                Lead.assigned_agent_id == user.id,
                Lead.pre_counsellor_id == user.id,
            ))
        elif agent_id:
            base = base.where(or_(
                Lead.assigned_agent_id.in_(agent_id),
                Lead.pre_counsellor_id.in_(agent_id),
            ))
        if stage:
            base = base.where(Lead.current_stage.in_(stage))
        if q:
            base = base.where(or_(
                Lead.full_name.ilike(f"%{q}%"),
                Lead.phone.ilike(f"%{q}%"),
                Lead.email.ilike(f"%{q}%"),
            ))
        # "Only leads that have gone to a bank" — and optionally to a
        # specific set of banks, which is how you answer "everything
        # sitting with PNB or Axis right now". Multiple banks OR together:
        # a lead shared with either one is a hit, matching what ticking two
        # boxes in a multi-select means. (An "at ALL of these banks" filter
        # would need a GROUP BY … HAVING count = n instead; nobody has
        # asked for it, so it isn't built.)
        if shared_only or bank_name:
            sub = select(LeadBank.lead_id).where(LeadBank.company_id == self.company_id)
            if bank_name:
                sub = sub.where(LeadBank.bank_name.in_(bank_name))
            base = base.where(Lead.id.in_(sub))

        base = base.order_by(Lead.created_at.desc())
        paged = await paginate(self.db, base, page, page_size)
        leads = paged["items"]

        lead_ids = [l.id for l in leads]
        shares = []
        if lead_ids:
            shares = (await self.db.execute(
                select(LeadBank)
                .where(LeadBank.lead_id.in_(lead_ids),
                       LeadBank.company_id == self.company_id)
            )).scalars().all()
        roll = await self._share_rollup([s.id for s in shares])

        from app.core.constants import LAKH_IN_RUPEES

        by_lead: dict = {}
        pf_paid_by_lead: dict = {}
        for s in shares:
            r = roll.get(s.id, {})
            # lead_banks.loan_amount is rupees; the UI speaks lakhs.
            # Converted once, here, so no caller has to remember which
            # column is in which unit.
            amount_lakh = (
                (s.loan_amount / LAKH_IN_RUPEES) if s.loan_amount is not None else None
            )
            by_lead.setdefault(s.lead_id, {})[s.bank_name] = {
                "entry_id": s.id,
                "shared_at": s.shared_at,
                "shared_by_name": s.sharer.full_name if s.sharer else None,
                "source": s.source,
                "bank_status": s.bank_status,
                "message_count": r.get("message_count", 0),
                "last_message_at": r.get("last_message_at"),
                "last_message_preview": r.get("last_message_preview"),
                "loan_amount_lakh": amount_lakh,
            }
            if s.bank_status == "pf_paid":
                pf_paid_by_lead.setdefault(s.lead_id, []).append({
                    "bank_name": s.bank_name,
                    "loan_amount_lakh": amount_lakh,
                })
        # Stable order so the row doesn't reshuffle between page loads.
        for v in pf_paid_by_lead.values():
            v.sort(key=lambda b: b["bank_name"])

        agent_names = await self._agent_name_map(
            [l.assigned_agent_id for l in leads if l.assigned_agent_id]
        )

        return {
            # ALL banks, not just active ones: a lender you have stopped
            # working with still had real files go to it, and dropping its
            # column would make that history invisible.
            "banks": list(await get_all_bank_names(self.db)),
            "items": [
                {
                    "lead_id": l.id,
                    "serial_no": l.serial_no,
                    "full_name": l.full_name,
                    "phone": l.phone,
                    "counsellor_name": agent_names.get(l.assigned_agent_id),
                    "current_stage": l.current_stage,
                    "loan_amount": l.loan_amount,
                    "pf_paid_banks": pf_paid_by_lead.get(l.id, []),
                    "shares": by_lead.get(l.id, {}),
                }
                for l in leads
            ],
            "total": paged["total"],
            "page": paged["page"],
            "page_size": paged["page_size"],
            "total_pages": paged["total_pages"],
        }

    async def _agent_name_map(self, ids: list) -> dict:
        ids = [i for i in ids if i]
        if not ids:
            return {}
        rows = (await self.db.execute(
            select(Profile.id, Profile.full_name).where(Profile.id.in_(ids))
        )).all()
        return {r[0]: r[1] for r in rows}

    # ─── AI board ⇄ normal board ────────────────────────────────────────

    async def move_pipeline(
        self, lead_id: uuid.UUID, target: str, user: Profile,
        reason: str | None = None,
    ):
        """Hand a lead between the AI board and the counsellor board.

        Moving to 'normal' is the handover: the lead leaves the AI board
        and future campaigns skip it (campaign_worker filters on
        pipeline='ai'), so the AI never cold-calls someone a counsellor is
        working. Its campaign rows and call history are untouched — that
        history is usually the reason the lead is worth taking over.

        Moving back to 'ai' is allowed so a mistake is reversible.
        """
        from app.core.constants import (
            PIPELINE_VALUES, PIPELINE_AI, PIPELINE_NORMAL,
            AI_PIPELINE_STAGE_VALUES,
        )
        from app.models.lead_remark import LeadRemark

        if target not in PIPELINE_VALUES:
            raise BadRequestError(
                f"pipeline must be one of {list(PIPELINE_VALUES)} (got '{target}')."
            )

        lead = await self.get_lead(lead_id, user)
        previous = lead.pipeline or PIPELINE_NORMAL
        if previous == target:
            return lead  # idempotent

        # Going back to the AI board only makes sense from a stage that
        # board can display; otherwise the lead would be invisible again.
        if target == PIPELINE_AI and lead.current_stage not in AI_PIPELINE_STAGE_VALUES:
            raise BadRequestError(
                f"This lead is at stage '{lead.current_stage}', which the AI "
                f"board does not show. Move it to one of "
                f"{list(AI_PIPELINE_STAGE_VALUES)} first, or leave it on the "
                f"normal pipeline."
            )

        lead.pipeline = target
        body = (
            f"Moved from the {previous} pipeline to the {target} pipeline"
            + (f": {reason}" if reason else ".")
        )
        self.db.add(LeadRemark(
            company_id=self.company_id, lead_id=lead.id,
            author_id=user.id, author_role=user.role, body=body,
        ))
        await self.db.commit()
        await self.db.refresh(lead)
        invalidate_kanban_cache_for_company(self.company_id)
        logger.info(
            "LEAD %s pipeline %s -> %s by %s", lead.id, previous, target, user.email,
        )
        return lead

    async def get_lead_campaigns(self, lead_ids: list) -> dict:
        """campaign membership per lead, for the 'which campaign did this
        come from?' block on the lead page."""
        from app.models.campaign import Campaign
        from app.models.campaign_lead import CampaignLead
        if not lead_ids:
            return {}
        rows = (await self.db.execute(
            select(
                CampaignLead.lead_id, Campaign.id, Campaign.name,
                Campaign.status, CampaignLead.status, CampaignLead.attempt_count,
                CampaignLead.created_at,
            )
            .join(Campaign, Campaign.id == CampaignLead.campaign_id)
            .where(CampaignLead.lead_id.in_(lead_ids))
            .order_by(CampaignLead.created_at.desc())
        )).all()
        out: dict = {}
        for lid, cid, cname, cstatus, clstatus, attempts, enrolled in rows:
            out.setdefault(lid, []).append({
                "campaign_id": cid, "campaign_name": cname,
                "campaign_status": cstatus, "lead_status": clstatus,
                "attempt_count": attempts, "enrolled_at": enrolled,
            })
        return out
