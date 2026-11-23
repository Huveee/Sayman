"""Faz 5 kabul ölçütü: alıntı kontrolü birim testleri.

Model belgede olmayan bir alıntı yaparsa, alıntıyı yanlış parçaya bağlarsa ya da bir rakamı değiştirirse yakalanır.
"""

from sayman.documents.citation import atiflar, cevabi_kontrol_et, kontrol_et, normalle

METINLER = {
    "SOZ-S001#m3": "ŞİRKET, TEDARİKÇİ'nin düzenlediği faturaları fatura tarihinden itibaren 30 gün içinde banka "
                   "havalesiyle öder. Ödeme günü hafta sonuna denk gelirse izleyen ilk iş günü ödeme yapılır.",
    "SOZ-S001#m5": "ŞİRKET, vadesinde ödemediği tutarlara vade tarihinden fiili ödeme tarihine kadar aylık %2,25 "
                   "oranında temerrüt faizi ödemeyi kabul eder.",
    "SOZ-S002#m5": "ŞİRKET, vadesinde ödemediği tutarlara vade tarihinden fiili ödeme tarihine kadar aylık %12 "
                   "oranında temerrüt faizi ödemeyi kabul eder.",
    "POL-NAKIT#m3": "Toplam nakdin %40 oranından fazlası tek bir bankada tutulmaz.",
}


def test_birebir_alinti_gecer():
    s = kontrol_et("faturaları fatura tarihinden itibaren 30 gün içinde banka havalesiyle öder", "SOZ-S001#m3",
                   METINLER)
    assert s.gecerli and s.neden == "tamam"


def test_buyuk_kucuk_harf_turkce_kurala_gore_esitlenir():
    assert kontrol_et("şirket, tedarikçi'nin düzenlediği faturaları", "SOZ-S001#m3", METINLER).gecerli
    assert kontrol_et("TOPLAM NAKDİN %40 ORANINDAN FAZLASI", "POL-NAKIT#m3", METINLER).gecerli
    assert normalle("IŞIK İLE") == "ışık ile"


def test_kesme_ve_tirnak_isaretleri_esitlenir():
    assert kontrol_et("ŞİRKET, TEDARİKÇİ’nin düzenlediği faturaları", "SOZ-S001#m3", METINLER).gecerli


def test_bosluk_ve_satir_sonu_esitlenir():
    assert kontrol_et("30 gün  içinde\nbanka   havalesiyle öder", "SOZ-S001#m3", METINLER).gecerli


def test_uydurma_alinti_yakalanir():
    s = kontrol_et("fatura tarihinden itibaren 45 gün içinde çekle öder", "SOZ-S001#m3", METINLER)
    assert not s.gecerli and s.neden == "metinde_yok" and s.bulundugu == ()


def test_degistirilmis_rakam_yakalanir():
    assert not kontrol_et("aylık %2,5 oranında temerrüt faizi", "SOZ-S001#m5", METINLER).gecerli
    assert not kontrol_et("aylık %1,2 oranında temerrüt faizi", "SOZ-S002#m5", METINLER).gecerli
    assert not kontrol_et("aylık %12 oranında temerrüt faizi", "SOZ-S001#m5", METINLER).gecerli
    assert not kontrol_et("otuz gün içinde banka havalesiyle öder", "SOZ-S001#m3", METINLER).gecerli


def test_sayinin_ortasindan_baslayan_ya_da_biten_alinti_yakalanir():
    metinler = {**METINLER,
                "POL-CEK#m2": "Vadesi düzenleme tarihinden itibaren 120 günü aşan çek kabul edilmez.",
                "POL-ONAY#m2": "50.000 TL'ye kadar olan ödemeleri finans uzmanı, 250.000 TL'ye kadar olanları finans "
                               "müdürü, 1.000.000 TL'ye kadar olanları CFO onaylar."}
    assert kontrol_et("20 günü aşan çek kabul edilmez", "POL-CEK#m2", metinler).neden == "metinde_yok"
    assert kontrol_et("50.000 TL'ye kadar olanları finans müdürü", "POL-ONAY#m2", metinler).neden == "metinde_yok"
    assert kontrol_et("fiili ödeme tarihine kadar aylık %2", "SOZ-S001#m5", metinler).neden == "metinde_yok"
    assert kontrol_et("ödeme tarihine kadar aylık %2,", "SOZ-S001#m5", metinler).neden == "metinde_yok"
    assert kontrol_et("şirket, vadesinde ödemediği tutarlar", "SOZ-S001#m5", metinler).neden == "metinde_yok"
    # sınırda olan aynı ifadeler geçer; cümle sonundaki nokta alıntıya dahil olsa da olmasa da
    assert kontrol_et("120 günü aşan çek kabul edilmez.", "POL-CEK#m2", metinler).gecerli
    assert kontrol_et("120 günü aşan çek kabul edilmez", "POL-CEK#m2", metinler).gecerli
    assert kontrol_et("250.000 TL'ye kadar olanları finans müdürü", "POL-ONAY#m2", metinler).gecerli
    assert kontrol_et("aylık %2,25 oranında", "SOZ-S001#m5", metinler).gecerli


