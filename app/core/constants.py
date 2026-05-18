from __future__ import annotations

import enum


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    # Renamed from TELECALLER on 2026-05-15. FMC's two-step counsellor
    # model formalised this role as the "Pre Counsellor" — the user
    # who warms up the lead before a senior Counsellor closes it. The
    # DB enum value is also 'pre_counsellor' (alembic-renamed in-place,
    # no profile rows touched).
    PRE_COUNSELLOR = "pre_counsellor"


# Roles that can only see + act on their OWN assigned records (leads,
# tasks, calls, CSV imports). Admin sees everything; managers and
# pre-counsellors see only what's assigned to them. The previous design
# treated managers identically to admins, which broke down once a
# tenant had multiple managers — every manager could see every other
# manager's leads. The isolated-portfolio model fixes that:
#   admin → distributes leads to managers
#   manager → redistributes to their pre-counsellors (sees only their own)
#   pre-counsellor → works only their own assigned leads
RESTRICTED_VIEW_ROLES: frozenset[UserRole] = frozenset(
    {UserRole.PRE_COUNSELLOR, UserRole.MANAGER}
)


# FMC "lost" reasons. Required when transitioning a lead into the LOST
# stage. Locked dropdown — set by Amit on 2026-05-15 to standardise
# reporting and stop telecallers writing free-text variants like
# "didn't pick" / "didnt pickup" / "not picking up" that all mean the
# same thing. Order matches Amit's brief.
# FMC canonical bank list. Used by the bank_name dropdown on the Kanban
# card and the lead edit form. Locked by Amit on 2026-05-16 — telecallers
# can't free-text invent new bank names, which had produced 5+ spelling
# variants in production (sbi / SBI / Unicred / UniCred / Propelld / Propelled).
# Order = how it should appear in the dropdown.
FMC_BANKS: tuple[str, ...] = (
    "Axis",
    "PNB",
    "SBI",
    "Yes Bank",
    "ICICI",
    "IDFC",
    "BOI",
    "Kuhoo",
    "Avanse",
    "Credila",
    "Propelld",
    "Tata Capital",
    "Zolve",
    "Nomad",
    "UniCred",
    "Auxilo",
    "Incred",
    "Edgro",
)


LOST_REASONS: tuple[str, ...] = (
    "Future Plans",
    "Not responding",
    "Not Interested",
    "Not reachable / Out of service / Wrong number",
    "Plan Dropped",
    "Repeat lead",
    "Self funding",
    "Indian University",
    "Junk Lead",
    "Loan already secured",
    "Low loan amount",
    "Wrong Product (Personal/Business Loan)",
    "Country not approved/ Courses not approved.",
    "Location Not Serviceable",
    "No collateral",
    "No Cosigner",
    "Student profile not eligible",
    "Cosigner - Ineligible",
    "Lost to competitor",
    "Already applied to multiple banks",
    "Visa Reject",
)


class LeadStage(str, enum.Enum):
    # FMC pipeline (original 6)
    LEAD = "lead"
    CALLED = "called"
    CONNECTED = "connected"
    QUALIFIED_LEAD = "qualified_lead"
    WON = "won"
    LOST = "lost"

    # Admitverse pipeline (17 additional values; CONNECTED and LOST are
    # reused from above)
    CREATED = "created"
    CONTACTED = "contacted"
    DNP_PRE_QUALIFIED = "dnp_pre_qualified"
    QUALIFIED = "qualified"
    OPPORTUNITY = "opportunity"
    DNP_POST_QUALIFIED = "dnp_post_qualified"
    PROCESSING = "processing"
    IMPORTANT = "important"
    PARTIAL_DOCS_COLLECTED = "partial_docs_collected"
    DOCS_COLLECTED = "docs_collected"
    APPLICATION_DONE = "application_done"
    CONDITIONAL_DRAFT = "conditional_draft"
    UCOL = "ucol"
    DEPOSIT_PAID = "deposit_paid"
    CAS_RECEIVED = "cas_received"
    VISA_APPLIED = "visa_applied"
    ENROLLED = "enrolled"

    # FMC loan-processing pipeline (May 2026 revamp). CREATED, CONTACTED,
    # QUALIFIED, PROCESSING, OPPORTUNITY, LOST are reused from the
    # Admitverse block above. The 6 below are FMC-specific.
    DNP = "dnp"
    DOCS_PENDING = "docs_pending"
    LOGGED_IN = "logged_in"
    SANCTIONED = "sanctioned"
    PF_PAID = "pf_paid"
    DISBURSED = "disbursed"


