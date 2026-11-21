"""Faz 8b: doğrulayıcının sahiplik, kimlik ve kapsam kuralları; orkestratörün kapsam düzeltmesi ve notu."""

import datetime as dt
import json
import re

from test_faz6_orkestrator import YURTICI, SahteModel, arac, calistir, cevap, son_arac_sonucu, sunucu, tr  # noqa: F401

from sayman.asistan.dogrulayici import KAPSAM_NOTLARI, dogrula, kimlikler, musteri_sorusu_mu
from sayman.asistan.orkestrator import METINLER, duzeltme_mesaji
from sayman.olcum.puanlama import RET_KELIMELERI

BUGUN = dt.date(2026, 9, 1)
YURTICI_KAPSAM = "yalnızca yurtiçi müşteriler"


def cagri(arac_adi, argumanlar, sonuc, kapsam="tüm şirket", cid="C1"):
    return {"arac": arac_adi, "argumanlar": argumanlar, "hata_mi": False,
            "yanit": {"cagri_id": cid, "arac": arac_adi, "tarih": "2026-09-01", "kapsam": kapsam, "sonuc": sonuc}}


PORTFOY = cagri("cek_portfoyu", {}, {
    "durum": "bekleyen", "cek_sayisi": 295, "toplam_tl": "7674405.79",
    "cekler": [{"cek_id": "C025774", "musteri": "Tan Orkide Mağazacılık A.Ş.", "vade_tarihi": "2026-09-02",
                "tutar_tl": "9521.42"}]}, kapsam=YURTICI_KAPSAM)
OZET = cagri("musteri_ozeti", {"musteri_adi": "Zümrüt Ufuk Tasarım A.Ş."}, {
    "musteri": "Zümrüt Ufuk Tasarım A.Ş.", "bekleyen_cek_sayisi": 2, "bekleyen_cek_tutari": "55275.27"},
    kapsam=YURTICI_KAPSAM, cid="C2")
CEK_SORUSU = "Zümrüt Ufuk Tasarım A.Ş. müşterisinden elimizde bekleyen kaç çek var?"


# ---------------------------------------------------------------- müşteri sorusu mu


def test_musteri_sorusu_yalniz_ad_varsa():
    assert musteri_sorusu_mu("Bright Home Group GmbH müşterisinin bekleyen çekleri var mı?")
    assert musteri_sorusu_mu("Zümrüt Ufuk müşterisinden en son ne zaman tahsilat yaptık?")  # kısa ad
    assert musteri_sorusu_mu("Gama AŞ için toplam açık alacağımız ne kadar?")  # şirket türü
    assert musteri_sorusu_mu("Toros Başak Gıda A.Ş. son 12 ayda ortalama kaç gün gecikmeyle ödedi?")
    # "müşterisi" kelimesi bir ad olmadan geçiyor: müşteri sorusu değil
    assert not musteri_sorusu_mu("F016076 faturasının müşterisi kim, vadesi ne?")
    assert not musteri_sorusu_mu("Vadesini en az 30 gün geçmiş faturalar içinde en büyüğü hangisi, müşterisi kim?")
    assert not musteri_sorusu_mu("Toplam açık alacağımız ne kadar?")


# ---------------------------------------------------------------- sahiplik


def test_portfoy_toplami_musteriye_yazilamaz():
    d = dogrula("Zümrüt Ufuk Tasarım A.Ş. müşterisinden 295 çek bekliyor; toplamı 7.674.405,79 TL.",
                CEK_SORUSU, [PORTFOY], BUGUN)
    assert d.eski_gecerli and d.sahipsiz and not d.gecerli  # eski kurallar geçirirdi
    assert "musteri_ozeti" in duzeltme_mesaji(cevap("x"), d)


def test_musteri_ozetiyle_gecer():
    d = dogrula("2 çek bekliyor; toplamı 55.275,27 TL.", CEK_SORUSU, [PORTFOY, OZET], BUGUN)
    assert d.gecerli and not d.sahipsiz


def test_kisa_adla_sorulup_tam_adla_bulunan_musteri():
    d = dogrula("2 çek bekliyor; toplamı 55.275,27 TL.", "Zümrüt Ufuk müşterisinden kaç çek var?", [OZET], BUGUN)
    assert not d.sahipsiz


