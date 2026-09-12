"""Loan amount is mandatory when a lead leaves 'created'.

The rule (FMC, Sep 2026): moving a lead out of `created` to anything
other than `dnp` or `lost` requires a loan amount. Leaving `created`
means somebody spoke to the student, and the first thing that
conversation establishes is how much they need.

`dnp` and `lost` are exempt deliberately — a lead who never picked up
has no amount to give, and blocking that transition would stop a
telecaller recording what actually happened.

DB-free by construction: the decision lives in the pure
`loan_amount_required()` predicate, and the gate itself is driven with
in-memory doubles (same approach as tests/test_auto_stage.py), because
DB-backed pytest against Supabase Korea is unusable.
"""
import uuid

import pytest

from app.core.constants import (
    LeadStage, UserRole, get_transitions_for_brand,
)
from app.core.exceptions import BadRequestError
from app.services.stage_machine import StageMachine, loan_amount_required

FMC = "default"
EXEMPT = (LeadStage.DNP, LeadStage.LOST)

# Everything FMC allows as a target when the lead is at `created`.
FROM_CREATED = get_transitions_for_brand(FMC)[LeadStage.CREATED]
GATED = [s for s in FROM_CREATED if s not in EXEMPT]


# ── The rule itself ───────────────────────────────────────────────────

@pytest.mark.parametrize("target", GATED, ids=lambda s: s.value)
def test_every_non_exempt_target_needs_an_amount(target):
    assert loan_amount_required(LeadStage.CREATED, target, FMC) is True


@pytest.mark.parametrize("target", EXEMPT, ids=lambda s: s.value)
def test_dnp_and_lost_are_exempt(target):
    assert loan_amount_required(LeadStage.CREATED, target, FMC) is False


def test_the_gate_covers_every_target_fmc_allows():
    """Guard against a new FMC stage quietly escaping the rule: if
    someone adds a target reachable from `created`, it is gated unless
    it was deliberately added to EXEMPT."""
    assert set(GATED) | set(EXEMPT) == set(FROM_CREATED)
    assert len(GATED) >= 8, f"expected the full FMC fan-out, got {GATED}"


@pytest.mark.parametrize("source", [
    s for s in get_transitions_for_brand(FMC) if s != LeadStage.CREATED
])
def test_only_leaving_created_is_gated(source):
    """A lead already past `created` is not re-gated — the amount was
    captured on the way out, and re-asking would block ordinary
    downstream work like processing → sanctioned."""
    for target in get_transitions_for_brand(FMC).get(source, []):
        assert loan_amount_required(source, target, FMC) is False


# ── Brand scoping ─────────────────────────────────────────────────────

@pytest.mark.parametrize("slug", ["admitverse", "Admitverse", "ADMITVERSE"])
def test_admitverse_is_never_gated(slug):
    """Admitverse tracks a budget in its own currency, not a loan. Case
    is normalised, matching every other brand gate in constants.py."""
    assert loan_amount_required(LeadStage.CREATED, LeadStage.QUALIFIED, slug) is False


@pytest.mark.parametrize("slug", [None, "", "default", "some-new-brand", "iconiq"])
def test_unknown_slug_is_gated_like_fmc(slug):
    """Unknown slugs fall back to FundMyCampus everywhere else in the
    codebase; this gate must not be the one place that disagrees."""
    assert loan_amount_required(LeadStage.CREATED, LeadStage.QUALIFIED, slug) is True


# ── The gate in situ, driving the real StageMachine ───────────────────

class _Lead:
    def __init__(self, lakh=None):
        self.id = uuid.uuid4()
        self.company_id = uuid.uuid4()
        self.current_stage = LeadStage.CREATED.value
        self.assigned_agent_id = None
        self.pre_counsellor_id = None
        self.is_deleted = False
        self.loan_amount = None
        self.loan_amount_lakh = lakh


class _User:
    def __init__(self):
        self.id = uuid.uuid4()
        self.role = UserRole.ADMIN


class _Result:
    def __init__(self, lead): self._lead = lead
    def scalar_one_or_none(self): return self._lead
    def scalar(self): return None
    def scalars(self): return self
    def all(self): return []
    def first(self): return None
    def __iter__(self): return iter(())
    rowcount = 0


