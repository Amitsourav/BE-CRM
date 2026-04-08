from __future__ import annotations

import enum


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    MANAGER = "manager"
    TELECALLER = "telecaller"


class LeadStage(str, enum.Enum):
    LEAD = "lead"
    CALLED = "called"
    CONNECTED = "connected"
    QUALIFIED_LEAD = "qualified_lead"
    WON = "won"
    LOST = "lost"


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


# Valid stage transitions
VALID_TRANSITIONS: dict[LeadStage, list[LeadStage]] = {
    LeadStage.LEAD: [LeadStage.CALLED, LeadStage.LOST],
    LeadStage.CALLED: [LeadStage.CONNECTED, LeadStage.LOST],
    LeadStage.CONNECTED: [LeadStage.QUALIFIED_LEAD, LeadStage.LOST],
    LeadStage.QUALIFIED_LEAD: [LeadStage.WON, LeadStage.LOST],
    LeadStage.WON: [],  # terminal
    LeadStage.LOST: [LeadStage.LEAD],  # admin-only reopen
}

# Stages that require notes
STAGES_REQUIRING_NOTES = {
    LeadStage.CALLED,
    LeadStage.CONNECTED,
    LeadStage.QUALIFIED_LEAD,
}