def test_baska_musteriyle_yapilan_cagri_sahipli_sayilmaz():
    baska = cagri("musteri_ozeti", {"musteri_adi": "Nar Zirve Ticaret Ltd. Şti."}, {
        "musteri": "Nar Zirve Ticaret Ltd. Şti.", "bekleyen_cek_sayisi": 3, "bekleyen_cek_tutari": "1200.00"})
    d = dogrula("Zümrüt Ufuk Tasarım A.Ş. müşterisinden 3 çek bekliyor; toplamı 1.200,00 TL.", CEK_SORUSU, [baska],
                BUGUN)
    assert d.sahipsiz


def test_baska_musterinin_fatura_kimligi_de_sahipsiz():
    fatura = cagri("fatura_ara", {"musteri_adi": "Nar Zirve Ticaret Ltd. Şti."}, {
        "faturalar": [{"fatura_id": "F035729", "kaynak_fatura_no": "564428", "musteri": "Nar Zirve Ticaret Ltd. Şti."}]})
    d = dogrula("En son fatura F035729.", "Zümrüt Ufuk Tasarım A.Ş. müşterisine kestiğimiz son fatura hangisi?",
                [fatura], BUGUN)
    assert d.sahipsiz


def test_rakamsiz_cevap_ve_belge_cevabi_sahiplige_takilmaz():
    assert not dogrula("Bu müşteriyi bulamadım.", CEK_SORUSU, [PORTFOY], BUGUN).sahipsiz
    belge = cagri("belge_ara", {"sorgu": "Gama AŞ vade"}, {"sonuclar": [
        {"parca_id": "SOZ-S001#m3", "metin": "Ödeme fatura tarihinden itibaren 30 gün içinde yapılır.",
         "belge": "Gama AŞ satış sözleşmesi", "madde": "Ödeme"}]})
    d = dogrula('Ödeme "fatura tarihinden itibaren 30 gün içinde" [SOZ-S001#m3] yapılır.',
                "Gama AŞ sözleşmesine göre ödeme vadesi kaç gün?", [belge], BUGUN)
    assert d.gecerli and not d.sahipsiz


def test_kapsam_disi_ve_olmayan_musteri_ayni_karari_alir():
    """Faz 3 güvencesi: doğrulayıcı veritabanına bakmaz; öteki bölgenin müşterisi ile hiç olmayan bir müşteri aynı
    karar ve aynı düzeltme metnini alır (adlar dışında)."""
    sonuclar = []
    for ad in ("Bright Home Group GmbH", "Hayali Örnek Ticaret GmbH"):
        d = dogrula(f"{ad} müşterisinin bekleyen çekleri var; toplamı 7.674.405,79 TL.",
                    f"{ad} müşterisinin bekleyen çekleri var mı, toplamı ne?", [PORTFOY], BUGUN)
        sonuclar.append((d.gecerli, d.sahipsiz, d.kimliksiz, d.kapsam_eksik,
                         duzeltme_mesaji(cevap("x"), d).replace(ad, "<ad>")))
    assert sonuclar[0] == sonuclar[1] and sonuclar[0][1]


# ---------------------------------------------------------------- kimlik


def test_uydurma_kimlik_yakalanir():
    fatura = cagri("fatura_ara", {"musteri_adi": "Gelincik Hilal Gıda San. ve Tic. Ltd. Şti."}, {"faturalar": [
        {"fatura_id": "F033963", "kaynak_fatura_no": "C560866"}, {"fatura_id": "F033927", "kaynak_fatura_no": "560878"}]})
    soru = "Gelincik Hilal Gıda San. ve Tic. Ltd. Şti. müşterisine kestiğimiz son fatura hangisi?"
    d = dogrula("En son fatura numarası C560878.", soru, [fatura], BUGUN)
    assert d.eski_gecerli and d.kimliksiz == ["C560878"] and not d.gecerli
    assert "C560878" in duzeltme_mesaji(cevap("x"), d)
    assert dogrula("En son fatura C560866 (F033963).", soru, [fatura], BUGUN).gecerli


def test_parca_kimligi_ve_kanun_numarasi_kimlik_sayilmaz():
    assert kimlikler('"otuz gün içinde" [SOZ-S001#m3]', [(16, 29)]) == []
    assert kimlikler("KANUN-6102 ve SOZ-S001 belgesi") == []
    assert kimlikler("F016076 faturası ve C560878") == ["F016076", "C560878"]


