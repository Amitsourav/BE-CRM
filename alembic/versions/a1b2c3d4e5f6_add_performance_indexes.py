"""add_performance_indexes

Revision ID: a1b2c3d4e5f6
Revises: ad3e2be42946
Create Date: 2026-04-17

Adds indexes on company_id (tenant scoping) for all tables,
plus indexes on frequently queried columns (status, dates, FKs).
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "3ab1c0de4f01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── company_id indexes (tenant scoping) ──
    op.create_index("idx_leads_company_id", "leads", ["company_id"])
    op.create_index("idx_ai_agents_company_id", "ai_agents", ["company_id"])
    op.create_index("idx_call_attempts_company_id", "call_attempts", ["company_id"])
    op.create_index("idx_tasks_company_id", "tasks", ["company_id"])
    op.create_index("idx_notifications_company_id", "notifications", ["company_id"])
    op.create_index("idx_activity_logs_company_id", "activity_logs", ["company_id"])
    op.create_index("idx_lead_stage_logs_company_id", "lead_stage_logs", ["company_id"])
    op.create_index("idx_profiles_company_id", "profiles", ["company_id"])
    op.create_index("idx_csv_imports_company_id", "csv_imports", ["company_id"])

    # ── Common query indexes ──
    op.create_index("idx_leads_current_stage", "leads", ["current_stage"])
    op.create_index("idx_leads_assigned_agent_id", "leads", ["assigned_agent_id"])
    op.create_index("idx_leads_is_deleted", "leads", ["is_deleted"])
    op.create_index("idx_tasks_status_due_date", "tasks", ["status", "due_date"])
    op.create_index("idx_tasks_assigned_to", "tasks", ["assigned_to"])
    op.create_index("idx_notifications_user_read", "notifications", ["user_id", "is_read"])
    op.create_index("idx_call_attempts_lead_id", "call_attempts", ["lead_id"])
    op.create_index("idx_call_attempts_status", "call_attempts", ["call_status"])
    op.create_index("idx_call_attempts_created_at", "call_attempts", ["created_at"])
    op.create_index("idx_lead_stage_logs_lead_id", "lead_stage_logs", ["lead_id"])
    op.create_index("idx_activity_logs_entity", "activity_logs", ["entity_type", "entity_id"])


def downgrade() -> None:
    op.drop_index("idx_activity_logs_entity")
    op.drop_index("idx_lead_stage_logs_lead_id")
    op.drop_index("idx_call_attempts_created_at")
    op.drop_index("idx_call_attempts_status")
    op.drop_index("idx_call_attempts_lead_id")
    op.drop_index("idx_notifications_user_read")
    op.drop_index("idx_tasks_assigned_to")
    op.drop_index("idx_tasks_status_due_date")
    op.drop_index("idx_leads_is_deleted")
    op.drop_index("idx_leads_assigned_agent_id")
    op.drop_index("idx_leads_current_stage")
    op.drop_index("idx_csv_imports_company_id")
    op.drop_index("idx_profiles_company_id")
    op.drop_index("idx_lead_stage_logs_company_id")
    op.drop_index("idx_activity_logs_company_id")
    op.drop_index("idx_notifications_company_id")
    op.drop_index("idx_tasks_company_id")
    op.drop_index("idx_call_attempts_company_id")
    op.drop_index("idx_ai_agents_company_id")
    op.drop_index("idx_leads_company_id")
