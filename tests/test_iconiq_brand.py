"""Iconiq Energy brand wiring — pure constants, no database.

DB-backed pytest against Supabase is effectively unusable on this
project (runs get killed at 10-40 minutes), so these are deliberately
DB-free and run in milliseconds.

Half of this file tests Iconiq. The other half asserts FundMyCampus and
Admitverse are UNCHANGED, because the failure mode that actually matters
when adding a brand is not "Iconiq is wrong" — it is "Iconiq is right
and it moved something under the two live businesses".
"""
from app.core import constants as c
from app.core.constants import LeadStage


ICONIQ = "iconiq"
FMC = "default"
AV = "admitverse"


# ── The board ──────────────────────────────────────────────────────────

def test_stage_order_is_the_agreed_pipeline():
    assert [s.value for s in c.get_stages_for_pipeline("normal", ICONIQ)] == [
        "created", "contacted", "not_interested",
        "interested", "quoted", "won", "lost",
    ]


def test_only_won_and_lost_are_terminal():
    assert c.get_terminal_stages_for_brand(ICONIQ) == {LeadStage.WON, LeadStage.LOST}


def test_not_interested_can_be_revived():
    """"Not interested" is usually "not this quarter" in this market — a
    factory that just bought a DG set is a live lead again later. If it
    were terminal, every revival would need an admin."""
    table = c.get_transitions_for_brand(ICONIQ)
    assert LeadStage.INTERESTED in table[LeadStage.NOT_INTERESTED]
    assert LeadStage.QUOTED in table[LeadStage.NOT_INTERESTED]


def test_terminal_stages_are_dead_ends():
    table = c.get_transitions_for_brand(ICONIQ)
    assert table[LeadStage.WON] == []
    assert table[LeadStage.LOST] == []


def test_every_transition_target_is_on_the_board():
    """A lead must never land in a stage the Kanban has no column for —
    the fault that stranded 1,575 FMC leads at the legacy 'lead' stage."""
    board = set(c.get_stages_for_pipeline("normal", ICONIQ))
    for src, targets in c.get_transitions_for_brand(ICONIQ).items():
        assert src in board
        assert set(targets) <= board


def test_leads_start_at_created():
    assert c.get_initial_stage_for_brand(ICONIQ) is LeadStage.CREATED
    assert LeadStage.CREATED in c.get_stages_for_pipeline("normal", ICONIQ)


# ── Lost reasons ───────────────────────────────────────────────────────

def test_lost_reasons_are_a_locked_list_not_free_text():
    """Locked, so the reports Amit deferred stay comparable across reps."""
    reasons = c.get_lost_reasons_for_brand(ICONIQ)
    assert reasons is not None
    assert "Went with DG instead" in reasons


def test_iconiq_does_not_inherit_fmc_lost_reasons():
    assert "Visa Reject" not in c.get_lost_reasons_for_brand(ICONIQ)


# ── Features Iconiq must NOT get ───────────────────────────────────────

def test_no_lender_features():
    """The gates used to ask "is this Admitverse?", which let a third
    brand straight through to the loan features."""
    assert c.brand_has_lender_features(ICONIQ) is False


def test_no_document_checklist():
    assert c.get_doc_checklist_for_brand(ICONIQ) == []
    assert c.get_doc_keys_for_brand(ICONIQ) == frozenset()


def test_no_universities():
    assert c.get_universities_for_brand(ICONIQ) == []


def test_industry_dropdown_is_iconiq_only():
    assert "Data Centre" in c.get_industries_for_brand(ICONIQ)
    assert c.get_industries_for_brand(FMC) == []
    assert c.get_industries_for_brand(AV) == []


def test_won_stage_for_reports():
    """Falling through to FMC's `disbursed` would make every Iconiq
    conversion figure read zero, since its board has no such stage."""
    from app.services.report_service import _BRAND_WON_STAGE
    assert _BRAND_WON_STAGE[ICONIQ] is LeadStage.WON


# ── The two live businesses must not move ──────────────────────────────

def test_fmc_board_unchanged():
    stages = [s.value for s in c.get_stages_for_pipeline("normal", FMC)]
    assert stages[0] == "created"
    assert "disbursed" in stages and "sanctioned" in stages
    assert c.get_terminal_stages_for_brand(FMC) == {LeadStage.DISBURSED, LeadStage.LOST}
    assert len(c.get_lost_reasons_for_brand(FMC)) == 21
    assert c.brand_has_lender_features(FMC) is True


def test_admitverse_board_unchanged():
    stages = [s.value for s in c.get_stages_for_pipeline("normal", AV)]
    assert "enrolled" in stages and "visa_applied" in stages
    assert c.get_terminal_stages_for_brand(AV) == {LeadStage.ENROLLED, LeadStage.LOST}
    assert c.get_lost_reasons_for_brand(AV) is None  # free text
    assert c.brand_has_lender_features(AV) is False


def test_unknown_slug_still_falls_back_to_fmc():
    """Unknown slugs have always landed on FMC. The new gate is a
    deny-list precisely so this stays true."""
    for slug in (None, "", "some-new-tenant"):
        assert c.get_stages_for_pipeline("normal", slug) == c.get_stages_for_pipeline("normal", FMC)
        assert c.brand_has_lender_features(slug) is True


def test_ai_board_untouched():
    assert [s.value for s in c.AI_PIPELINE_STAGES] == [
        "created", "contacted", "dnp", "qualified", "lost",
    ]


def test_new_enum_values_are_additive():
    """Existing values must all survive — a removed value is a failed
    read on every historical row that holds it."""
    for old in ("lead", "called", "connected", "qualified_lead", "won", "lost",
                "created", "contacted", "dnp", "sanctioned", "disbursed", "enrolled"):
        assert old in c.LEAD_STAGE_VALUES
    for new in ("not_interested", "interested", "quoted"):
        assert new in c.LEAD_STAGE_VALUES
