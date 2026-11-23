"""Two blind spots of the verifier (found in the Phase 9 and Phase 10 rehearsal): correct answers were being stopped by
mistake.

1. A record number + a countable noun: when "564713'tür (fatura tarihi ...)" or "1335451 no'lu çek" is written, the
   number took the count kind because of the "fatura"/"çek" after it; the tool's record number is text, so it was a source
   of the amount kind. Because a count and an amount do not match, the correct number was treated as unsourced.
2. A number written in words in the document: the policy says "son on iki ayın", the answer says "son 12 ayın"; because
   only digits in the document text counted as a source, 12 was left unsourced.

The fixes must not loosen the verifier: a wrong number, a wrong figure and a wrong kind are still caught.
"""

from __future__ import annotations

import datetime as dt

from sayman.assistant.verifier import verify, collect_sources

TODAY = dt.date(2026, 9, 1)

INVOICE_CALL = {
    "arac": "fatura_ara", "argumanlar": {"fatura_no": "564713"}, "hata_mi": False,
    "yanit": {"cagri_id": "Cfat", "arac": "fatura_ara", "tarih": "2026-09-01", "kapsam": "tüm şirket",
              "sonuc": {"faturalar": [{"fatura_id": "F035729", "kaynak_fatura_no": "564713", "tur": "satis",
                                       "musteri": "Lale Tekstil A.Ş.", "fatura_tarihi": "2026-08-30",
                                       "tutar": "48210.50", "durum": "açık"}]}}}

CHEQUE_CALL = {
    "arac": "cek_portfoyu", "argumanlar": {}, "hata_mi": False,
    "yanit": {"cagri_id": "Ccek", "arac": "cek_portfoyu", "tarih": "2026-09-01", "kapsam": "tüm şirket",
              "sonuc": {"cekler": [{"cek_id": "C560866", "cek_no": "1335451", "musteri": "Lale Tekstil A.Ş.",
                                    "vade_tarihi": "2026-09-20", "tutar": "19032.82", "durum": "portföyde"}]}}}

POLICY_TEXT = ("Ana TL hesabının gün içindeki en düşük bakiyesi, son on iki ayın ortalama aylık net satışının %15 "
            "oranından az olmamalıdır. Eşik iki yüz elli bin TL'nin altına inemez; ihlal on beş gün içinde "
            "giderilir ve kayıtlar on yıl saklanır.")
DOCUMENT_CALL = {
    "arac": "belge_ara", "argumanlar": {"sorgu": "asgari nakit"}, "hata_mi": False,
    "yanit": {"cagri_id": "Cpol", "arac": "belge_ara", "tarih": "2026-09-01", "kapsam": "tüm şirket",
              "sonuc": {"sorgu": "asgari nakit", "sonuclar": [{
                  "parca_id": "POL-NAKIT#m3", "belge": "Nakit Yönetimi Politikası", "madde": "Güvenlik Eşiği",
                  "metin": POLICY_TEXT}]},
              "kaynak": {"belge_parcalari": ["POL-NAKIT#m3"]}}}
CITATION = ' "en düşük bakiyesi, son on iki ayın ortalama aylık net satışının" [POL-NAKIT#m3]'


def _unsourced(answer, calls, question=""):
    return [s.metin for s in verify(answer, question, calls, TODAY).unsourced]


# ---------------------------------------------------------------- 1. record number


def test_record_number_matches_even_with_invoice_word_after_it():
    answer = "Faturanın kaynak numarası 564713'tür (fatura tarihi 2026-08-30), tutarı 48.210,50 TL."
    assert _unsourced(answer, [INVOICE_CALL]) == []
    assert verify(answer, "564713 numaralı fatura ne durumda?", [INVOICE_CALL], TODAY).gecerli


def test_record_number_matches_in_parentheses_and_with_no_suffix():
    assert _unsourced("Fatura 564713'dir (fatura ID: F035729).", [INVOICE_CALL]) == []
    assert _unsourced("1335451 no'lu çek 20 Eylül 2026'da tahsil edilecek: 19.032,82 TL.", [CHEQUE_CALL]) == []


def test_wrong_record_number_is_still_caught():
    assert _unsourced("Faturanın kaynak numarası 564714'tür (fatura tarihi 2026-08-30).", [INVOICE_CALL]) == ["564714"]
    assert _unsourced("1335452 no'lu çek portföyde.", [CHEQUE_CALL]) == ["1335452"]


def test_record_number_cannot_be_used_as_amount():
    """A record number is no longer a source of the amount kind: a fabrication like "564713 TL" is caught."""
    assert _unsourced("Faturanın tutarı 564.713 TL.", [INVOICE_CALL]) == ["564.713"]
    assert _unsourced("Çekin tutarı 1.335.451 TL.", [CHEQUE_CALL]) == ["1.335.451"]


