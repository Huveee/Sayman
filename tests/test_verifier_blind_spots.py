"""Doğrulayıcının iki kör noktası (Faz 9 ve Faz 10 provasında bulundu): doğru cevaplar yanlışlıkla durduruluyordu.

1. Kayıt numarası + sayılabilir isim: "564713'tür (fatura tarihi ...)" ya da "1335451 no'lu çek" yazılınca sayı
   ardındaki "fatura"/"çek" yüzünden adet türü alıyordu; aracın kayıt numarası ise metin olduğu için tutar türünde
   kaynaktı. Adet ile tutar eşleşmediği için doğru numara kaynaksız sayılıyordu.
2. Belgede yazıyla yazılan sayı: politika "son on iki ayın" diyor, cevap "son 12 ayın" diyor; belge metninden yalnızca
   rakamlar kaynak sayıldığı için 12 kaynaksız kalıyordu.

Düzeltmeler doğrulayıcıyı gevşetmemeli: yanlış numara, yanlış sayı ve yanlış tür yine yakalanır.
"""

from __future__ import annotations

import datetime as dt

from sayman.assistant.verifier import dogrula, kaynaklar

BUGUN = dt.date(2026, 9, 1)

FATURA = {
    "arac": "fatura_ara", "argumanlar": {"fatura_no": "564713"}, "hata_mi": False,
    "yanit": {"cagri_id": "Cfat", "arac": "fatura_ara", "tarih": "2026-09-01", "kapsam": "tüm şirket",
              "sonuc": {"faturalar": [{"fatura_id": "F035729", "kaynak_fatura_no": "564713", "tur": "satis",
                                       "musteri": "Lale Tekstil A.Ş.", "fatura_tarihi": "2026-08-30",
                                       "tutar": "48210.50", "durum": "açık"}]}}}

CEK = {
    "arac": "cek_portfoyu", "argumanlar": {}, "hata_mi": False,
    "yanit": {"cagri_id": "Ccek", "arac": "cek_portfoyu", "tarih": "2026-09-01", "kapsam": "tüm şirket",
              "sonuc": {"cekler": [{"cek_id": "C560866", "cek_no": "1335451", "musteri": "Lale Tekstil A.Ş.",
                                    "vade_tarihi": "2026-09-20", "tutar": "19032.82", "durum": "portföyde"}]}}}

POLITIKA = ("Ana TL hesabının gün içindeki en düşük bakiyesi, son on iki ayın ortalama aylık net satışının %15 "
            "oranından az olmamalıdır. Eşik iki yüz elli bin TL'nin altına inemez; ihlal on beş gün içinde "
            "giderilir ve kayıtlar on yıl saklanır.")
BELGE = {
    "arac": "belge_ara", "argumanlar": {"sorgu": "asgari nakit"}, "hata_mi": False,
    "yanit": {"cagri_id": "Cpol", "arac": "belge_ara", "tarih": "2026-09-01", "kapsam": "tüm şirket",
              "sonuc": {"sorgu": "asgari nakit", "sonuclar": [{
                  "parca_id": "POL-NAKIT#m3", "belge": "Nakit Yönetimi Politikası", "madde": "Güvenlik Eşiği",
                  "metin": POLITIKA}]},
              "kaynak": {"belge_parcalari": ["POL-NAKIT#m3"]}}}
ALINTI = ' "en düşük bakiyesi, son on iki ayın ortalama aylık net satışının" [POL-NAKIT#m3]'


def _kaynaksiz(cevap, cagrilar, soru=""):
    return [s.metin for s in dogrula(cevap, soru, cagrilar, BUGUN).kaynaksiz]


# ---------------------------------------------------------------- 1. kayıt numarası


def test_kayit_numarasi_ardinda_fatura_kelimesi_olsa_da_eslesir():
    cevap = "Faturanın kaynak numarası 564713'tür (fatura tarihi 2026-08-30), tutarı 48.210,50 TL."
    assert _kaynaksiz(cevap, [FATURA]) == []
    assert dogrula(cevap, "564713 numaralı fatura ne durumda?", [FATURA], BUGUN).gecerli


def test_kayit_numarasi_parantezde_ve_no_lu_yazilisla_eslesir():
    assert _kaynaksiz("Fatura 564713'dir (fatura ID: F035729).", [FATURA]) == []
    assert _kaynaksiz("1335451 no'lu çek 20 Eylül 2026'da tahsil edilecek: 19.032,82 TL.", [CEK]) == []


def test_yanlis_kayit_numarasi_yine_yakalanir():
    assert _kaynaksiz("Faturanın kaynak numarası 564714'tür (fatura tarihi 2026-08-30).", [FATURA]) == ["564714"]
    assert _kaynaksiz("1335452 no'lu çek portföyde.", [CEK]) == ["1335452"]


def test_kayit_numarasi_tutar_olarak_kullanilamaz():
    """Kayıt numarası artık tutar türünde kaynak değildir: "564713 TL" gibi bir uydurma yakalanır."""
    assert _kaynaksiz("Faturanın tutarı 564.713 TL.", [FATURA]) == ["564.713"]
    assert _kaynaksiz("Çekin tutarı 1.335.451 TL.", [CEK]) == ["1.335.451"]