class _DB:
    """Returns the lead for the opening SELECT, then nothing useful —
    enough to reach the gate, which raises before the rest is needed."""
    def __init__(self, lead): self._lead = lead
    def add(self, obj): pass
    async def execute(self, *a, **k): return _Result(self._lead)
    async def flush(self): pass
    async def commit(self): pass


def _machine(lead, slug=FMC):
    m = StageMachine(_DB(lead), lead.company_id)
    m._slug = slug          # instance cache — skips the Company lookup
    return m


async def _move(lead, to_stage, *, slug=FMC, amount=None):
    return await _machine(lead, slug).transition(
        lead_id=lead.id, to_stage=to_stage.value, user=_User(),
        due_date=None, loan_amount_lakh=amount,
    )


async def test_leaving_created_without_an_amount_is_rejected():
    with pytest.raises(BadRequestError) as e:
        await _move(_Lead(), LeadStage.QUALIFIED)
    assert "loan amount is required" in str(e.value).lower()


async def test_the_error_names_the_exemptions():
    """The telecaller reading this needs to know the way out."""
    with pytest.raises(BadRequestError) as e:
        await _move(_Lead(), LeadStage.PROCESSING)
    msg = str(e.value).lower()
    assert "dnp" in msg and "lost" in msg


async def test_a_zero_amount_is_rejected():
    from decimal import Decimal
    with pytest.raises(BadRequestError) as e:
        await _move(_Lead(), LeadStage.QUALIFIED, amount=Decimal("0"))
    assert "greater than 0" in str(e.value)


async def test_an_amount_already_on_the_lead_satisfies_the_gate():
    """No BadRequestError about the loan amount — the run fails later,
    in the DB-dependent tail, which is out of scope here."""
    from decimal import Decimal
    lead = _Lead(lakh=Decimal("25"))
    try:
        await _move(lead, LeadStage.QUALIFIED)
    except BadRequestError as e:                     # pragma: no cover
        assert "loan amount is required" not in str(e).lower()
    except Exception:
        pass


async def test_supplying_the_amount_writes_both_columns():
    """loan_amount_lakh drives every filter, sort and report;
    loan_amount is what the tile shows. One without the other leaves a
    lead looking filled in but unfilterable."""
    from decimal import Decimal
    lead = _Lead()
    try:
        await _move(lead, LeadStage.QUALIFIED, amount=Decimal("12.5"))
    except Exception:
        pass
    assert lead.loan_amount_lakh == Decimal("12.5")
    assert lead.loan_amount == "12.5"


async def test_dnp_needs_no_amount():
    lead = _Lead()
    try:
        await _move(lead, LeadStage.DNP)
    except BadRequestError as e:                     # pragma: no cover
        assert "loan amount is required" not in str(e).lower()
    except Exception:
        pass


async def test_admitverse_lead_is_not_gated():
    lead = _Lead()
    lead.current_stage = LeadStage.CREATED.value
    try:
        await _move(lead, LeadStage.CONTACTED, slug="admitverse")
    except BadRequestError as e:                     # pragma: no cover
        assert "loan amount is required" not in str(e).lower()
    except Exception:
        pass


# ── The amount is written in a shape the tile and the UI accept ───────

@pytest.mark.parametrize("lakh,expected", [
    ("12.5", "12.5"),
    ("25", "25"),
    ("30", "30"),        # Decimal.normalize() renders this "3E+1"
    ("100", "100"),      # ...and this "1E+2"
    ("1000", "1000"),
    ("12.50", "12.5"),   # trailing zero still trimmed
    ("0.5", "0.5"),
])
async def test_loan_amount_is_written_as_a_plain_number(lakh, expected):
    """FMC's loan_amount must be a bare number in lakhs. Scientific
    notation breaks the tile AND makes the lead uneditable, because the
    CRM-UI amount input rejects every keystroke on a non-numeric value
    (known bug, 15 FMC leads already stuck that way)."""
    from decimal import Decimal
    lead = _Lead()
    try:
        await _move(lead, LeadStage.QUALIFIED, amount=Decimal(lakh))
    except Exception:
        pass
    assert lead.loan_amount == expected
    assert "E" not in lead.loan_amount.upper()
