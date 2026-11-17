"""Faz 6: cevap doğrulayıcı testleri.

Taslak seti (tests/faz6_taslaklar.py) doğrulayıcıdan önce yazıldı; bu testler doğrulayıcının o seti olduğu gibi
geçirip geçirmediğine bakar. Doğru taslakların hepsi geçmeli, yanlış taslaklarda tam olarak beklenen sayılar
yakalanmalı, bilinen kör nokta ise ayrı bir testte açıkça gösterilmelidir.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from faz6_taslaklar import ALINTISI_GECERSIZ, DOGRU, KOR_NOKTA, YANLIS, arac_sonuclari
from sayman.asistan.dogrulayici import dogrula, kaynaklar, metinden_sayilar

VERI = arac_sonuclari()
BUGUN = dt.date.fromisoformat(VERI["bugun"])


def _calistir(soru, anahtarlar, taslak):
    return dogrula(taslak, soru, [VERI["cagrilar"][a] for a in anahtarlar], BUGUN)


# ---------------------------------------------------------------- taslak seti


@pytest.mark.parametrize("ad,soru,anahtarlar,taslak", DOGRU, ids=[t[0] for t in DOGRU])
def test_dogru_taslak_gecer(ad, soru, anahtarlar, taslak):
    sonuc = _calistir(soru, anahtarlar, taslak)
    assert [s.metin for s in sonuc.kaynaksiz] == []
    assert sonuc.gecersiz_alintilar == []
    assert sonuc.gecerli


@pytest.mark.parametrize("ad,soru,anahtarlar,taslak,beklenen", YANLIS, ids=[t[0] for t in YANLIS])
def test_yanlis_taslak_yakalanir(ad, soru, anahtarlar, taslak, beklenen):
    sonuc = _calistir(soru, anahtarlar, taslak)
    assert [s.metin for s in sonuc.kaynaksiz] == beklenen
    assert bool(sonuc.gecersiz_alintilar) == (ad in ALINTISI_GECERSIZ)
    assert not sonuc.gecerli


def test_kor_nokta_dogru_sayi_yanlis_musteri_gecer():
    """Bilinen sınır: doğrulayıcı sayının neye ait olduğuna bakmaz. Green Home Store Ltd'nin açık alacağı Prime
    Design House Ltd için yazıldığında, sayı bu turdaki bir sonuçta geçtiği için cevap geçer. Bu test sınırı
    saklamak için değil, görünür kılmak için vardır: düzeltilirse bu test bilerek değiştirilmelidir."""
    soru, anahtarlar, taslak, (kaynak_cagri, kaynak_alan) = KOR_NOKTA
    sonuc = _calistir(soru, anahtarlar, taslak)
    assert sonuc.gecerli
    [sayi] = sonuc.sayilar
    cagri_id = VERI["cagrilar"][kaynak_cagri]["yanit"]["cagri_id"]
    assert (cagri_id, kaynak_alan) in sayi.kaynaklar
    dogru_musteri = VERI["cagrilar"]["musteri_b"]["yanit"]["cagri_id"]
    assert all(c != dogru_musteri for c, _ in sayi.kaynaklar)


def test_her_sayi_kaynagini_gosterir():
    _, soru, anahtarlar, taslak = next(t for t in DOGRU if t[0] == "nakit_tam")
    sonuc = _calistir(soru, anahtarlar, taslak)
    cid = VERI["cagrilar"]["nakit"]["yanit"]["cagri_id"]
    assert [(s.metin, s.kaynaklar) for s in sonuc.sayilar] == [
        ("75.826.754,65", ((cid, "toplam_tl"),)), ("74.205.083,37", ((cid, "bakiye"), (cid, "bakiye_tl")))]


# ---------------------------------------------------------------- metinden sayı okuma


def _oku(metin):
    return [(s.metin, s.tur, s.deger) for s in metinden_sayilar(metin)]


@pytest.mark.parametrize("metin,beklenen", [
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
def test_sayi_okuma(metin, beklenen):
    assert _oku(metin) == [beklenen]


@pytest.mark.parametrize("metin,beklenen", [
    ("2026-09-01", dt.date(2026, 9, 1)),
    ("1.9.2026", dt.date(2026, 9, 1)),
    ("19 Eylül 2025", dt.date(2025, 9, 19)),
    ("19 eylül 2025'te", dt.date(2025, 9, 19)),
])
def test_tarih_okuma(metin, beklenen):
    [s] = metinden_sayilar(metin)
    assert (s.tur, s.tarih) == ("tarih", beklenen)


def test_yilsiz_tarih():
    [s] = metinden_sayilar("19 Eylül'de doldu")
    assert (s.tur, s.tarih, s.gun_ay) == ("tarih", None, (19, 9))


@pytest.mark.parametrize("metin", [
    "1. Green Home Store Ltd",       # numaralı liste işareti
    "2) Prime Design House Ltd",
    "F032047 numaralı fatura",       # fatura numarası
    "[SOZ-S001#m3]",                 # parça kimliği
    "1 USD = ",                       # kur birimi
])
def test_sayi_sayilmayanlar(metin):
    assert metinden_sayilar(metin) == []


def test_tirnak_ici_atlanir():
    from sayman.belge.alinti import tirnak_araliklari
    metin = 'Vade 30 gün: "fatura tarihinden itibaren 45 gün içinde öder" [SOZ-S001#m3]'
    assert [s.metin for s in metinden_sayilar(metin, tirnak_araliklari(metin))] == ["30"]


# ---------------------------------------------------------------- kaynaklar


def test_reddedilen_cagri_kaynak_degil():
    ret = VERI["cagrilar"]["satis_nakit_ret"]
    assert ret["hata_mi"]
    k = kaynaklar("", [ret], BUGUN)
    assert {x.cagri_id for x in k} == {"bugun"}


def test_aciklama_metni_kaynak_degil():
    cagri = {"arac": "x", "argumanlar": {}, "hata_mi": False,
             "yanit": {"cagri_id": "C1", "sonuc": {"aciklama": "Kur %10 değişirse 999 TL", "tutar": "5.00"}}}
    k = kaynaklar("", [cagri], BUGUN)
    assert {(x.alan, x.deger) for x in k if x.cagri_id == "C1"} == {("tutar", Decimal("5.00"))}


def test_anahtar_adindan_donem_ve_yuzde():
    cagri = {"arac": "x", "argumanlar": {"donem": "son_90_gun"}, "hata_mi": False,
             "yanit": {"cagri_id": "C1", "sonuc": {"son_12_ay_kapanan": 4, "kur_yuzde_10_etki": "1.00",
                                                   "gecikme_31_60": "2.00", "gecikme_90_ustu": "3.00"}}}
    k = {(x.tur, x.deger) for x in kaynaklar("", [cagri], BUGUN) if x.cagri_id == "C1"}
    assert {("ay", 12), ("yuzde", 10), ("gun", 31), ("gun", 60), ("gun", 90)} <= k
    assert ("gun", 90) in k  # argümandaki son_90_gun


def test_sorudaki_sayi_kaynaktir():
    sonuc = dogrula("Son 45 günde hareket yok.", "Son 45 günde ne oldu?", [], BUGUN)
    assert sonuc.gecerli


def test_bugunun_tarihi_kaynaktir():
    assert dogrula("1 Eylül 2026 itibarıyla durum bu.", "", [], BUGUN).gecerli
    assert not dogrula("2 Eylül 2026 itibarıyla durum bu.", "", [], BUGUN).gecerli


# ---------------------------------------------------------------- taslak seti dondurulduktan sonra eklenenler
# Bu durumlar doğrulayıcı yazılırken bulundu; dondurulmuş sete değil buraya eklendi.

KANUN_CAGRISI = {
    "arac": "belge_ara", "argumanlar": {"sorgu": "ticari defterler saklama"}, "hata_mi": False,
    "yanit": {"cagri_id": "Ckanun", "arac": "belge_ara", "tarih": "2026-09-01", "kapsam": "tüm şirket",
              "sonuc": {"sorgu": "ticari defterler saklama", "sonuclar": [{
                  "parca_id": "KANUN-6102#m82", "belge": "6102 sayılı Türk Ticaret Kanunu",
                  "madde": "Saklama yükümlülüğü",
                  "metin": "Her tacir, ticari defterlerini on yıl süreyle saklamakla yükümlüdür."}]},
              "kaynak": {"belge_parcalari": ["KANUN-6102#m82"]}}}


def test_kanun_parca_kimligi_sayi_sayilmaz_ve_numaralari_kaynaktir():
    cevap = ('6102 sayılı Kanun\'un 82. maddesine göre: "ticari defterlerini on yıl süreyle saklamakla '
             'yükümlüdür" [KANUN-6102#m82]')
    sonuc = dogrula(cevap, "Defterleri ne kadar saklamalıyız?", [KANUN_CAGRISI], BUGUN)
    assert [s.metin for s in sonuc.sayilar] == ["6102", "82"]
    assert sonuc.gecerli
    assert not dogrula("Kanunun 83. maddesine göre on yıl.", "", [KANUN_CAGRISI], BUGUN).gecerli


def test_aralik_iki_ucu_da_denetlenir():
    assert [s.metin for s in metinden_sayilar("31-60 gün dilimi")] == ["31", "60"]
    yas = VERI["cagrilar"]["yaslandirma"]
    assert dogrula("31-60 gün diliminde alacak var.", "", [yas], BUGUN).gecerli
    assert [s.metin for s in dogrula("31-65 gün diliminde alacak var.", "", [yas], BUGUN).kaynaksiz] == ["65"]


def test_kor_nokta_sorudaki_sayi_kaynak_sayilir():
    """İkinci bilinen sınır: kullanıcının sorusundaki sayı kaynaktır. Gerçek DSO 50,93 gün; kullanıcı "59 mu?"
    diye sorarsa ve model "Evet, 59 gün" derse doğrulayıcı geçirir."""
    dso = VERI["cagrilar"]["dso"]
    sonuc = dogrula("Evet, son 90 günlük DSO 59 gün.", "Son 90 günlük DSO 59 gün mü?", [dso], BUGUN)
    assert sonuc.gecerli
    assert ("soru", "soru") in next(s for s in sonuc.sayilar if s.metin == "59").kaynaklar
    assert not dogrula("Evet, son 90 günlük DSO 59 gün.", "Son 90 günlük DSO kaç?", [dso], BUGUN).gecerli


def test_tirnak_icindeki_musteri_adi_reddedilir():
    """Çift tırnak yalnızca belge alıntısı içindir (bilerek katı): tırnak içine alınmış üç ya da daha çok kelimelik
    müşteri adı geçmez."""
    nakit = VERI["cagrilar"]["musteri_a"]
    sonuc = dogrula('"Green Home Store Ltd" müşterisinin açık alacağı 7.775.050,92 TL.', "", [nakit], BUGUN)
    assert not sonuc.kaynaksiz
    assert [a.neden for a in sonuc.gecersiz_alintilar] == ["kimliksiz_alinti"]


# Gerçek modelle duman testinde (scripts/faz6_duman.py, ilk çalıştırma) görülen yanlış retler:


def test_kalin_yazi_isareti_para_birimini_gizlemez():
    """"**43.510.100,56 TL** ödeme": "TL**" para birimi olarak okunmalı; kesirli sayı "ödeme" yüzünden adet olmamalı."""
    yaklasan = VERI["cagrilar"]["yaklasan"]
    taslak = "Toplam **43.510.100,56 TL** ödeme var; **657.272,69 TL'si** vadesi geçmiş."
    assert dogrula(taslak, "", [yaklasan], BUGUN).gecerli
    assert [(s.tur) for s in metinden_sayilar("43.510.100,56 adet")] == ["bilinmeyen"]


def test_tarih_araligi_iki_tarih_okunur():
    assert [(s.metin, s.gun_ay) for s in metinden_sayilar("2. Hafta (09-15 Eylül)")] == \
        [("09", (9, 9)), ("15 Eylül", (15, 9))]
    [a, b] = metinden_sayilar("25-31 Ağustos 2026")
    assert (a.tarih, b.tarih) == (dt.date(2026, 8, 25), dt.date(2026, 8, 31))
    hafta = {"arac": "nakit_akis_tahmini", "argumanlar": {}, "hata_mi": False,
             "yanit": {"cagri_id": "C1", "sonuc": {"haftalar": [{"baslangic": "2026-09-09", "bitis": "2026-09-15"}]}}}
    assert dogrula("2. hafta (09-15 Eylül)", "", [hafta], BUGUN).gecerli
    assert [s.metin for s in dogrula("2. hafta (09-16 Eylül)", "", [hafta], BUGUN).kaynaksiz] == ["16 Eylül"]


def test_bicimli_numarali_liste_isareti():
    assert metinden_sayilar("*   **1. Hafta**") == []
    assert metinden_sayilar("- 2) Prime Design House Ltd") == []


def test_kisa_rakamsiz_tirnak_alinti_sayilmaz():
    """'"kaç yıl" sorusuna' gibi rakamsız, üç kelimeden kısa tırnaklı söz alıntı sayılmaz; rakamlı ya da daha uzun
    tırnaklı metin yine kimliksiz_alinti olur."""
    assert dogrula('Belgede "kaç yıl" sorusunun cevabı yok.', "", [], BUGUN).gecerli
    assert not dogrula('Belgede "30 gün" yazıyor.', "", [], BUGUN).gecerli
    assert not dogrula('Belgede "ödeme banka havalesiyle yapılır" yazıyor.', "", [], BUGUN).gecerli


def test_kor_nokta_yaziyla_yazilan_sayi_denetlenmez():
    """Üçüncü bilinen sınır: doğrulayıcı yalnızca rakamla yazılan sayılara bakar. Madde "on yıl" derken cevap
    "yirmi yıl" yazarsa ve alıntı yoksa geçer. Belge cevaplarında alıntı şart değildir (Faz 7'de karar verilecek)."""
    soru = "Defterleri kaç yıl saklamalıyız?"
    sonuc = dogrula("Ticari defterleri yirmi yıl saklamak zorundasınız.", soru, [KANUN_CAGRISI], BUGUN)
    assert sonuc.gecerli and sonuc.sayilar == [] and sonuc.alintilar == []
    assert not dogrula("Ticari defterleri 20 yıl saklamak zorundasınız.", soru, [KANUN_CAGRISI], BUGUN).gecerli