def test_sorudaki_kimlik_bilinir():
    assert dogrula("F016076 faturasını bulamadım.", "F016076 faturasının vadesi ne?", [], BUGUN).gecerli


# ---------------------------------------------------------------- kapsam


def test_satis_toplaminda_kapsam_soylenmeli():
    soru = "Bekleyen çeklerimizin toplamı ne?"
    assert dogrula("Bekleyen 295 çekin toplamı 7.674.405,79 TL.", soru, [PORTFOY], BUGUN).kapsam_eksik == \
        YURTICI_KAPSAM
    for metin in ("Yurtiçi müşterilerinizden bekleyen 295 çekin toplamı 7.674.405,79 TL.",
                  "Yurt içi müşterilerin bekleyen 295 çekinin toplamı 7.674.405,79 TL."):
        assert dogrula(metin, soru, [PORTFOY], BUGUN).kapsam_eksik is None
    d = dogrula("Bekleyen 295 çekin toplamı 7.674.405,79 TL.", soru, [PORTFOY], BUGUN)
    assert d.gecerli  # kapsam cevabı reddetmez


def test_kapsam_gerekmeyen_durumlar():
    cfo = cagri("cek_portfoyu", {}, {"cek_sayisi": 295, "toplam_tl": "7674405.79"})
    assert dogrula("Toplam 7.674.405,79 TL.", "Çeklerimiz?", [cfo], BUGUN).kapsam_eksik is None  # tüm şirket
    assert dogrula("2 çek; toplamı 55.275,27 TL.", CEK_SORUSU, [OZET], BUGUN).kapsam_eksik is None  # tek müşteri
    kur = cagri("doviz_kuru", {"para_birimi": "USD"}, {"alis": "41.2345"}, kapsam=YURTICI_KAPSAM)
    assert dogrula("USD alış kuru 41,2345.", "USD kuru?", [kur], BUGUN).kapsam_eksik is None  # müşteri verisi değil
    fatura = cagri("fatura_ara", {"fatura_no": "F016076"}, {"faturalar": [{"fatura_id": "F016076",
                                                                           "tutar_tl": "1200.00"}]},
                   kapsam=YURTICI_KAPSAM)
    assert dogrula("F016076 tutarı 1.200,00 TL.", "F016076 ne kadar?", [fatura], BUGUN).kapsam_eksik is None


def test_bilinen_sinir_soruyu_yankilayan_cevap_kapsam_sozu_sayilir():
    """Bilinen sınır (README Faz 8b): kapsam yalnızca cevapta kapsam sözü var mı diye bakar. Soruyu yankılayan
    "yurtiçi ve yurt dışı birlikte" cümlesi de "yurtiçi" içerdiği için kapsam söylenmiş sayılır; yurtiçi müdürünün
    kendi toplamı şirketin toplamıymış gibi notsuz gösterilir. Tüm şirket soruları bu yüzden elle puanlanır."""
    alacak = cagri("alacak_yaslandirma", {}, {"toplam_tl": "51719292.77"}, kapsam=YURTICI_KAPSAM)
    soru = "Yurtiçi ve yurt dışı birlikte bütün müşterilerimizden ne kadar alacağımız var?"
    d = dogrula("Yurtiçi ve yurt dışı birlikte tüm müşterilerinizden 51.719.292,77 TL alacağınız var.", soru,
                [alacak], BUGUN)
    assert d.gecerli and d.kapsam_eksik is None


def test_kapsam_notu_ret_kelimesi_icermez():
    for metin in KAPSAM_NOTLARI.values():
        assert not any(k in metin.lower() for k in RET_KELIMELERI)


# ---------------------------------------------------------------- orkestratör: kapsam düzeltmesi ve notu


def _kapsamsiz(mesajlar):
    return cevap(f"Açık alacak toplamı {tr(son_arac_sonucu(mesajlar)['sonuc']['toplam_tl'])} TL.")