def test_gercek_alinti_yanlis_parcaya_baglanirsa_yakalanir():
    s = kontrol_et("aylık %2,25 oranında temerrüt faizi", "SOZ-S002#m5", METINLER)
    assert not s.gecerli and s.neden == "baska_parcada" and s.bulundugu == ("SOZ-S001#m5",)


def test_bilinmeyen_parca_yakalanir():
    s = kontrol_et("aylık %2,25 oranında temerrüt faizi", "SOZ-S999#m5", METINLER)
    assert not s.gecerli and s.neden == "bilinmeyen_parca"


def test_cok_kisa_alinti_kabul_edilmez():
    assert kontrol_et("30 gün", "SOZ-S001#m3", METINLER).neden == "cok_kisa"


def test_atlamali_alinti_parcalari_sirayla_aranir():
    assert kontrol_et("faturaları ... 30 gün içinde … öder", "SOZ-S001#m3", METINLER).gecerli
    assert not kontrol_et("30 gün içinde ... faturaları ... öder", "SOZ-S001#m3", METINLER).gecerli


def test_cevaptaki_atiflar_ayiklanir_ve_kontrol_edilir():
    cevap = ('Vade 30 gündür: "fatura tarihinden itibaren 30 gün içinde banka havalesiyle öder" [SOZ-S001#m3]. '
             'Gecikmede “aylık %2,5 oranında temerrüt faizi” [SOZ-S001#m5] işler.')
    assert atiflar(cevap) == [("fatura tarihinden itibaren 30 gün içinde banka havalesiyle öder", "SOZ-S001#m3"),
                              ("aylık %2,5 oranında temerrüt faizi", "SOZ-S001#m5")]
    assert [s.gecerli for s in cevabi_kontrol_et(cevap, METINLER)] == [True, False]


def test_alintisiz_kimlik_yakalanir():
    s = cevabi_kontrol_et("Vade 45 gündür [SOZ-S001#m3].", METINLER)
    assert [(x.gecerli, x.neden, x.parca_id) for x in s] == [(False, "alintisiz_atif", "SOZ-S001#m3")]


def test_tek_tirnakli_uydurma_alinti_gecemez():
    s = cevabi_kontrol_et("Vade: '45 gün içinde çekle öder' [SOZ-S001#m3]", METINLER)
    assert [(x.gecerli, x.neden) for x in s] == [(False, "alintisiz_atif")]


def test_kimliksiz_tirnakli_alinti_yakalanir():
    s = cevabi_kontrol_et('Sözleşme "45 gün içinde çekle öder" diyor; ayrıca "aylık %2,25 oranında temerrüt '
                          'faizi" [SOZ-S001#m5] işler.', METINLER)
    assert [(x.gecerli, x.neden) for x in s] == [(False, "kimliksiz_alinti"), (True, "tamam")]


def test_sinir_gercek_ama_ilgisiz_alinti_gecer():
    """Bilinen sınır: kontrol alıntının metinde geçtiğini doğrular, iddiayı desteklediğini doğrulamaz.

    "Vade 90 gündür" iddiasına gerçek ama ilgisiz bir alıntı eklenirse kontrol bunu geçirir."""
    cevap = 'Vade 90 gündür: "Ödeme günü hafta sonuna denk gelirse izleyen ilk iş günü" [SOZ-S001#m3]'
    assert [s.gecerli for s in cevabi_kontrol_et(cevap, METINLER)] == [True]
