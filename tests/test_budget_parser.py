"""Pure unit tests for the Admitverse budget parser + brand-dispatch
helpers. No DB — these run anywhere (unlike the DB-backed suite which
needs the live Supabase test DB).
"""
from decimal import Decimal

from app.utils.budget_parser import parse_budget
from app.core.constants import (
    get_doc_checklist_for_brand, get_doc_keys_for_brand,
    get_universities_for_brand, get_lost_reasons_for_brand,
    APPLICATION_STATUS_VALUES, APPLICATION_STATUS_PRIORITY,
    APPLICATION_OFFER_STATUSES, FMC_DOC_KEYS, AV_DOC_KEYS,
)


class TestBudgetParser:
    def test_foreign_currencies(self):
        assert parse_budget("£18,000") == (Decimal("18000.00"), "GBP")
        assert parse_budget("$30,000") == (Decimal("30000.00"), "USD")
        assert parse_budget("12000 GBP") == (Decimal("12000.00"), "GBP")
        assert parse_budget("€25,000") == (Decimal("25000.00"), "EUR")

    def test_inr_lakh_crore(self):
        assert parse_budget("50 lakh") == (Decimal("5000000.00"), "INR")
        assert parse_budget("2 cr") == (Decimal("20000000.00"), "INR")
        assert parse_budget("₹25,00,000") == (Decimal("2500000.00"), "INR")

    def test_inr_bare_number_assumed_lakhs(self):
        # bare small number → lakhs (study-abroad budgets are quoted in lakhs)
        assert parse_budget("25") == (Decimal("2500000.00"), "INR")
        # large bare number → already rupees
        assert parse_budget("1800000") == (Decimal("1800000.00"), "INR")

    def test_foreign_with_magnitude(self):
        assert parse_budget("30k USD") == (Decimal("30000.00"), "USD")

    def test_null_and_junk(self):
        for junk in ("", None, "n/a", "tbd", "flexible", "depends"):
            assert parse_budget(junk) == (None, None)


class TestBrandDispatch:
    def test_doc_checklist_per_brand(self):
        av = get_doc_checklist_for_brand("admitverse")
        fmc = get_doc_checklist_for_brand("fmc")
        assert {d["key"] for d in av} == set(AV_DOC_KEYS)
        assert "passport" in {d["key"] for d in av}
        assert "aadhaar" in {d["key"] for d in fmc}
        # unknown / missing slug falls back to FMC
        assert get_doc_checklist_for_brand(None) == fmc

    def test_doc_keys_per_brand(self):
        assert get_doc_keys_for_brand("admitverse") == AV_DOC_KEYS
        assert get_doc_keys_for_brand("fundmycampus") == FMC_DOC_KEYS

    def test_universities_only_for_av(self):
        assert get_universities_for_brand("admitverse")  # non-empty list
        assert get_universities_for_brand("fmc") == []

    def test_lost_reasons_av_free_text(self):
        # AV → None (free text); FMC → locked tuple
        assert get_lost_reasons_for_brand("admitverse") is None
        assert get_lost_reasons_for_brand("fmc") is not None

    def test_application_status_invariants(self):
        # every status has a priority; offer statuses are a subset of all
        assert set(APPLICATION_OFFER_STATUSES).issubset(set(APPLICATION_STATUS_VALUES))
        assert set(APPLICATION_STATUS_PRIORITY).issubset(set(APPLICATION_STATUS_VALUES))
        # enrolled is the most-advanced; rejected/withdrawn never win primary
        assert APPLICATION_STATUS_PRIORITY["enrolled"] == max(APPLICATION_STATUS_PRIORITY.values())
        assert APPLICATION_STATUS_PRIORITY["rejected"] == 0
        assert APPLICATION_STATUS_PRIORITY["withdrawn"] == 0
