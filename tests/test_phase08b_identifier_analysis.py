"""Phase 8b identifier measurement: extracting candidate numbers, labels, "which rule stopped it" and the
fabricated-identifier check."""

import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import support.phase07_questions as f7  # noqa: E402
from phase08b_identifier_analysis import _draft_reading, candidates, spelling_class, label_mentions  # noqa: E402

from sayman.assistant.verifier import verify  # noqa: E402
from sayman.evaluation.oracle import fabricated_identifier  # noqa: E402

DB = {"F000001": ("F", "F000001"), "F000002": ("F", "F000002"), "F000003": ("F", "F000003"),
      "M00001": ("M", "M00001"), "489434": ("K", "489434"), "H0000010": ("H", "H0000010")}
QUESTION = {"soru_id": "X", "soru": "M00001 müşterisinin en son faturası hangisi?", "dogru_kimlikler": ["F000001"]}


def make_call(result, error=None):
    return {"arac": "fatura_ara", "argumanlar": {}, "hata": error, "yanit": {"sonuc": result}}


CALLS = [make_call({"faturalar": [{"fatura_id": "F000001", "kaynak_fatura_no": "489434", "musteri": "M00001"},
                              {"fatura_id": "F000002"}]})]


def test_candidates_find_numbers_but_not_amounts_or_dates():
    text = ("Çekler: CK-2026-0891, F 035473, #F035474, F-035475'in yanında T00039E4A ve 489434. "
             "Tutar 250000 TL, 1.234.567,89 TL; tarih 2026-08-12; TL 500.")
    assert sorted(candidates(text)) == sorted(["T00039E4A", "CK-2026-0891", "#F035474", "F-035475", "F 035473", "489434"])


def test_form_classes():
    assert [spelling_class(x) for x in ("F035473", "F-2026-089", "F 035473", "#F035473", "TA3B7C1D9", "489434",
                               "1, 2, 3")] == ["sistem", "ayracli", "ayracli", "ayracli", "karisik", "rakam", "rakam"]


def test_labels_by_tool_result_and_database():
    def labels_for(text, calls=CALLS):
        return [(a["ham"], a["etiket"]) for a in label_mentions(text, QUESTION, calls, DB)["anmalar"]]

    assert labels_for("Son fatura F000001.") == [("F000001", "dogru")]
    assert labels_for("Son fatura F000002.") == [("F000002", "yanlis_kayit")]          # present in the result, but not the correct one
    assert labels_for("Son fatura F000003.") == [("F000003", "desteksiz_gercek")]      # present in the database, absent from the result
    assert labels_for("Son fatura F099999.") == [("F099999", "uydurma")]
    assert labels_for("Son fatura F-000001.") == [("F-000001", "dogru")]               # a separator difference, same number
    assert labels_for("M00001 müşterisinin faturası F000001.") == [("M00001", "sorudaki"), ("F000001", "dogru")]
    assert labels_for("Son fatura F000001.", []) == [("F000001", "desteksiz_dogru")]   # correct, but absent from the tool result
    assert labels_for("Son fatura F000001.", [make_call({"faturalar": [{"fatura_id": "F000001"}]}, error="yetki_yok")]) == \
        [("F000001", "desteksiz_dogru")]                                         # a failed call does not count as support


def test_text_label_is_the_worst_mention():
    assert label_mentions("F000001 ve F099999", QUESTION, CALLS, DB)["etiket"] == "uydurma"
    assert label_mentions("F000001 ve F000002", QUESTION, CALLS, DB)["etiket"] == "yanlis_kayit"
    assert label_mentions("Bulamadım.", QUESTION, CALLS, DB)["etiket"] == "numarasiz"


def test_manual_file_adds_and_removes_candidates():
    e = label_mentions("Numara: KAYIT 77 ve F000002", QUESTION, CALLS, DB, {"ekle": ["KAYIT 77"], "cikar": ["F000002"]})
    assert [(a["ham"], a["etiket"]) for a in e["anmalar"]] == [("KAYIT 77", "uydurma")]


def make_draft(text, old_valid=True, unidentified=(), ownerless=False):
    return {"metin": text, "eski_gecerli": old_valid, "gecerli": old_valid and not unidentified and not ownerless,
            "kimliksiz": list(unidentified), "kaynaksiz": [] if old_valid else ["1"], "sahipsiz": ownerless,
            "arac_cagrisi": 1, "kesildi": False}


def test_which_rule_stopped():
    k = {"cagrilar": CALLS}

    def d(t):
        return _draft_reading(t, QUESTION, k, DB, None)["durduran"]

    assert d(make_draft("F099999", unidentified=["F099999"])) == "yalniz_yeni_kural"
    assert d(make_draft("F-2026-099", old_valid=False)) == "eski_kurallar"
    assert d(make_draft("F099999", old_valid=False, unidentified=["F099999"])) == "ikisi"
    assert d(make_draft("TA3B7C1D9")) == "durdurulmadi"
    assert d(make_draft("F000002")) is None              # a wrong record is not unsupported: not the rule's business
    assert d(make_draft("F000001", unidentified=["F000001"])) is None  # wrong flag: listed separately in the analysis


def test_fabricated_identifier_check_forms_and_todays_rule():
    forms = {}
    for i in range(60):
        b, number = fabricated_identifier(f"K-X-{i:02d}")
        forms.setdefault(b, number)
    assert set(forms) == {"sistem", "tireli", "karisik"}
    assert [spelling_class(forms[b]) for b in ("sistem", "tireli", "karisik")] == ["sistem", "ayracli", "karisik"]
    calls = [{"arac": "fatura_ara", "argumanlar": {}, "hata_mi": False,
                 "yanit": {"cagri_id": "C1", "arac": "fatura_ara", "tarih": "2026-09-01", "kapsam": "tüm şirket",
                           "sonuc": {"faturalar": [{"fatura_id": "F035473"}]}}}]
    result = {b: verify(f"Kayıt numarası: {n}.", "Numarası ne?", calls, dt.date(2026, 9, 1))
             for b, n in forms.items()}
    assert result["sistem"].unknown_identifiers and not result["sistem"].unsourced
    assert not result["karisik"].unknown_identifiers and not result["karisik"].unsourced  # known blind spot


def test_frozen_identifier_set_is_intact():
    data = json.loads((ROOT / "reports" / "faz8b_kimlik_soru_seti.json").read_text(encoding="utf-8"))
    questions = data["sorular"]
    assert data["ozet"] == f7.digest(questions)
    assert data["sayilar"]["grup"] == {"D": 10, "U": 20, "Y": 6}
    for s in questions:
        assert s["set"] == "kimlik" and s["dogru_kimlikler"]
        if s["grup"] == "D":
            a = s["alternatifler"][0]["anahtarlar"][0]
            assert s["bolum"] == "arac" and a["tur"] == "kimlik" and [a["deger"]] == s["dogru_kimlikler"]
        else:
            assert s["bolum"] == "yetkisiz" and s["tur"] == "kimlik_yok" and s["gizli"] == []
    assert data["denetimler"]["sizinti"]["temiz"] and not data["denetimler"]["ortusme"]["ihlal"]