# All 23 enum string values, in the order they appear in the DB type.
# Used by SQLAlchemy ENUM column declarations.
LEAD_STAGE_VALUES: tuple[str, ...] = tuple(s.value for s in LeadStage)


class CallDisposition(str, enum.Enum):
    DNP = "dnp"
    CONNECTED = "connected"
    BUSY = "busy"
    SWITCHED_OFF = "switched_off"
    WRONG_NUMBER = "wrong_number"
    CALLBACK = "callback"


class TaskType(str, enum.Enum):
    FOLLOW_UP = "follow_up"
    CALL = "call"
    MEETING = "meeting"
    DOCUMENT_COLLECTION = "document_collection"
    APPLICATION = "application"
    OTHER = "other"


class TaskStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    OVERDUE = "overdue"


class NotificationType(str, enum.Enum):
    LEAD_ASSIGNED = "lead_assigned"
    TASK_CREATED = "task_created"
    TASK_OVERDUE = "task_overdue"
    DNP_WARNING = "dnp_warning"
    DNP_AUTO_LOST = "dnp_auto_lost"
    STAGE_CHANGED = "stage_changed"
    CSV_IMPORT_COMPLETE = "csv_import_complete"
    GENERAL = "general"


class LeadSourceType(str, enum.Enum):
    CSV = "csv"
    META_ADS = "meta_ads"
    MANUAL = "manual"
    WHATSAPP = "whatsapp"


class CSVImportStatus(str, enum.Enum):
    UPLOADED = "uploaded"
    PREVIEWING = "previewing"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


# ── FMC loan-processing pipeline (May 2026 revamp) ─────────────────────
# Replaces the original 6-stage funnel with a 12-stage loan flow.
# Free movement, just like Admitverse — agents can move any lead to
# any non-terminal stage. Terminal states are DISBURSED (happy) and
# LOST (sad). The previous LEAD/CALLED/CONNECTED/QUALIFIED_LEAD/WON
# stages are kept in the enum for legacy data but no transition table
# routes traffic to them.
FMC_STAGES: list[LeadStage] = [
    LeadStage.CREATED,
    LeadStage.CONTACTED,
    LeadStage.DNP,
    LeadStage.QUALIFIED,
    LeadStage.PROCESSING,
    # DOCS_PENDING removed 2026-05-15 per Amit — team handles docs as
    # part of PROCESSING, no need for a separate column. Enum value
    # stays defined (legacy data + Admitverse uses it) but FMC pipeline
    # no longer shows it as a column or accepts transitions to it.
    LeadStage.LOGGED_IN,
    LeadStage.SANCTIONED,
    LeadStage.PF_PAID,
    LeadStage.DISBURSED,
    LeadStage.OPPORTUNITY,
    LeadStage.LOST,
]

FMC_TERMINAL: set[LeadStage] = {LeadStage.DISBURSED, LeadStage.LOST}


def _build_fmc_transitions() -> dict[LeadStage, list[LeadStage]]:
    table: dict[LeadStage, list[LeadStage]] = {}
    for src in FMC_STAGES:
        if src in FMC_TERMINAL:
            table[src] = []
        else:
            table[src] = [s for s in FMC_STAGES if s != src]
    # Admin reopen: LOST → CREATED so a lead can be revived.
    table[LeadStage.LOST] = [LeadStage.CREATED]
    return table


FMC_VALID_TRANSITIONS: dict[LeadStage, list[LeadStage]] = _build_fmc_transitions()

# Default export, kept for code that doesn't yet pass a brand.
VALID_TRANSITIONS = FMC_VALID_TRANSITIONS


# ── Admitverse pipeline transitions ────────────────────────────────────
# Counselor can move freely (forward + backward) between any non-terminal
# stages, can skip stages, and can drop to LOST from anywhere. Once in
# LOST or ENROLLED, the lead is final — no transitions out.
ADMITVERSE_STAGES: list[LeadStage] = [
    LeadStage.CREATED,
    LeadStage.CONTACTED,
    LeadStage.DNP_PRE_QUALIFIED,
    LeadStage.CONNECTED,
    LeadStage.QUALIFIED,
    LeadStage.OPPORTUNITY,
    LeadStage.DNP_POST_QUALIFIED,
    LeadStage.PROCESSING,
    LeadStage.IMPORTANT,
    LeadStage.PARTIAL_DOCS_COLLECTED,
    LeadStage.DOCS_COLLECTED,
    LeadStage.APPLICATION_DONE,
    LeadStage.CONDITIONAL_DRAFT,
    LeadStage.UCOL,
    LeadStage.DEPOSIT_PAID,
    LeadStage.CAS_RECEIVED,
    LeadStage.VISA_APPLIED,
    LeadStage.ENROLLED,
    LeadStage.LOST,
]