def test_record_number_source_is_of_number_kind():
    k = [x for x in collect_sources("", [INVOICE_CALL, CHEQUE_CALL], TODAY) if x.alan in ("kaynak_fatura_no", "cek_no")]
    assert {(x.alan, x.tur) for x in k} == {("kaynak_fatura_no", "numara"), ("cek_no", "numara")}
    # Amount and date fields as before
    assert any(x.alan == "tutar" and x.tur == "tutar" for x in collect_sources("", [INVOICE_CALL], TODAY))


# ---------------------------------------------------------------- 2. number written in words in the document


def test_number_in_words_in_document_matches_when_written_in_digits():
    answer = "Politikaya göre son 12 ayın ortalama aylık net satışının %15'i eşiktir:" + CITATION
    assert _unsourced(answer, [DOCUMENT_CALL]) == []
    assert verify(answer, "Nakit politikamıza göre asgari nakit eşiği nedir?", [DOCUMENT_CALL], TODAY).gecerli


def test_compound_numbers_in_words_and_scale():
    answer = "Eşik 250 bin TL'nin altına inemez, ihlal 15 gün içinde giderilir, kayıtlar 10 yıl saklanır:" + CITATION
    assert _unsourced(answer, [DOCUMENT_CALL]) == []
    assert _unsourced("Eşik 250.000 TL'dir." + CITATION, [DOCUMENT_CALL]) == []


def test_number_in_words_keeps_its_kind():
    """"on iki ay" is of the month kind: it cannot be used as a percentage or as days; "on yıl" is 10 years, not 20."""
    assert _unsourced("Eşik %12'dir." + CITATION, [DOCUMENT_CALL]) == ["%12"]
    assert _unsourced("Son 12 gün esas alınır." + CITATION, [DOCUMENT_CALL]) == ["12"]
    assert _unsourced("Kayıtlar 20 yıl saklanır." + CITATION, [DOCUMENT_CALL]) == ["20"]
    assert _unsourced("İhlal 16 gün içinde giderilir." + CITATION, [DOCUMENT_CALL]) == ["16"]


def test_indefinite_article_bir_is_not_a_number():
    """"bir" on its own is an indefinite article ("bir müşteri"); in a compound and with a scale it is a number ("on bir",
    "bir milyon")."""
    call = {**DOCUMENT_CALL, "yanit": {**DOCUMENT_CALL["yanit"], "sonuc": {"sorgu": "x", "sonuclar": [{
        "parca_id": "POL-NAKIT#m4", "belge": "Nakit Yönetimi Politikası", "madde": "Bildirim",
        "metin": "Bir müşteri için on bir gün ve bir milyon TL sınırı uygulanır."}]}}}
    values = {(x.tur, int(x.deger)) for x in collect_sources("", [call], TODAY) if x.alan == "metin"}
    assert ("gun", 11) in values and ("tutar", 1_000_000) in values
    assert not any(d == 1 for _, d in values)


def test_suffixed_and_ordinal_words_are_not_numbers():
    """Only number words without a suffix are read: "ikinci", "onda", "altına" are not numbers."""
    call = {**DOCUMENT_CALL, "yanit": {**DOCUMENT_CALL["yanit"], "sonuc": {"sorgu": "x", "sonuclar": [{
        "parca_id": "POL-NAKIT#m5", "belge": "Nakit Yönetimi Politikası", "madde": "İkinci kontrol",
        "metin": "İkinci kontrol, eşiğin altına inildiğinde yapılır; onda biri ayrılır."}]}}}
    assert [x for x in collect_sources("", [call], TODAY) if x.alan in ("metin", "madde")] == []


def test_number_in_words_in_question_is_also_a_source():
    assert _unsourced("Son 3 ayda 5 fatura kesildi.", [], question="Son üç ayda kaç fatura kestik?") == ["5"]


def test_number_in_words_of_unknown_kind_is_not_a_source():
    """A number in words with no kind word after it ("her iki borç", "binde üç") is not a source; if it were, it would match
    a fabricated "2 gün" or "3 TL" as a source of unknown kind."""
    call = {**DOCUMENT_CALL, "yanit": {**DOCUMENT_CALL["yanit"], "sonuc": {"sorgu": "x", "sonuclar": [{
        "parca_id": "KANUN-6098#m1", "belge": "Türk Borçlar Kanunu", "madde": "Takas",
        "metin": "Her iki borç muaccel ise ve binde üç gecikme cezası öngörülmüşse takas yapılabilir."}]}}}
    citation = ' "Her iki borç muaccel ise ve binde üç gecikme cezası" [KANUN-6098#m1]'
    assert _unsourced("Takas için 2 gün beklenir, ceza 3 TL'dir:" + citation, [call]) == ["2", "3"]
    assert _unsourced("Son 2 fatura kesildi.", [], question="Kaç fatura kestik, iki mi?") == ["2"]
