from app.models.base import Base
from app.models.company import Company
from app.models.profile import Profile
from app.models.lead_source import LeadSource
from app.models.lead import Lead
from app.models.lead_stage_log import LeadStageLog
from app.models.lead_remark import LeadRemark
from app.models.bank import Bank
from app.models.lead_bank import LeadBank
from app.models.lead_bank_message import LeadBankMessage
from app.models.lead_application import LeadApplication
from app.models.call_attempt import CallAttempt
from app.models.task import Task
from app.models.notification import Notification
from app.models.csv_import import CSVImport
from app.models.activity_log import ActivityLog
from app.models.ai_agent import AIAgent
from app.models.campaign import Campaign
from app.models.campaign_lead import CampaignLead
from app.models.invoice_settings import InvoiceSettings
from app.models.invoice_counter import InvoiceCounter
from app.models.invoice import Invoice
from app.models.bank_disbursement import BankDisbursement
from app.models.website_submission import WebsiteSubmission
from app.models.api_key import ApiKey
# Meta Lead Ads gateway. These two were never imported here, so they were
# absent from Base.metadata — with two consequences, both real:
#   1. A fresh database never got the tables (create_all only emits what
#      is in the metadata), and the Meta retry worker runs every 20s on
#      EVERY deployment regardless of brand, so a new tenant threw
#      UndefinedTableError forever. Found standing up Iconiq, Sep 2026.
#   2. alembic autogenerate compares metadata against the database, so a
#      generated migration could propose dropping them on FMC.
from app.models.meta_form_routing import MetaFormRouting
from app.models.meta_webhook_event import MetaWebhookEvent

__all__ = [
    "Base",
    "Company",
    "Profile",
    "LeadSource",
    "Lead",
    "LeadStageLog",
    "LeadRemark",
    "Bank",
    "LeadBank",
    "LeadBankMessage",
    "LeadApplication",
    "CallAttempt",
    "Task",
    "Notification",
    "CSVImport",
    "ActivityLog",
    "AIAgent",
    "Campaign",
    "CampaignLead",
    "InvoiceSettings",
    "InvoiceCounter",
    "Invoice",
    "BankDisbursement",
    "WebsiteSubmission",
    "ApiKey",
    "MetaFormRouting",
    "MetaWebhookEvent",
]
