"""Phase 0 acceptance test: checks the recorded measurement report (reports/faz0_hiz.json).

It does not call Ollama; it only reads the file written by the measurement script. If the report
is missing the phase is not closed and the test fails.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sayman.model_selection.call_validation import (
    REQUIRED_VALID,
    DURATION_THRESHOLD_S,
    is_expected_call,
    is_response_valid,
    meets_rule,
    selection_rule,
)
from sayman.model_selection.sample_questions import SAMPLE_QUESTIONS

REPORT = Path(__file__).resolve().parents[1] / "reports" / "faz0_hiz.json"


@pytest.fixture(scope="module")
def report() -> dict:
    assert REPORT.exists(), (
        "Ölçüm raporu yok. Önce şunu çalıştırın: .venv\\Scripts\\python scripts\\phase00_speed_benchmark.py"
    )
    return json.loads(REPORT.read_text(encoding="utf-8"))


def test_at_least_two_candidates_were_measured(report):
    assert len(report["adaylar"]) >= 2


def test_rule_values_match_the_phase0_criterion(report):
    assert report["kural"]["sure_esigi_s"] == DURATION_THRESHOLD_S == 30.0
    assert report["kural"]["gereken_gecerli"] == REQUIRED_VALID == 5


def test_speeds_were_recorded_for_every_candidate(report):
    for a in report["adaylar"]:
        assert a["istem_isleme_tps"] and a["istem_isleme_tps"] > 0, a["model"]
        assert a["uretim_tps"] and a["uretim_tps"] > 0, a["model"]
        assert a["soguk_tur_s"] > 0, a["model"]
        assert a["ozet"], a["model"]


def test_every_candidate_was_measured_with_five_questions(report):
    expected = [s["kimlik"] for s in SAMPLE_QUESTIONS]
    for a in report["adaylar"]:
        assert [t["kimlik"] for t in a["turlar"]] == expected, a["model"]
        assert all(t["sure_s"] > 0 for t in a["turlar"]), a["model"]


def test_measurement_conditions_are_valid(report):
    """Thinking off, the prompt fit the context, the cache warmed up, memory did not run out."""
    for a in report["adaylar"]:
        assert a["dusunme_kapali"], a["model"]
        assert a["baglam_yetti"], a["model"]
        assert a["soguk_istem_token"] < report["ayarlar"]["num_ctx"], a["model"]
        assert a["isinma_dogrulandi"], a["model"]
        assert a["en_dusuk_bos_bellek_gb"] >= 0.5, a["model"]


def test_recorded_validity_is_unchanged_when_recomputed(report):
    for a in report["adaylar"]:
        for t in a["turlar"]:
            assert is_response_valid(t["cagrilar"])[0] == t["gecerli"], (a["model"], t["kimlik"])
            question = next(s for s in SAMPLE_QUESTIONS if s["kimlik"] == t["kimlik"])
            correct = is_expected_call(t["cagrilar"], question["beklenen_arac"], question["beklenen_argumanlar"])
            assert correct == t["dogru_cagri"], (a["model"], t["kimlik"])
        assert a["gecerli_sayisi"] == sum(t["gecerli"] for t in a["turlar"])
        assert a["kural_saglandi"] == meets_rule(a)


def test_selected_model_satisfies_the_rule(report):
    assert report["secilen_model"] == selection_rule(report["adaylar"])


def test_a_demo_model_was_selected(report):
    assert report["secilen_model"] is not None, "Hiçbir aday kuralı sağlamadı; Faz 0 kapanamaz."
