"""Phase 8b: the verifier's ownership, identifier and scope rules; the orchestrator's scope correction and note."""

import datetime as dt
import json
import re

from test_phase06_orchestrator import DOMESTIC, FakeModel, tool_call, run_turn, reply, last_tool_result, server, tr  # noqa: F401

from sayman.assistant.verifier import SCOPE_NOTES, verify, identifiers, is_customer_question
from sayman.assistant.orchestrator import FIXED_TEXTS, correction_message
from sayman.evaluation.scoring import REFUSAL_WORDS

TODAY = dt.date(2026, 9, 1)
DOMESTIC_SCOPE = "yalnızca yurtiçi müşteriler"


def make_call(tool_name, arguments, result, scope="tüm şirket", cid="C1"):
    return {"arac": tool_name, "argumanlar": arguments, "hata_mi": False,
            "yanit": {"cagri_id": cid, "arac": tool_name, "tarih": "2026-09-01", "kapsam": scope, "sonuc": result}}


CHEQUE_PORTFOLIO_CALL = make_call("cek_portfoyu", {}, {
    "durum": "bekleyen", "cek_sayisi": 295, "toplam_tl": "7674405.79",
    "cekler": [{"cek_id": "C025774", "musteri": "Tan Orkide Mağazacılık A.Ş.", "vade_tarihi": "2026-09-02",
                "tutar_tl": "9521.42"}]}, scope=DOMESTIC_SCOPE)
SUMMARY_CALL = make_call("musteri_ozeti", {"musteri_adi": "Zümrüt Ufuk Tasarım A.Ş."}, {
    "musteri": "Zümrüt Ufuk Tasarım A.Ş.", "bekleyen_cek_sayisi": 2, "bekleyen_cek_tutari": "55275.27"},
    scope=DOMESTIC_SCOPE, cid="C2")
CHEQUE_QUESTION = "Zümrüt Ufuk Tasarım A.Ş. müşterisinden elimizde bekleyen kaç çek var?"


# ---------------------------------------------------------------- is it a customer question


def test_customer_question_only_when_a_name_is_present():
    assert is_customer_question("Bright Home Group GmbH müşterisinin bekleyen çekleri var mı?")
    assert is_customer_question("Zümrüt Ufuk müşterisinden en son ne zaman tahsilat yaptık?")  # short name
    assert is_customer_question("Gama AŞ için toplam açık alacağımız ne kadar?")  # company type
    assert is_customer_question("Toros Başak Gıda A.Ş. son 12 ayda ortalama kaç gün gecikmeyle ödedi?")
    # the word "müşterisi" appears without a name: not a customer question
    assert not is_customer_question("F016076 faturasının müşterisi kim, vadesi ne?")
    assert not is_customer_question("Vadesini en az 30 gün geçmiş faturalar içinde en büyüğü hangisi, müşterisi kim?")
    assert not is_customer_question("Toplam açık alacağımız ne kadar?")


# ---------------------------------------------------------------- ownership


def test_portfolio_total_cannot_be_attributed_to_customer():
    d = verify("Zümrüt Ufuk Tasarım A.Ş. müşterisinden 295 çek bekliyor; toplamı 7.674.405,79 TL.",
                CHEQUE_QUESTION, [CHEQUE_PORTFOLIO_CALL], TODAY)
    assert d.legacy_valid and d.ownerless and not d.gecerli  # the old rules would have passed it
    assert "musteri_ozeti" in correction_message(reply("x"), d)


def test_passes_with_customer_summary():
    d = verify("2 çek bekliyor; toplamı 55.275,27 TL.", CHEQUE_QUESTION, [CHEQUE_PORTFOLIO_CALL, SUMMARY_CALL], TODAY)
    assert d.gecerli and not d.ownerless


def test_customer_asked_by_short_name_and_found_by_full_name():
    d = verify("2 çek bekliyor; toplamı 55.275,27 TL.", "Zümrüt Ufuk müşterisinden kaç çek var?", [SUMMARY_CALL], TODAY)
    assert not d.ownerless


