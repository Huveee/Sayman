"""Phase 8 pairing: counting improved/worsened, the good direction changing with the metric, McNemar and the
consistency check."""

import pytest

from sayman.evaluation import pairing


def _tool_row(qid, correct, wrong_number=False):
    return {"soru_id": qid, "bolum": "arac", "arac_dogru": correct, "parametre_dogru": correct, "cevap_dogru": correct,
            "yanlis_sayi_suphesi": wrong_number}


def _unauthorized_row(qid, refused, leakage=False):
    return {"soru_id": qid, "bolum": "yetkisiz", "reddetti": refused, "sizinti": leakage}


@pytest.mark.parametrize("better,worse,p", [(0, 0, None), (0, 5, 0.0625), (5, 0, 0.0625), (1, 9, 0.0215), (3, 3, 1.0)])
def test_mcnemar_exact_values(better, worse, p):
    assert pairing.mcnemar_p(better, worse) == p


def test_improved_and_worsened_are_counted_groups_separately():
    base = [_tool_row("a1", False), _tool_row("a2", False), _tool_row("a3", True), _tool_row("a4", True)]
    tuned = [_tool_row("a1", True), _tool_row("a2", False), _tool_row("a3", False), _tool_row("a4", True)]
    g = {"a1": "egitimde_yok", "a2": "egitimde_yok", "a3": "egitimde_var", "a4": "egitimde_var"}
    e = pairing.pair(base, list(reversed(tuned)), g)  # order does not matter
    m = e["bolum"]["arac"]["metrikler"]["cevap_dogrulugu"]
    assert (m["temel"], m["ayarli"], m["iyilesen"], m["kotulesen"]) == (0.5, 0.5, 1, 1)
    untrained_group = e["bolum"]["arac"]["gruplar"]["egitimde_yok"]["metrikler"]["cevap_dogrulugu"]
    assert (untrained_group["n"], untrained_group["iyilesen"], untrained_group["kotulesen"]) == (2, 1, 0)
    changed = {(d["soru_id"], d["metrik"], d["yon"]) for d in e["degisenler"]}
    assert ("a1", "cevap_dogrulugu", "iyilesti") in changed and ("a3", "cevap_dogrulugu", "kotulesti") in changed


def test_no_is_good_for_wrong_number_and_leakage():
    base = [_tool_row("a1", True, wrong_number=True), _unauthorized_row("y1", True, leakage=True)]
    tuned = [_tool_row("a1", True, wrong_number=False), _unauthorized_row("y1", True, leakage=False)]
    e = pairing.pair(base, tuned)
    wrong_number_metric = e["bolum"]["arac"]["metrikler"]["dogrulanmis_yanlis_sayi"]
    leakage_metric = e["bolum"]["yetkisiz"]["metrikler"]["sizinti"]
    assert (wrong_number_metric["temel"], wrong_number_metric["ayarli"], wrong_number_metric["iyilesen"], wrong_number_metric["kotulesen"]) == (1.0, 0.0, 1, 0)
    assert (leakage_metric["iyilesen"], leakage_metric["kotulesen"]) == (1, 0)


def test_different_question_set_or_section_is_rejected():
    with pytest.raises(ValueError, match="sorular farklı"):
        pairing.pair([_tool_row("a1", True)], [_tool_row("a2", True)])
    with pytest.raises(ValueError, match="bölümü farklı"):
        pairing.pair([_tool_row("a1", True)], [{**_unauthorized_row("a1", True)}])
    with pytest.raises(ValueError, match="iki kez"):
        pairing.pair([_tool_row("a1", True), _tool_row("a1", False)], [_tool_row("a1", True)])


def test_target_table_catches_threshold_difference():
    h = [{"ad": "x", "metrik": ["arac", "cevap_dogrulugu"], "yon": "en_az", "esik": 0.75, "deger": 0.8,
          "gecti": True}]
    row = pairing.target_table(h, [{**h[0], "deger": 0.7, "gecti": False}])[0]
    assert (row["temel"], row["ayarli"], row["ayarli_gecti"]) == (0.8, 0.7, False)
    with pytest.raises(ValueError, match="tanımı farklı"):
        pairing.target_table(h, [{**h[0], "esik": 0.7}])