def test_kapsam_duzeltmesi_tutarsa_duzeltilmis_cevap(sunucu):  # noqa: F811
    def kapsamli(mesajlar):
        toplam = next(m for m in mesajlar if m["role"] == "tool")
        return cevap(f"Yurtiçi müşterilerinizin açık alacak toplamı "
                     f"{tr(json.loads(toplam['content'])['sonuc']['toplam_tl'])} TL.")

    model = SahteModel(arac("alacak_yaslandirma"), _kapsamsiz, kapsamli)
    tur, _, _ = calistir(sunucu, model, kullanici=YURTICI, soru="Toplam açık alacağımız ne kadar?")
    assert tur.durum == "tamam" and tur.cevap.startswith("Yurtiçi") and not tur.kapsam_notu
    assert "yalnızca yurtiçi müşteriler kapsıyor" in model.cagrilar[2][-1]["content"]


def test_kapsam_duzeltmesi_bozulursa_ilk_taslak_notla(sunucu):  # noqa: F811
    model = SahteModel(arac("alacak_yaslandirma"), _kapsamsiz, cevap("Yurtiçi müşterilerinizden 99.999,99 TL."))
    tur, _, _ = calistir(sunucu, model, kullanici=YURTICI, soru="Toplam açık alacağımız ne kadar?")
    ilk = tur.taslaklar[0].strip()
    assert (tur.durum, tur.kapsam_notu) == ("tamam", True)
    assert tur.cevap == ilk + "\n\n" + KAPSAM_NOTLARI[YURTICI_KAPSAM] and "99.999" not in tur.cevap


def test_kapsam_yine_soylenmezse_son_taslak_notla(sunucu):  # noqa: F811
    model = SahteModel(arac("alacak_yaslandirma"), _kapsamsiz, _kapsamsiz)
    tur, _, _ = calistir(sunucu, model, kullanici=YURTICI, soru="Toplam açık alacağımız ne kadar?")
    assert (tur.durum, tur.kapsam_notu, tur.model_cagrisi) == ("tamam", True, 3)
    assert tur.cevap.endswith(KAPSAM_NOTLARI[YURTICI_KAPSAM])


def test_cfo_icin_kapsam_duzeltmesi_yok(sunucu):  # noqa: F811
    model = SahteModel(arac("alacak_yaslandirma"), _kapsamsiz)
    tur, _, _ = calistir(sunucu, model, soru="Toplam açık alacağımız ne kadar?")
    assert (tur.durum, tur.kapsam_notu, tur.model_cagrisi) == ("tamam", False, 2)


def test_sahipsiz_cevap_duzeltilir_ya_da_uretemedim(sunucu):  # noqa: F811
    """Öteki bölgenin müşterisi (Beta GmbH yurt dışı) ve hiç olmayan bir müşteri: aynı akış, aynı mesajlar."""
    akislar = []
    for ad in ("Beta GmbH", "Omega GmbH"):
        def toplam_yaz(mesajlar, ad=ad):
            return cevap(f"{ad} müşterisinin çekleri toplamı {tr(son_arac_sonucu(mesajlar)['sonuc']['toplam_tl'])} TL.")

        model = SahteModel(arac("cek_portfoyu"), toplam_yaz, arac("musteri_ozeti", musteri_adi=ad),
                           cevap(f"{ad} adında bir müşteri bulamadım."))
        tur, _, _ = calistir(sunucu, model, kullanici=YURTICI, soru=f"{ad} müşterisinin bekleyen çekleri var mı?")
        assert tur.durum == "tamam" and tur.cevap == f"{ad} adında bir müşteri bulamadım."
        assert tur.dogrulamalar[0].sahipsiz
        mesajlar = [{k: v for k, v in m.items() if k != "content"} | {"content": str(m.get("content", "")).replace(
            ad, "<ad>")} for m in model.cagrilar[-1][1:]]
        akislar.append([m for m in mesajlar if m["role"] in ("user", "tool")])
    temiz = [[re.sub(r'"cagri_id": "C\w+"', "", m["content"]) for m in a] for a in akislar]
    assert temiz[0] == temiz[1]

    model = SahteModel(arac("cek_portfoyu"), lambda m: cevap(
        f"Beta GmbH çekleri {tr(son_arac_sonucu(m)['sonuc']['toplam_tl'])} TL."), lambda m: cevap(
        f"Beta GmbH çekleri {tr(son_arac_sonucu(m)['sonuc']['toplam_tl'])} TL."))
    tur, _, _ = calistir(sunucu, model, kullanici=YURTICI, soru="Beta GmbH müşterisinin çekleri ne kadar?")
    assert (tur.durum, tur.cevap) == ("uretemedim", METINLER["uretemedim"])
