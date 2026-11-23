"""Phase 5 acceptance criterion: unit tests for the citation check.

If the model cites text that is not in the document, attaches a citation to the wrong chunk or alters a digit, it is caught.
"""

from sayman.documents.citation import citations, check_answer, check_citation, normalize_text

CHUNK_TEXTS = {
    "SOZ-S001#m3": "ŞİRKET, TEDARİKÇİ'nin düzenlediği faturaları fatura tarihinden itibaren 30 gün içinde banka "
                   "havalesiyle öder. Ödeme günü hafta sonuna denk gelirse izleyen ilk iş günü ödeme yapılır.",
    "SOZ-S001#m5": "ŞİRKET, vadesinde ödemediği tutarlara vade tarihinden fiili ödeme tarihine kadar aylık %2,25 "
                   "oranında temerrüt faizi ödemeyi kabul eder.",
    "SOZ-S002#m5": "ŞİRKET, vadesinde ödemediği tutarlara vade tarihinden fiili ödeme tarihine kadar aylık %12 "
                   "oranında temerrüt faizi ödemeyi kabul eder.",
    "POL-NAKIT#m3": "Toplam nakdin %40 oranından fazlası tek bir bankada tutulmaz.",
}


def test_verbatim_citation_passes():
    s = check_citation("faturaları fatura tarihinden itibaren 30 gün içinde banka havalesiyle öder", "SOZ-S001#m3",
                   CHUNK_TEXTS)
    assert s.gecerli and s.reason == "tamam"


def test_case_is_folded_by_turkish_rules():
    assert check_citation("şirket, tedarikçi'nin düzenlediği faturaları", "SOZ-S001#m3", CHUNK_TEXTS).gecerli
    assert check_citation("TOPLAM NAKDİN %40 ORANINDAN FAZLASI", "POL-NAKIT#m3", CHUNK_TEXTS).gecerli
    assert normalize_text("IŞIK İLE") == "ışık ile"


def test_apostrophes_and_quotation_marks_are_normalised():
    assert check_citation("ŞİRKET, TEDARİKÇİ’nin düzenlediği faturaları", "SOZ-S001#m3", CHUNK_TEXTS).gecerli


def test_whitespace_and_line_breaks_are_normalised():
    assert check_citation("30 gün  içinde\nbanka   havalesiyle öder", "SOZ-S001#m3", CHUNK_TEXTS).gecerli


def test_fabricated_citation_is_caught():
    s = check_citation("fatura tarihinden itibaren 45 gün içinde çekle öder", "SOZ-S001#m3", CHUNK_TEXTS)
    assert not s.gecerli and s.reason == "metinde_yok" and s.found_in == ()


def test_altered_digit_is_caught():
    assert not check_citation("aylık %2,5 oranında temerrüt faizi", "SOZ-S001#m5", CHUNK_TEXTS).gecerli
    assert not check_citation("aylık %1,2 oranında temerrüt faizi", "SOZ-S002#m5", CHUNK_TEXTS).gecerli
    assert not check_citation("aylık %12 oranında temerrüt faizi", "SOZ-S001#m5", CHUNK_TEXTS).gecerli
    assert not check_citation("otuz gün içinde banka havalesiyle öder", "SOZ-S001#m3", CHUNK_TEXTS).gecerli