def test_call_made_for_other_customer_does_not_count_as_owned():
    other_customer_call = make_call("musteri_ozeti", {"musteri_adi": "Nar Zirve Ticaret Ltd. Şti."}, {
        "musteri": "Nar Zirve Ticaret Ltd. Şti.", "bekleyen_cek_sayisi": 3, "bekleyen_cek_tutari": "1200.00"})
    d = verify("Zümrüt Ufuk Tasarım A.Ş. müşterisinden 3 çek bekliyor; toplamı 1.200,00 TL.", CHEQUE_QUESTION, [other_customer_call],
                TODAY)
    assert d.ownerless


def test_other_customers_invoice_identifier_is_also_ownerless():
    invoice_call = make_call("fatura_ara", {"musteri_adi": "Nar Zirve Ticaret Ltd. Şti."}, {
        "faturalar": [{"fatura_id": "F035729", "kaynak_fatura_no": "564428", "musteri": "Nar Zirve Ticaret Ltd. Şti."}]})
    d = verify("En son fatura F035729.", "Zümrüt Ufuk Tasarım A.Ş. müşterisine kestiğimiz son fatura hangisi?",
                [invoice_call], TODAY)
    assert d.ownerless


def test_answer_without_digits_and_document_answer_pass_ownership():
    assert not verify("Bu müşteriyi bulamadım.", CHEQUE_QUESTION, [CHEQUE_PORTFOLIO_CALL], TODAY).ownerless
    document_call = make_call("belge_ara", {"sorgu": "Gama AŞ vade"}, {"sonuclar": [
        {"parca_id": "SOZ-S001#m3", "metin": "Ödeme fatura tarihinden itibaren 30 gün içinde yapılır.",
         "belge": "Gama AŞ satış sözleşmesi", "madde": "Ödeme"}]})
    d = verify('Ödeme "fatura tarihinden itibaren 30 gün içinde" [SOZ-S001#m3] yapılır.',
                "Gama AŞ sözleşmesine göre ödeme vadesi kaç gün?", [document_call], TODAY)
    assert d.gecerli and not d.ownerless


def test_out_of_scope_and_nonexistent_customer_get_same_decision():
    """Phase 3 guarantee: the verifier does not look at the database; a customer of the other region and a customer that does
    not exist get the same decision and the same correction text (apart from the names)."""
    results = []
    for name in ("Bright Home Group GmbH", "Hayali Örnek Ticaret GmbH"):
        d = verify(f"{name} müşterisinin bekleyen çekleri var; toplamı 7.674.405,79 TL.",
                    f"{name} müşterisinin bekleyen çekleri var mı, toplamı ne?", [CHEQUE_PORTFOLIO_CALL], TODAY)
        results.append((d.gecerli, d.ownerless, d.unknown_identifiers, d.missing_scope,
                         correction_message(reply("x"), d).replace(name, "<ad>")))
    assert results[0] == results[1] and results[0][1]


# ---------------------------------------------------------------- identifier


def test_fabricated_identifier_is_caught():
    invoice_call = make_call("fatura_ara", {"musteri_adi": "Gelincik Hilal Gıda San. ve Tic. Ltd. Şti."}, {"faturalar": [
        {"fatura_id": "F033963", "kaynak_fatura_no": "C560866"}, {"fatura_id": "F033927", "kaynak_fatura_no": "560878"}]})
    question = "Gelincik Hilal Gıda San. ve Tic. Ltd. Şti. müşterisine kestiğimiz son fatura hangisi?"
    d = verify("En son fatura numarası C560878.", question, [invoice_call], TODAY)
    assert d.legacy_valid and d.unknown_identifiers == ["C560878"] and not d.gecerli
    assert "C560878" in correction_message(reply("x"), d)
    assert verify("En son fatura C560866 (F033963).", question, [invoice_call], TODAY).gecerli