ADMITVERSE_TERMINAL: set[LeadStage] = {LeadStage.ENROLLED, LeadStage.LOST}


def _build_admitverse_transitions() -> dict[LeadStage, list[LeadStage]]:
    table: dict[LeadStage, list[LeadStage]] = {}
    for src in ADMITVERSE_STAGES:
        if src in ADMITVERSE_TERMINAL:
            table[src] = []
        else:
            # Any non-terminal stage can move to any other Admitverse
            # stage except itself.
            table[src] = [s for s in ADMITVERSE_STAGES if s != src]
    return table


ADMITVERSE_VALID_TRANSITIONS: dict[LeadStage, list[LeadStage]] = _build_admitverse_transitions()


def get_transitions_for_brand(slug: str | None) -> dict[LeadStage, list[LeadStage]]:
    """Return the valid-transitions table for a given company slug.

    Unknown / missing slugs fall back to the FMC table so any new tenant
    works out of the box with the simple 6-stage flow.
    """
    if (slug or "").lower() == "admitverse":
        return ADMITVERSE_VALID_TRANSITIONS
    return FMC_VALID_TRANSITIONS


def get_terminal_stages_for_brand(slug: str | None) -> set[LeadStage]:
    if (slug or "").lower() == "admitverse":
        return ADMITVERSE_TERMINAL
    return FMC_TERMINAL


# ── Notes requirement (per brand) ──────────────────────────────────────
# Stages that need conversation_notes + agent_agenda before moving in.
# FMC's revamp goes free-flow like Admitverse — gating every transition
# behind a notes dialog fights "any agent can move freely". Empty set =
# only `lost` keeps its lost_reason gate (enforced separately).
FMC_STAGES_REQUIRING_NOTES: set[LeadStage] = set()

# Admitverse: free movement is the design — gating every contacted/connected
# transition behind a notes-required dialog fights that. Frontend agreed:
# only `lost` keeps its lost_reason gate. If product later wants connected
# (or any other) to require notes for Admitverse, just add the LeadStage
# value to this set.
ADMITVERSE_STAGES_REQUIRING_NOTES: set[LeadStage] = set()

# Default export for back-compat with code that imports the old name.
STAGES_REQUIRING_NOTES = FMC_STAGES_REQUIRING_NOTES


def get_notes_required_for_brand(slug: str | None) -> set[LeadStage]:
    if (slug or "").lower() == "admitverse":
        return ADMITVERSE_STAGES_REQUIRING_NOTES
    return FMC_STAGES_REQUIRING_NOTES


def get_lost_reasons_for_brand(slug: str | None) -> tuple[str, ...] | None:
    # FMC has a locked 21-value dropdown (LOST_REASONS) so reports stay
    # comparable across telecallers. Admitverse doesn't have a canonical
    # list yet (Phase 5 pipeline customization still open), so FE renders
    # a free-text field. Returning None tells stage_machine to skip the
    # membership check and only require a non-empty string.
    if (slug or "").lower() == "admitverse":
        return None
    return LOST_REASONS


def get_initial_stage_for_brand(slug: str | None) -> LeadStage:
    """Initial stage assigned to a freshly-created lead. Both brands now
    start at CREATED — FMC's May 2026 revamp dropped the legacy 'lead'
    stage in favor of the loan-processing pipeline."""
    return LeadStage.CREATED


# ── FMC document checklist ─────────────────────────────────────────────
# The 6 docs every FMC loan application needs. submitted_docs on the
# lead stores the keys; the FE renders the checklist using the labels.
# Adding/removing entries here changes the standard checklist length —
# leads.docs_required default also bumps via migration if you reshape.
FMC_DOC_CHECKLIST: list[dict[str, str]] = [
    {"key": "aadhaar", "label": "Aadhaar Card"},
    {"key": "pan", "label": "PAN Card"},
    {"key": "academic", "label": "Academic Documents"},
    {"key": "offer_letter", "label": "Offer Letter"},
    {"key": "financial", "label": "Financial Documents"},
    {"key": "itr", "label": "ITR"},
]

FMC_DOC_KEYS: frozenset[str] = frozenset(d["key"] for d in FMC_DOC_CHECKLIST)