def test_citation_starting_or_ending_mid_number_is_caught():
    texts = {**CHUNK_TEXTS,
                "POL-CEK#m2": "Vadesi düzenleme tarihinden itibaren 120 günü aşan çek kabul edilmez.",
                "POL-ONAY#m2": "50.000 TL'ye kadar olan ödemeleri finans uzmanı, 250.000 TL'ye kadar olanları finans "
                               "müdürü, 1.000.000 TL'ye kadar olanları CFO onaylar."}
    assert check_citation("20 günü aşan çek kabul edilmez", "POL-CEK#m2", texts).reason == "metinde_yok"
    assert check_citation("50.000 TL'ye kadar olanları finans müdürü", "POL-ONAY#m2", texts).reason == "metinde_yok"
    assert check_citation("fiili ödeme tarihine kadar aylık %2", "SOZ-S001#m5", texts).reason == "metinde_yok"
    assert check_citation("ödeme tarihine kadar aylık %2,", "SOZ-S001#m5", texts).reason == "metinde_yok"
    assert check_citation("şirket, vadesinde ödemediği tutarlar", "SOZ-S001#m5", texts).reason == "metinde_yok"
    # identical expressions at the boundary pass; whether or not the sentence-final full stop is part of the citation
    assert check_citation("120 günü aşan çek kabul edilmez.", "POL-CEK#m2", texts).gecerli
    assert check_citation("120 günü aşan çek kabul edilmez", "POL-CEK#m2", texts).gecerli
    assert check_citation("250.000 TL'ye kadar olanları finans müdürü", "POL-ONAY#m2", texts).gecerli
    assert check_citation("aylık %2,25 oranında", "SOZ-S001#m5", texts).gecerli


def test_real_citation_attached_to_wrong_chunk_is_caught():
    s = check_citation("aylık %2,25 oranında temerrüt faizi", "SOZ-S002#m5", CHUNK_TEXTS)
    assert not s.gecerli and s.reason == "baska_parcada" and s.found_in == ("SOZ-S001#m5",)


def test_unknown_chunk_is_caught():
    s = check_citation("aylık %2,25 oranında temerrüt faizi", "SOZ-S999#m5", CHUNK_TEXTS)
    assert not s.gecerli and s.reason == "bilinmeyen_parca"


def test_too_short_citation_is_rejected():
    assert check_citation("30 gün", "SOZ-S001#m3", CHUNK_TEXTS).reason == "cok_kisa"


def test_elided_citation_parts_are_searched_in_order():
    assert check_citation("faturaları ... 30 gün içinde … öder", "SOZ-S001#m3", CHUNK_TEXTS).gecerli
    assert not check_citation("30 gün içinde ... faturaları ... öder", "SOZ-S001#m3", CHUNK_TEXTS).gecerli


def test_references_in_answer_are_extracted_and_checked():
    answer = ('Vade 30 gündür: "fatura tarihinden itibaren 30 gün içinde banka havalesiyle öder" [SOZ-S001#m3]. '
             'Gecikmede “aylık %2,5 oranında temerrüt faizi” [SOZ-S001#m5] işler.')
    assert citations(answer) == [("fatura tarihinden itibaren 30 gün içinde banka havalesiyle öder", "SOZ-S001#m3"),
                              ("aylık %2,5 oranında temerrüt faizi", "SOZ-S001#m5")]
    assert [s.gecerli for s in check_answer(answer, CHUNK_TEXTS)] == [True, False]


def test_identifier_without_citation_is_caught():
    s = check_answer("Vade 45 gündür [SOZ-S001#m3].", CHUNK_TEXTS)
    assert [(x.gecerli, x.reason, x.parca_id) for x in s] == [(False, "alintisiz_atif", "SOZ-S001#m3")]


def test_fabricated_single_quoted_citation_fails():
    s = check_answer("Vade: '45 gün içinde çekle öder' [SOZ-S001#m3]", CHUNK_TEXTS)
    assert [(x.gecerli, x.reason) for x in s] == [(False, "alintisiz_atif")]


def test_quoted_citation_without_identifier_is_caught():
    s = check_answer('Sözleşme "45 gün içinde çekle öder" diyor; ayrıca "aylık %2,25 oranında temerrüt '
                          'faizi" [SOZ-S001#m5] işler.', CHUNK_TEXTS)
    assert [(x.gecerli, x.reason) for x in s] == [(False, "kimliksiz_alinti"), (True, "tamam")]


def test_known_limit_real_but_irrelevant_citation_passes():
    """Known limit: the check verifies that the citation occurs in the text, it does not verify that it supports the claim.

    If a real but irrelevant citation is attached to the claim "Vade 90 gündür", the check lets it pass."""
    answer = 'Vade 90 gündür: "Ödeme günü hafta sonuna denk gelirse izleyen ilk iş günü" [SOZ-S001#m3]'
    assert [s.gecerli for s in check_answer(answer, CHUNK_TEXTS)] == [True]
