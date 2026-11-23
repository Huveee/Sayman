"""Phase 8b analysis: deriving the result of the old regime from the records of the same run (scripts/phase08b_analysis.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from phase08b_analysis import old_layout_record  # noqa: E402


def make_draft(text, old_valid=True, call_no=2, tool_call_count=1, truncated=False, ownerless=False, unidentified=(),
           scope_missing=None):
    return {"metin": text, "gecerli": old_valid and not ownerless and not unidentified, "eski_gecerli": old_valid,
            "kaynaksiz": [] if old_valid else ["1"], "gecersiz_alinti": [], "alinti_eksik": False,
            "kimliksiz": list(unidentified), "sahipsiz": ownerless, "kapsam_eksik": scope_missing, "cagri_no": call_no,
            "arac_cagrisi": tool_call_count, "kesildi": truncated}


def make_record(drafts, status="tamam", answer="son", call_count=2, durations=(5.0, 10.0, 20.0, 30.0)):
    return {"soru_id": "X", "durum": status, "cevap": answer, "sure_s": sum(durations) + 1.0,
            "cagrilar": [{"arac": f"a{i}", "argumanlar": {}, "hata": None, "yanit": {}} for i in range(call_count)],
            "taslaklar": drafts, "tokenlar": [{"istem": 1, "uretim": 1, "sure_s": s} for s in durations[:4]],
            "kapsam_notu": False}


def test_first_draft_valid_under_both_regimes_gives_same_result():
    k = make_record([make_draft("Cevap 1", call_no=2)], answer="Cevap 1", durations=(5.0, 10.0))
    old, divergence = old_layout_record(k)
    assert divergence is None and old["cevap"] == "Cevap 1" and old["sure_s"] == k["sure_s"]


def test_old_regime_stops_at_draft_the_new_rule_flagged():
    k = make_record([make_draft("Portföy toplamı 7 milyon", ownerless=True, call_no=2, tool_call_count=1),
               make_draft("Müşterinin 2 çeki var", call_no=4, tool_call_count=2)], answer="Müşterinin 2 çeki var")
    old, divergence = old_layout_record(k)
    assert divergence is None
    assert old["cevap"] == "Portföy toplamı 7 milyon" and old["durum"] == "tamam"
    assert len(old["cagrilar"]) == 1 and len(old["taslaklar"]) == 1
    assert old["sure_s"] == k["sure_s"] - 20.0 - 30.0  # the 3rd and 4th model calls do not exist in the old regime


def test_scope_missing_draft_is_shown_without_note_under_old_regime():
    k = make_record([make_draft("Toplam 51 milyon", scope_missing="yalnızca yurtiçi müşteriler")], answer="Toplam 51 milyon\n\nNot")
    k["kapsam_notu"] = True
    old, _ = old_layout_record(k)
    assert old["cevap"] == "Toplam 51 milyon" and old["kapsam_notu"] is False


def test_diverges_if_new_rule_also_flagged_earlier():
    # the old rules also rejected draft 1, but the new rule added a line to the correction message: the rest is not comparable
    k = make_record([make_draft("Yanlış 1", old_valid=False, ownerless=True), make_draft("Cevap", call_no=3)], answer="Cevap")
    old, divergence = old_layout_record(k)
    assert old is None and divergence.startswith("1. taslak")


def test_draft_rejected_by_old_rules_but_untouched_by_new_rule_takes_same_path():
    k = make_record([make_draft("Yanlış 1", old_valid=False), make_draft("Cevap", call_no=3, tool_call_count=2)], answer="Cevap")
    old, divergence = old_layout_record(k)
    assert divergence is None and old["cevap"] == "Cevap" and old["sure_s"] == k["sure_s"] - 30.0


def test_old_regime_does_not_stop_at_empty_or_truncated_draft():
    k = make_record([make_draft("", call_no=2), make_draft("yarım", truncated=True, call_no=3), make_draft("Cevap", call_no=4)],
              answer="Cevap")
    old, divergence = old_layout_record(k)
    assert divergence is None and old["cevap"] == "Cevap"


def test_new_result_is_unchanged_when_nothing_was_accepted():
    k = make_record([make_draft("Yanlış", old_valid=False), make_draft("Yanlış 2", old_valid=False, call_no=3)],
              status="uretemedim", answer="üretemedim metni")
    old, divergence = old_layout_record(k)
    assert divergence is None and old is k
