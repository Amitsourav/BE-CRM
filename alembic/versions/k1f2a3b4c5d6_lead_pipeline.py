"""leads.pipeline — split AI campaign leads from counsellor-worked leads

Revision ID: k1f2a3b4c5d6
Revises: j0e1f2a3b4c5
Create Date: 2026-08-11

Campaign leads and hand-worked leads were mixed on one board, and
"is this an AI lead?" was DERIVED from having a campaign_leads row. That
made it impossible to hand a lead over to a counsellor: the only way to
stop it counting as a campaign lead was to delete its call history.

Storing the board explicitly makes the handover possible while the
history stays.

Also repairs 1,575 leads stranded at the legacy stage 'lead'. That stage
was dropped from the FMC pipeline in the May 2026 revamp but never
migrated, so those leads had NO Kanban column and NO valid transitions —
invisible in the pipeline and impossible to move from the UI. Every one
of them came from an AI campaign.
"""
import sqlalchemy as sa
from alembic import op


revision = "k1f2a3b4c5d6"
down_revision = "j0e1f2a3b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "leads",
        sa.Column("pipeline", sa.String(length=10),
                  server_default=sa.text("'normal'"), nullable=False),
    )

    # Anything that has ever been in a campaign belongs to the AI board.
    op.execute("""
        UPDATE leads SET pipeline = 'ai'
         WHERE id IN (SELECT lead_id FROM campaign_leads)
    """)

    # Repair the dead-end legacy stage. Guarded to campaign leads on the
    # AI board so it cannot touch anything a counsellor worked by hand.
    op.execute("""
        UPDATE leads SET current_stage = 'created'
         WHERE current_stage = 'lead' AND pipeline = 'ai'
    """)

    # A campaign lead that has since progressed through the HUMAN funnel
    # (processing, sanctioned, disbursed, opportunity...) must not sit on
    # the AI board — that board has no column for those stages, so the
    # lead would be invisible on both boards. Reaching those stages means
    # a counsellor already took it over.
    op.execute("""
        UPDATE leads SET pipeline = 'normal'
         WHERE pipeline = 'ai'
           AND current_stage NOT IN ('created','contacted','dnp','qualified','lost')
    """)

    # The AI board and the normal board are always queried by pipeline
    # plus stage; without this every board render seq-scans 10k+ rows.
    op.create_index(
        "ix_leads_pipeline_stage", "leads",
        ["company_id", "pipeline", "current_stage"],
    )


def downgrade() -> None:
    op.drop_index("ix_leads_pipeline_stage", table_name="leads")
    op.drop_column("leads", "pipeline")
