"""add post_call_analysis_prompt to ai_agents

Revision ID: h3c4d5e6f7g8
Revises: g2b3c4d5e6f7
Create Date: 2026-05-05

The post-call LLM analysis system prompt was hardcoded in voice.py with
FMC-specific context ("FundMyCampus, education-loan consultancy", "agent
is Priya", asks for loan_amount + banks_tried). On Admitverse calls this
produced FMC-shaped extraction with empty fields and a wrong-brand summary
framing.

Move the prompt to ai_agents.post_call_analysis_prompt so each agent owns
its own analysis behavior. Backfill existing FMC agents with the legacy
FMC prompt so behavior is unchanged on FMC. Admitverse rows stay NULL
and fall back to a generic safe prompt in code.
"""
from alembic import op
import sqlalchemy as sa


revision = "h3c4d5e6f7g8"
down_revision = "g2b3c4d5e6f7"
branch_labels = None
depends_on = None


# Pasted from voice.py:1488-1551 verbatim — keeps FMC behavior identical
# after the move. Single source of truth lives here now; future changes
# happen via the admin UI updating the per-agent column.
_FMC_LEGACY_PROMPT = (
    "You analyse sales call transcripts for FundMyCampus, an Indian "
    "education-loan consultancy. The agent is Priya. Calls happen in "
    "Hinglish (Hindi+English). Focus on what the USER (the lead) said, "
    "NOT what Priya asked. Return ONLY valid JSON with these fields:\n\n"
    '- "summary": 3-5 sentences IN ENGLISH. Mention specific facts the '
    'user revealed: loan amount, college/university, course, country, '
    'intake, banks already tried, co-applicant. Avoid generic phrasing '
    'like "the user expressed interest" — quote specifics. If the call '
    'was very short or the user said almost nothing, write a one-line '
    'summary stating that.\n'
    '- "sentiment": "positive" if user clearly engaged, asked questions, '
    'agreed to next steps, or shared loan details. "negative" if user '
    'declined, asked not to be called, or was hostile. "neutral" for '
    'short / inconclusive / unclear calls. Default to "neutral" in doubt.\n'
    '- "confidence": integer 0-100. Your confidence in the sentiment / '
    'interest assessment given the transcript length and clarity. '
    'For transcripts under 200 chars, max confidence is 40.\n'
    '- "interest_level": "high" ONLY if user EXPLICITLY did 2+ of: '
    'asked about rates/amounts, named a college/course, asked next steps, '
    'said yes to WhatsApp/callback, or shared specific timeline. '
    '"medium" if user shared 1 concrete detail but no commitment. '
    '"low" otherwise — including if user only said "hello"/"ok"/'
    '"haan"/"who is this". When in doubt, choose lower tier.\n'
    '- "user_name": the user\'s name if they explicitly said it (e.g. '
    '"I am Rajesh", "main Rajesh hoon", "Rajesh speaking"). null '
    'otherwise. Do NOT use Priya / Priya from FundMyCampus / agent.\n'
    '- "loan_amount": amount the user mentioned, e.g. "15 lakhs", '
    '"1 crore", "50 lakhs". null if not mentioned. Use the user\'s '
    'phrasing — do not normalise to digits.\n'
    '- "college": college / university the user named, e.g. '
    '"IIT Delhi", "Sheffield", "GLIM Gurgaon". null if not mentioned. '
    'If the user named a city by mistake (Sheffield is a city, not a '
    'university), still capture what they said.\n'
    '- "study_location": "india", "abroad", or null. Infer from college '
    'name or explicit statement.\n'
    '- "course": e.g. "MBA", "B.Tech CS", "MS", "MBBS". null if not '
    'mentioned.\n'
    '- "intake": admission intake the user mentioned, e.g. '
    '"September 2026", "Jan 2027". null if not mentioned.\n'
    '- "banks_tried": array of bank names the user said they applied '
    'with already, e.g. ["SBI", "Axis"]. Empty array if none.\n'
    '- "objections": array of specific concerns the user raised, e.g. '
    '["interest rate too high", "doesn\'t want collateral"]. Empty '
    'array if none.\n'
    '- "next_action": what was agreed at the end, e.g. '
    '"Priya will send WhatsApp message with loan link", "callback '
    'at 5pm", "user will check rates and respond". null if no '
    'concrete action was agreed.\n\n'
    "No markdown, no explanation, just the JSON object."
)


def upgrade() -> None:
    op.add_column(
        "ai_agents",
        sa.Column("post_call_analysis_prompt", sa.Text(), nullable=True),
    )

    # Backfill existing agents whose company is NOT Admitverse with the
    # legacy FMC prompt so FMC behavior is byte-identical after the move.
    # Admitverse agents (and any future tenant) keep NULL → falls back to
    # a generic safe prompt in code. Bind parameter avoids quoting issues
    # in the long string.
    op.execute(
        sa.text(
            "UPDATE ai_agents SET post_call_analysis_prompt = :prompt "
            "WHERE post_call_analysis_prompt IS NULL "
            "AND company_id IN ("
            "  SELECT id FROM companies "
            "  WHERE LOWER(slug) NOT LIKE 'admitverse%'"
            ")"
        ).bindparams(prompt=_FMC_LEGACY_PROMPT)
    )


def downgrade() -> None:
    op.drop_column("ai_agents", "post_call_analysis_prompt")