def test_kayit_numarasi_kaynagi_numara_turundedir():
    k = [x for x in kaynaklar("", [FATURA, CEK], BUGUN) if x.alan in ("kaynak_fatura_no", "cek_no")]
    assert {(x.alan, x.tur) for x in k} == {("kaynak_fatura_no", "numara"), ("cek_no", "numara")}
    # Tutar ve tarih alanları eskisi gibi
    assert any(x.alan == "tutar" and x.tur == "tutar" for x in kaynaklar("", [FATURA], BUGUN))


# ---------------------------------------------------------------- 2. belgede yazıyla sayı


def test_belgedeki_yaziyla_sayi_rakamla_yazilinca_eslesir():
    cevap = "Politikaya göre son 12 ayın ortalama aylık net satışının %15'i eşiktir:" + ALINTI
    assert _kaynaksiz(cevap, [BELGE]) == []
    assert dogrula(cevap, "Nakit politikamıza göre asgari nakit eşiği nedir?", [BELGE], BUGUN).gecerli


def test_yaziyla_bilesik_sayilar_ve_olcek():
    cevap = "Eşik 250 bin TL'nin altına inemez, ihlal 15 gün içinde giderilir, kayıtlar 10 yıl saklanır:" + ALINTI
    assert _kaynaksiz(cevap, [BELGE]) == []
    assert _kaynaksiz("Eşik 250.000 TL'dir." + ALINTI, [BELGE]) == []


def test_yaziyla_sayi_turunu_korur():
    """"on iki ay" ay türündedir: yüzde ya da gün olarak kullanılamaz; "on yıl" 10 yıldır, 20 yıl değil."""
    assert _kaynaksiz("Eşik %12'dir." + ALINTI, [BELGE]) == ["%12"]
    assert _kaynaksiz("Son 12 gün esas alınır." + ALINTI, [BELGE]) == ["12"]
    assert _kaynaksiz("Kayıtlar 20 yıl saklanır." + ALINTI, [BELGE]) == ["20"]
    assert _kaynaksiz("İhlal 16 gün içinde giderilir." + ALINTI, [BELGE]) == ["16"]


def test_belirsiz_tanimlik_bir_sayi_sayilmaz():
    """"bir" tek başına belirsiz tanımlıktır ("bir müşteri"); bileşikte ve ölçekle sayıdır ("on bir", "bir milyon")."""
    cagri = {**BELGE, "yanit": {**BELGE["yanit"], "sonuc": {"sorgu": "x", "sonuclar": [{
        "parca_id": "POL-NAKIT#m4", "belge": "Nakit Yönetimi Politikası", "madde": "Bildirim",
        "metin": "Bir müşteri için on bir gün ve bir milyon TL sınırı uygulanır."}]}}}
    degerler = {(x.tur, int(x.deger)) for x in kaynaklar("", [cagri], BUGUN) if x.alan == "metin"}
    assert ("gun", 11) in degerler and ("tutar", 1_000_000) in degerler
    assert not any(d == 1 for _, d in degerler)


def test_ek_almis_ve_sira_sayisi_kelimeler_sayi_sayilmaz():
    """Yalnızca ek almamış sayı kelimeleri okunur: "ikinci", "onda", "altına" sayı değildir."""
    cagri = {**BELGE, "yanit": {**BELGE["yanit"], "sonuc": {"sorgu": "x", "sonuclar": [{
        "parca_id": "POL-NAKIT#m5", "belge": "Nakit Yönetimi Politikası", "madde": "İkinci kontrol",
        "metin": "İkinci kontrol, eşiğin altına inildiğinde yapılır; onda biri ayrılır."}]}}}
    assert [x for x in kaynaklar("", [cagri], BUGUN) if x.alan in ("metin", "madde")] == []


def test_sorudaki_yaziyla_sayi_da_kaynaktir():
    assert _kaynaksiz("Son 3 ayda 5 fatura kesildi.", [], soru="Son üç ayda kaç fatura kestik?") == ["5"]


def test_turu_belli_olmayan_yaziyla_sayi_kaynak_degildir():
    """Ardında tür kelimesi olmayan yazıyla sayı ("her iki borç", "binde üç") kaynak sayılmaz; sayılsaydı türü belli
    olmayan kaynak olarak uydurma "2 gün" ya da "3 TL" ile eşleşirdi."""
    cagri = {**BELGE, "yanit": {**BELGE["yanit"], "sonuc": {"sorgu": "x", "sonuclar": [{
        "parca_id": "KANUN-6098#m1", "belge": "Türk Borçlar Kanunu", "madde": "Takas",
        "metin": "Her iki borç muaccel ise ve binde üç gecikme cezası öngörülmüşse takas yapılabilir."}]}}}
    alinti = ' "Her iki borç muaccel ise ve binde üç gecikme cezası" [KANUN-6098#m1]'
    assert _kaynaksiz("Takas için 2 gün beklenir, ceza 3 TL'dir:" + alinti, [cagri]) == ["2", "3"]
    assert _kaynaksiz("Son 2 fatura kesildi.", [], soru="Kaç fatura kestik, iki mi?") == ["2"]
