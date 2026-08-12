"""Automatic stage movement after an AI call hangs up.

Admitverse used to walk created → contacted → connected → qualified one
stage per call, with NO route to DNP and NO route to LOST. In production
that meant:

  * an unanswered call moved the lead to "contacted", so people who never
    picked up looked the same as people we had spoken to
  * "not interested" never reached LOST and stayed in the working queue
  * qualifying required FOUR conditions at once, so 36 genuinely
    interested people on the Sep'26 campaign sat at "connected" and had
    to be moved by hand

These pin the replacement decision matrix. They drive the real
_av_auto_advance / _fmc_auto_advance with in-memory doubles — no
database, so they run in milliseconds.
"""
import uuid

import pytest

from app.api.v1.voice import (
    _av_auto_advance, _AV_AUTO_ADVANCE_STAGES, _AV_DNP_LOST_THRESHOLD,
)


class _Lead:
    def __init__(self, stage="created", attempts=0):
        self.id = uuid.uuid4()
        self.company_id = uuid.uuid4()
        self.current_stage = stage
        self.call_attempt_count = attempts
        self.assigned_agent_id = uuid.uuid4()
        self.created_by = uuid.uuid4()
        self.full_name = "Test Student"
        self.connected_time = None
        self.due_date = None
        self.lost_time = None
        self.lost_reason = None


class _Call:
    def __init__(self):
        self.id = uuid.uuid4()
        self.transcript = "User: yes\nAgent: hello"
        self.call_duration_seconds = 60
        self.call_status = "ended"


class _Result:
    """Permissive empty result — the handler's tail calls
    auto_complete_stale_call_tasks, which queries for stale tasks."""
    def scalar_one_or_none(self): return None
    def scalar(self): return None
    def scalars(self): return self
    def all(self): return []
    def first(self): return None
    def __iter__(self): return iter(())
    rowcount = 0


class _DB:
    """Collects what would have been written instead of writing it."""
    def __init__(self):
        self.added = []
        self.queries = 0
    def add(self, obj): self.added.append(obj)
    async def execute(self, *a, **k):
        self.queries += 1
        return _Result()
    async def commit(self): pass


async def run(stage="created", *, connected=True, sentiment="neutral",
              interest="low", summary="", attempts=0):
    lead, db = _Lead(stage, attempts), _DB()
    await _av_auto_advance(db, _Call(), lead, sentiment, interest,
                           summary, lead.assigned_agent_id, connected)
    return lead, db


# ── Not picked up → DNP ────────────────────────────────────────────────

@pytest.mark.parametrize("stage", ["created", "contacted", "connected"])
async def test_no_answer_goes_to_dnp(stage):
    lead, _ = await run(stage, connected=False)
    assert lead.current_stage == "dnp_pre_qualified"


async def test_no_answer_does_not_mark_as_contacted():
    """The old behaviour: a lead nobody answered looked 'contacted',
    indistinguishable from one we actually spoke to."""
    lead, _ = await run("created", connected=False)
    assert lead.current_stage != "contacted"


async def test_repeated_no_answer_eventually_goes_lost():
    lead, _ = await run("dnp_pre_qualified", connected=False,
                        attempts=_AV_DNP_LOST_THRESHOLD)
    assert lead.current_stage == "lost"
    assert "unanswered" in lead.lost_reason
    assert lead.lost_time is not None


async def test_dnp_below_threshold_stays_dnp():
    lead, _ = await run("dnp_pre_qualified", connected=False,
                        attempts=_AV_DNP_LOST_THRESHOLD - 1)
    assert lead.current_stage == "dnp_pre_qualified"


# ── Not interested → LOST ──────────────────────────────────────────────

async def test_negative_sentiment_goes_lost():
    lead, _ = await run(sentiment="negative")
    assert lead.current_stage == "lost"
    assert lead.lost_time is not None


@pytest.mark.parametrize("phrase", [
    "The user said they are not interested in studying abroad",
    "User declined and asked not to be called",
    "Wrong number, the person has already enrolled elsewhere",
])
async def test_decline_phrasing_goes_lost_even_when_sentiment_is_neutral(phrase):
    """The sentiment classifier biases neutral; the summary text is the
    backstop."""
    lead, _ = await run(sentiment="neutral", summary=phrase)
    assert lead.current_stage == "lost"


# ── Interested → QUALIFIED ─────────────────────────────────────────────

async def test_positive_goes_straight_to_qualified():
    lead, _ = await run(sentiment="positive")
    assert lead.current_stage == "qualified"
    assert lead.due_date is not None, "should get a follow-up date"


async def test_qualifies_from_connected_in_one_step():
    """The 36 leads stranded at 'connected' are the reason this exists."""
    lead, _ = await run("connected", sentiment="positive")
    assert lead.current_stage == "qualified"


@pytest.mark.parametrize("level", ["high", "medium"])
async def test_medium_or_high_interest_qualifies(level):
    lead, _ = await run(sentiment="neutral", interest=level)
    assert lead.current_stage == "qualified"


async def test_study_intent_in_summary_qualifies_a_neutral_call():
    lead, _ = await run(
        sentiment="neutral", interest="low",
        summary="The user is interested in a Masters in Germany for the Sep intake")
    assert lead.current_stage == "qualified"


async def test_qualified_creates_a_follow_up_task_and_notification():
    _, db = await run(sentiment="positive")
    kinds = {type(o).__name__ for o in db.added}
    assert "Task" in kinds, "a qualified lead needs a follow-up task"
    assert "Notification" in kinds
    assert "LeadStageLog" in kinds, "the move must be auditable"


# ── Interested, but later → OPPORTUNITY ────────────────────────────────

async def test_future_intent_goes_to_opportunity_not_qualified():
    lead, _ = await run(
        sentiment="positive",
        summary="Student is in 12th and plans to apply next year")
    assert lead.current_stage == "opportunity"


# ── Neutral conversation → CONNECTED ───────────────────────────────────

async def test_real_but_inconclusive_conversation_marks_connected():
    lead, _ = await run("created", sentiment="neutral", interest="low")
    assert lead.current_stage == "connected"


# ── Guards ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("stage", [
    "processing", "docs_collected", "application_done", "visa_applied",
    "enrolled", "lost",
])
async def test_never_touches_human_worked_stages(stage):
    """Documents, applications and visas are counsellor work — an AI call
    must not move a lead that has got that far."""
    lead, db = await run(stage, sentiment="positive")
    assert lead.current_stage == stage
    assert db.added == []


async def test_no_op_when_target_equals_current_stage():
    lead, db = await run("lost", sentiment="negative")
    assert db.added == []


async def test_auto_advance_stage_set_is_early_stages_only():
    assert _AV_AUTO_ADVANCE_STAGES == {
        "created", "contacted", "connected", "dnp_pre_qualified"}
