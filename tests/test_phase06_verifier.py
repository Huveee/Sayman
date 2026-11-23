"""Phase 6: tests of the answer verifier.

The draft set (tests/support/phase06_drafts.py) was written before the verifier; these tests check whether the verifier
passes that set as it is. All correct drafts must pass, exactly the expected numbers must be caught in the wrong drafts,
and the known blind spot is shown explicitly in a separate test.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from support.phase06_drafts import INVALID_CITATION_DRAFTS, PASSING_DRAFTS, BLIND_SPOT, FAILING_DRAFTS, tool_results
from sayman.assistant.verifier import verify, collect_sources, numbers_from_text

DATA = tool_results()
TODAY = dt.date.fromisoformat(DATA["bugun"])


def _run_verifier(question, call_keys, draft):
    return verify(draft, question, [DATA["cagrilar"][a] for a in call_keys], TODAY)


# ---------------------------------------------------------------- draft set


@pytest.mark.parametrize("name,question,call_keys,draft", PASSING_DRAFTS, ids=[t[0] for t in PASSING_DRAFTS])
def test_correct_draft_passes(name, question, call_keys, draft):
    result = _run_verifier(question, call_keys, draft)
    assert [s.metin for s in result.unsourced] == []
    assert result.invalid_citations == []
    assert result.gecerli


@pytest.mark.parametrize("name,question,call_keys,draft,expected", FAILING_DRAFTS, ids=[t[0] for t in FAILING_DRAFTS])
def test_wrong_draft_is_caught(name, question, call_keys, draft, expected):
    result = _run_verifier(question, call_keys, draft)
    assert [s.metin for s in result.unsourced] == expected
    assert bool(result.invalid_citations) == (name in INVALID_CITATION_DRAFTS)
    assert not result.gecerli


def test_blind_spot_right_number_wrong_customer_passes():
    """Known limit: the verifier does not look at what a number belongs to. When the open receivable of Green Home Store Ltd
    is written for Prime Design House Ltd, the answer passes because the number appears in a result of this turn. The test
    is here not to hide the limit but to make it visible: if it is fixed, this test must be changed deliberately."""
    question, call_keys, draft, (source_call, source_field) = BLIND_SPOT
    result = _run_verifier(question, call_keys, draft)
    assert result.gecerli
    [number] = result.numbers
    call_id = DATA["cagrilar"][source_call]["yanit"]["cagri_id"]
    assert (call_id, source_field) in number.sources
    correct_customer = DATA["cagrilar"]["musteri_b"]["yanit"]["cagri_id"]
    assert all(c != correct_customer for c, _ in number.sources)


def test_every_number_shows_its_source():
    _, question, call_keys, draft = next(t for t in PASSING_DRAFTS if t[0] == "nakit_tam")
    result = _run_verifier(question, call_keys, draft)
    cid = DATA["cagrilar"]["nakit"]["yanit"]["cagri_id"]
    assert [(s.metin, s.sources) for s in result.numbers] == [
        ("75.826.754,65", ((cid, "toplam_tl"),)), ("74.205.083,37", ((cid, "bakiye"), (cid, "bakiye_tl")))]


# ---------------------------------------------------------------- reading numbers from text


def _read_numbers(text):
    return [(s.metin, s.tur, s.deger) for s in numbers_from_text(text)]


@pytest.mark.parametrize("text,expected", [
    ("75.826.754,65 TL", ("75.826.754,65", "tutar", Decimal("75826754.65"))),
    ("50,93 gün", ("50,93", "gun", Decimal("50.93"))),
    ("55.865", ("55.865", "bilinmeyen", Decimal("55865"))),
    ("48.1745 TL", ("48.1745", "tutar", Decimal("48.1745"))),
    ("2,5 milyon TL", ("2,5 milyon", "tutar", Decimal("2500000"))),
    ("657 bin TL'si", ("657 bin", "tutar", Decimal("657000"))),
    ("%12", ("%12", "yuzde", Decimal("12"))),
    ("yüzde 12", ("12", "yuzde", Decimal("12"))),
    ("%2,25 faiz", ("%2,25", "yuzde", Decimal("2.25"))),
    ("12 ay", ("12", "ay", Decimal("12"))),
    ("son 12 ayda", ("12", "ay", Decimal("12"))),
    ("840 fatura", ("840", "adet", Decimal("840"))),
    ("3'ü geç kapandı", ("3", "bilinmeyen", Decimal("3"))),
    ("4 haftalık", ("4", "hafta", Decimal("4"))),
    ("$1.200", ("1.200", "tutar", Decimal("1200"))),
    ("2025'te", ("2025", "yil", Decimal("2025"))),
])
def test_number_reading(text, expected):
    assert _read_numbers(text) == [expected]


@pytest.mark.parametrize("text,expected", [
    ("2026-09-01", dt.date(2026, 9, 1)),
    ("1.9.2026", dt.date(2026, 9, 1)),
    ("19 Eylül 2025", dt.date(2025, 9, 19)),
    ("19 eylül 2025'te", dt.date(2025, 9, 19)),
])
def test_date_reading(text, expected):
    [s] = numbers_from_text(text)
    assert (s.tur, s.tarih) == ("tarih", expected)


def test_date_without_year():
    [s] = numbers_from_text("19 Eylül'de doldu")
    assert (s.tur, s.tarih, s.day_month) == ("tarih", None, (19, 9))


@pytest.mark.parametrize("text", [
    "1. Green Home Store Ltd",       # numbered list marker
    "2) Prime Design House Ltd",
    "F032047 numaralı fatura",       # invoice number
    "[SOZ-S001#m3]",                 # chunk id
    "1 USD = ",                       # exchange-rate unit
])
def test_things_not_counted_as_numbers(text):
    assert numbers_from_text(text) == []


def test_text_inside_quotes_is_skipped():
    from sayman.documents.citation import quoted_spans
    text = 'Vade 30 gün: "fatura tarihinden itibaren 45 gün içinde öder" [SOZ-S001#m3]'
    assert [s.metin for s in numbers_from_text(text, quoted_spans(text))] == ["30"]


# ---------------------------------------------------------------- sources


def test_rejected_call_is_not_a_source():
    refusal = DATA["cagrilar"]["satis_nakit_ret"]
    assert refusal["hata_mi"]
    k = collect_sources("", [refusal], TODAY)
    assert {x.call_id for x in k} == {"bugun"}


def test_description_text_is_not_a_source():
    call = {"arac": "x", "argumanlar": {}, "hata_mi": False,
             "yanit": {"cagri_id": "C1", "sonuc": {"aciklama": "Kur %10 değişirse 999 TL", "tutar": "5.00"}}}
    k = collect_sources("", [call], TODAY)
    assert {(x.alan, x.deger) for x in k if x.call_id == "C1"} == {("tutar", Decimal("5.00"))}


def test_period_and_percent_from_key_name():
    call = {"arac": "x", "argumanlar": {"donem": "son_90_gun"}, "hata_mi": False,
             "yanit": {"cagri_id": "C1", "sonuc": {"son_12_ay_kapanan": 4, "kur_yuzde_10_etki": "1.00",
                                                   "gecikme_31_60": "2.00", "gecikme_90_ustu": "3.00"}}}
    k = {(x.tur, x.deger) for x in collect_sources("", [call], TODAY) if x.call_id == "C1"}
    assert {("ay", 12), ("yuzde", 10), ("gun", 31), ("gun", 60), ("gun", 90)} <= k
    assert ("gun", 90) in k  # son_90_gun from the argument


def test_number_in_question_is_a_source():
    result = verify("Son 45 günde hareket yok.", "Son 45 günde ne oldu?", [], TODAY)
    assert result.gecerli


def test_todays_date_is_a_source():
    assert verify("1 Eylül 2026 itibarıyla durum bu.", "", [], TODAY).gecerli
    assert not verify("2 Eylül 2026 itibarıyla durum bu.", "", [], TODAY).gecerli


# ---------------------------------------------------------------- added after the draft set was frozen
# These cases were found while the verifier was being written; they were added here, not to the frozen set.

LAW_CALL = {
    "arac": "belge_ara", "argumanlar": {"sorgu": "ticari defterler saklama"}, "hata_mi": False,
    "yanit": {"cagri_id": "Ckanun", "arac": "belge_ara", "tarih": "2026-09-01", "kapsam": "tüm şirket",
              "sonuc": {"sorgu": "ticari defterler saklama", "sonuclar": [{
                  "parca_id": "KANUN-6102#m82", "belge": "6102 sayılı Türk Ticaret Kanunu",
                  "madde": "Saklama yükümlülüğü",
                  "metin": "Her tacir, ticari defterlerini on yıl süreyle saklamakla yükümlüdür."}]},
              "kaynak": {"belge_parcalari": ["KANUN-6102#m82"]}}}


def test_law_chunk_id_is_not_a_number_and_its_numbers_are_sources():
    answer = ('6102 sayılı Kanun\'un 82. maddesine göre: "ticari defterlerini on yıl süreyle saklamakla '
             'yükümlüdür" [KANUN-6102#m82]')
    result = verify(answer, "Defterleri ne kadar saklamalıyız?", [LAW_CALL], TODAY)
    assert [s.metin for s in result.numbers] == ["6102", "82"]
    assert result.gecerli
    assert not verify("Kanunun 83. maddesine göre on yıl.", "", [LAW_CALL], TODAY).gecerli


def test_both_ends_of_range_are_checked():
    assert [s.metin for s in numbers_from_text("31-60 gün dilimi")] == ["31", "60"]
    aging = DATA["cagrilar"]["yaslandirma"]
    assert verify("31-60 gün diliminde alacak var.", "", [aging], TODAY).gecerli
    assert [s.metin for s in verify("31-65 gün diliminde alacak var.", "", [aging], TODAY).unsourced] == ["65"]


def test_blind_spot_number_in_question_counts_as_source():
    """Second known limit: a number in the user's question is a source. The real DSO is 50.93 days; if the user asks "59 mu?"
    and the model says "Evet, 59 gün", the verifier lets it pass."""
    dso = DATA["cagrilar"]["dso"]
    result = verify("Evet, son 90 günlük DSO 59 gün.", "Son 90 günlük DSO 59 gün mü?", [dso], TODAY)
    assert result.gecerli
    assert ("soru", "soru") in next(s for s in result.numbers if s.metin == "59").sources
    assert not verify("Evet, son 90 günlük DSO 59 gün.", "Son 90 günlük DSO kaç?", [dso], TODAY).gecerli


def test_customer_name_in_quotes_is_rejected():
    """Double quotes are only for document citations (strict on purpose): a customer name of three or more words put in
    quotes does not pass."""
    cash = DATA["cagrilar"]["musteri_a"]
    result = verify('"Green Home Store Ltd" müşterisinin açık alacağı 7.775.050,92 TL.', "", [cash], TODAY)
    assert not result.unsourced
    assert [a.reason for a in result.invalid_citations] == ["kimliksiz_alinti"]


# Wrong refusals seen in the smoke test with the real model (scripts/phase06_smoke.py, first run):


def test_bold_markers_do_not_hide_currency():
    """"**43.510.100,56 TL** ödeme": "TL**" must be read as the currency; a fractional number must not become a count because of
    the word "ödeme"."""
    upcoming = DATA["cagrilar"]["yaklasan"]
    draft = "Toplam **43.510.100,56 TL** ödeme var; **657.272,69 TL'si** vadesi geçmiş."
    assert verify(draft, "", [upcoming], TODAY).gecerli
    assert [(s.tur) for s in numbers_from_text("43.510.100,56 adet")] == ["bilinmeyen"]


def test_date_range_reads_two_dates():
    assert [(s.metin, s.day_month) for s in numbers_from_text("2. Hafta (09-15 Eylül)")] == \
        [("09", (9, 9)), ("15 Eylül", (15, 9))]
    [a, b] = numbers_from_text("25-31 Ağustos 2026")
    assert (a.tarih, b.tarih) == (dt.date(2026, 8, 25), dt.date(2026, 8, 31))
    week = {"arac": "nakit_akis_tahmini", "argumanlar": {}, "hata_mi": False,
             "yanit": {"cagri_id": "C1", "sonuc": {"haftalar": [{"baslangic": "2026-09-09", "bitis": "2026-09-15"}]}}}
    assert verify("2. hafta (09-15 Eylül)", "", [week], TODAY).gecerli
    assert [s.metin for s in verify("2. hafta (09-16 Eylül)", "", [week], TODAY).unsourced] == ["16 Eylül"]


def test_formatted_numbered_list_marker():
    assert numbers_from_text("*   **1. Hafta**") == []
    assert numbers_from_text("- 2) Prime Design House Ltd") == []


def test_short_quote_without_digits_is_not_a_citation():
    """A quoted phrase like '"kaç yıl" sorusuna' with no digits and fewer than three words does not count as a citation; a
    quoted text with digits or a longer one is still kimliksiz_alinti."""
    assert verify('Belgede "kaç yıl" sorusunun cevabı yok.', "", [], TODAY).gecerli
    assert not verify('Belgede "30 gün" yazıyor.', "", [], TODAY).gecerli
    assert not verify('Belgede "ödeme banka havalesiyle yapılır" yazıyor.', "", [], TODAY).gecerli


def test_blind_spot_number_written_in_words_is_not_checked():
    """Third known limit: the verifier only looks at numbers written in digits. Since Phase 7 a citation is required in a
    document answer, so "yirmi yıl" without a citation no longer passes. But if a wrong duration written in words is placed
    next to a real citation, it passes: the claim is not compared with the citation."""
    question = "Defterleri kaç yıl saklamalıyız?"
    without_citation = verify("Ticari defterleri yirmi yıl saklamak zorundasınız.", question, [LAW_CALL], TODAY)
    assert without_citation.numbers == [] and without_citation.citation_missing and not without_citation.gecerli
    with_citation = verify('Ticari defterleri yirmi yıl saklamak zorundasınız: "ticari defterlerini on yıl süreyle '
                       'saklamakla yükümlüdür" [KANUN-6102#m82]', question, [LAW_CALL], TODAY)
    assert with_citation.gecerli and with_citation.numbers == []
    assert not verify("Ticari defterleri 20 yıl saklamak zorundasınız.", question, [LAW_CALL], TODAY).gecerli


def test_citation_is_required_when_document_search_returned_results():
    """Phase 7 decision: if document search returned results in this turn, the answer must contain at least one valid citation."""
    question = "Defterleri kaç yıl saklamalıyız?"
    assert verify("Belgelerde bu konuda bir süre geçmiyor.", question, [LAW_CALL], TODAY).citation_missing
    empty = {**LAW_CALL, "yanit": {**LAW_CALL["yanit"], "sonuc": {"sorgu": "x", "sonuclar": []}}}
    assert verify("Belgelerde bu konuda bir madde bulunamadı.", question, [empty], TODAY).gecerli
    assert verify("Bu soru belgeyle ilgili değil.", question, [], TODAY).gecerli