def test_chunk_id_and_law_number_are_not_identifiers():
    assert identifiers('"otuz gün içinde" [SOZ-S001#m3]', [(16, 29)]) == []
    assert identifiers("KANUN-6102 ve SOZ-S001 belgesi") == []
    assert identifiers("F016076 faturası ve C560878") == ["F016076", "C560878"]


def test_identifier_in_question_is_known():
    assert verify("F016076 faturasını bulamadım.", "F016076 faturasının vadesi ne?", [], TODAY).gecerli


# ---------------------------------------------------------------- scope


def test_scope_must_be_stated_for_sales_totals():
    question = "Bekleyen çeklerimizin toplamı ne?"
    assert verify("Bekleyen 295 çekin toplamı 7.674.405,79 TL.", question, [CHEQUE_PORTFOLIO_CALL], TODAY).missing_scope == \
        DOMESTIC_SCOPE
    for text in ("Yurtiçi müşterilerinizden bekleyen 295 çekin toplamı 7.674.405,79 TL.",
                  "Yurt içi müşterilerin bekleyen 295 çekinin toplamı 7.674.405,79 TL."):
        assert verify(text, question, [CHEQUE_PORTFOLIO_CALL], TODAY).missing_scope is None
    d = verify("Bekleyen 295 çekin toplamı 7.674.405,79 TL.", question, [CHEQUE_PORTFOLIO_CALL], TODAY)
    assert d.gecerli  # scope does not reject the answer


def test_cases_that_need_no_scope():
    cfo = make_call("cek_portfoyu", {}, {"cek_sayisi": 295, "toplam_tl": "7674405.79"})
    assert verify("Toplam 7.674.405,79 TL.", "Çeklerimiz?", [cfo], TODAY).missing_scope is None  # whole company
    assert verify("2 çek; toplamı 55.275,27 TL.", CHEQUE_QUESTION, [SUMMARY_CALL], TODAY).missing_scope is None  # a single customer
    rate_call = make_call("doviz_kuru", {"para_birimi": "USD"}, {"alis": "41.2345"}, scope=DOMESTIC_SCOPE)
    assert verify("USD alış kuru 41,2345.", "USD kuru?", [rate_call], TODAY).missing_scope is None  # not customer data
    invoice_call = make_call("fatura_ara", {"fatura_no": "F016076"}, {"faturalar": [{"fatura_id": "F016076",
                                                                           "tutar_tl": "1200.00"}]},
                   scope=DOMESTIC_SCOPE)
    assert verify("F016076 tutarı 1.200,00 TL.", "F016076 ne kadar?", [invoice_call], TODAY).missing_scope is None


def test_known_limit_answer_echoing_question_counts_as_scope_statement():
    """Known limit (README Phase 8b): the scope check only looks at whether the answer contains a scope statement. The sentence
    "yurtiçi ve yurt dışı birlikte" that echoes the question also contains "yurtiçi", so the scope counts as stated; the
    domestic manager's own total is shown without a note as if it were the company's total. That is why whole-company
    questions are scored by hand."""
    receivable_call = make_call("alacak_yaslandirma", {}, {"toplam_tl": "51719292.77"}, scope=DOMESTIC_SCOPE)
    question = "Yurtiçi ve yurt dışı birlikte bütün müşterilerimizden ne kadar alacağımız var?"
    d = verify("Yurtiçi ve yurt dışı birlikte tüm müşterilerinizden 51.719.292,77 TL alacağınız var.", question,
                [receivable_call], TODAY)
    assert d.gecerli and d.missing_scope is None


def test_scope_note_contains_no_refusal_word():
    for text in SCOPE_NOTES.values():
        assert not any(k in text.lower() for k in REFUSAL_WORDS)


# ---------------------------------------------------------------- orchestrator: scope correction and note


def _without_scope(messages):
    return reply(f"Açık alacak toplamı {tr(last_tool_result(messages)['sonuc']['toplam_tl'])} TL.")


def test_scope_correction_holds_gives_corrected_answer(server):  # noqa: F811
    def with_scope(messages):
        tool_message = next(m for m in messages if m["role"] == "tool")
        return reply(f"Yurtiçi müşterilerinizin açık alacak toplamı "
                     f"{tr(json.loads(tool_message['content'])['sonuc']['toplam_tl'])} TL.")

    model = FakeModel(tool_call("alacak_yaslandirma"), _without_scope, with_scope)
    turn, _, _ = run_turn(server, model, user=DOMESTIC, question="Toplam açık alacağımız ne kadar?")
    assert turn.status == "tamam" and turn.answer.startswith("Yurtiçi") and not turn.scope_note
    assert "yalnızca yurtiçi müşteriler kapsıyor" in model.calls[2][-1]["content"]


def test_scope_correction_breaks_gives_first_draft_with_note(server):  # noqa: F811
    model = FakeModel(tool_call("alacak_yaslandirma"), _without_scope, reply("Yurtiçi müşterilerinizden 99.999,99 TL."))
    turn, _, _ = run_turn(server, model, user=DOMESTIC, question="Toplam açık alacağımız ne kadar?")
    first = turn.drafts[0].strip()
    assert (turn.status, turn.scope_note) == ("tamam", True)
    assert turn.answer == first + "\n\n" + SCOPE_NOTES[DOMESTIC_SCOPE] and "99.999" not in turn.answer


def test_scope_still_unstated_gives_last_draft_with_note(server):  # noqa: F811
    model = FakeModel(tool_call("alacak_yaslandirma"), _without_scope, _without_scope)
    turn, _, _ = run_turn(server, model, user=DOMESTIC, question="Toplam açık alacağımız ne kadar?")
    assert (turn.status, turn.scope_note, turn.model_calls) == ("tamam", True, 3)
    assert turn.answer.endswith(SCOPE_NOTES[DOMESTIC_SCOPE])


def test_no_scope_correction_for_cfo(server):  # noqa: F811
    model = FakeModel(tool_call("alacak_yaslandirma"), _without_scope)
    turn, _, _ = run_turn(server, model, question="Toplam açık alacağımız ne kadar?")
    assert (turn.status, turn.scope_note, turn.model_calls) == ("tamam", False, 2)


def test_ownerless_answer_is_corrected_or_cannot_answer(server):  # noqa: F811
    """The customer of the other region (Beta GmbH, abroad) and a customer that does not exist: same flow, same messages."""
    flows = []
    for name in ("Beta GmbH", "Omega GmbH"):
        def write_total(messages, name=name):
            return reply(f"{name} müşterisinin çekleri toplamı {tr(last_tool_result(messages)['sonuc']['toplam_tl'])} TL.")

        model = FakeModel(tool_call("cek_portfoyu"), write_total, tool_call("musteri_ozeti", musteri_adi=name),
                           reply(f"{name} adında bir müşteri bulamadım."))
        turn, _, _ = run_turn(server, model, user=DOMESTIC, question=f"{name} müşterisinin bekleyen çekleri var mı?")
        assert turn.status == "tamam" and turn.answer == f"{name} adında bir müşteri bulamadım."
        assert turn.verifications[0].ownerless
        messages = [{k: v for k, v in m.items() if k != "content"} | {"content": str(m.get("content", "")).replace(
            name, "<ad>")} for m in model.calls[-1][1:]]
        flows.append([m for m in messages if m["role"] in ("user", "tool")])
    clean = [[re.sub(r'"cagri_id": "C\w+"', "", m["content"]) for m in a] for a in flows]
    assert clean[0] == clean[1]

    model = FakeModel(tool_call("cek_portfoyu"), lambda m: reply(
        f"Beta GmbH çekleri {tr(last_tool_result(m)['sonuc']['toplam_tl'])} TL."), lambda m: reply(
        f"Beta GmbH çekleri {tr(last_tool_result(m)['sonuc']['toplam_tl'])} TL."))
    turn, _, _ = run_turn(server, model, user=DOMESTIC, question="Beta GmbH müşterisinin çekleri ne kadar?")
    assert (turn.status, turn.answer) == ("uretemedim", FIXED_TEXTS["uretemedim"])
