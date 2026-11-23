"""Araç çağrısı geçerlilik kontrolü ve seçim kuralı için birim testleri (sahte veriyle)."""

from __future__ import annotations

import pytest

from sayman.model_selection.call_validation import (
    arac_cagrisi_gecerli_mi,
    beklenen_cagri_mi,
    cevap_gecerli_mi,
    secim_kurali,
)


def _gecerli(ad, argumanlar):
    return arac_cagrisi_gecerli_mi({"ad": ad, "argumanlar": argumanlar})


def test_dogru_cagri_gecerli():
    assert _gecerli("geciken_alacaklar", {"min_gun": 60}) == (True, "geçerli")


def test_argumansiz_arac_bos_argumanla_gecerli():
    assert _gecerli("nakit_pozisyonu", {})[0]
    assert _gecerli("nakit_pozisyonu", None)[0]


def test_json_metni_olarak_gelen_argumanlar_cozulur():
    assert _gecerli("doviz_kuru", '{"para_birimi": "USD"}')[0]


@pytest.mark.parametrize(
    "ad, argumanlar, neden_parcasi",
    [
        ("para_transferi_yap", {}, "bilinmeyen araç"),
        ("geciken_alacaklar", {}, "eksik zorunlu alan"),
        ("geciken_alacaklar", {"min_gun": "60"}, "tamsayı olmalı"),
        ("geciken_alacaklar", {"min_gun": True}, "tamsayı olmalı"),
        ("doviz_kuru", {"para_birimi": "dolar"}, "izin verilmeyen değer"),
        ("doviz_kuru", {"para_birimi": "USD", "banka": "X"}, "tanımsız alan"),
        ("doviz_kuru", {"para_birimi": "USD", "tarih": "12.11.2026"}, "YYYY-MM-DD"),
        ("doviz_kuru", {"para_birimi": "USD", "tarih": "2026-02-30"}, "geçerli bir tarih değil"),
        ("doviz_kuru", "{para_birimi: USD}", "JSON olarak okunamadı"),
        ("doviz_kuru", "[1, 2]", "JSON nesnesi değil"),
    ],
)
def test_gecersiz_cagrilar_yakalanir(ad, argumanlar, neden_parcasi):
    gecerli, neden = _gecerli(ad, argumanlar)
    assert not gecerli
    assert neden_parcasi in neden


def test_cagri_yoksa_gecersiz():
    assert cevap_gecerli_mi([]) == (False, "araç çağrısı yok")


def test_birden_fazla_cagride_biri_bozuksa_gecersiz():
    cagrilar = [
        {"ad": "nakit_pozisyonu", "argumanlar": {}},
        {"ad": "geciken_alacaklar", "argumanlar": {}},
    ]
    assert not cevap_gecerli_mi(cagrilar)[0]


def test_beklenen_cagri_arac_ve_argumanlara_bakar():
    cagrilar = [{"ad": "geciken_alacaklar", "argumanlar": {"min_gun": 60, "en_fazla": 10}}]
    assert beklenen_cagri_mi(cagrilar, "geciken_alacaklar", {"min_gun": 60})
    assert not beklenen_cagri_mi(cagrilar, "geciken_alacaklar", {"min_gun": 30})
    assert not beklenen_cagri_mi(cagrilar, "alacak_yaslandirma", {})


def _aday(model, milyar, sureler, gecerli_sayisi):
    turlar = [{"sure_s": s, "gecerli": i < gecerli_sayisi} for i, s in enumerate(sureler)]
    return {"model": model, "parametre_milyar": milyar, "turlar": turlar}


def test_kurali_saglayan_en_buyuk_model_secilir():
    adaylar = [
        _aday("kucuk", 4, [5, 6, 5, 7, 6], 5),
        _aday("buyuk", 9, [20, 25, 22, 30, 21], 5),
    ]
    assert secim_kurali(adaylar) == "buyuk"


def test_tek_turu_esigi_asan_model_elenir():
    adaylar = [
        _aday("kucuk", 4, [5, 6, 5, 7, 6], 5),
        _aday("buyuk", 9, [20, 25, 22, 30.5, 21], 5),
    ]
    assert secim_kurali(adaylar) == "kucuk"


def test_bes_soruda_bes_gecerli_olmayan_elenir():
    adaylar = [
        _aday("kucuk", 4, [5, 6, 5, 7, 6], 5),
        _aday("buyuk", 9, [10, 10, 10, 10, 10], 4),
    ]
    assert secim_kurali(adaylar) == "kucuk"


def test_eksik_tur_olan_elenir():
    assert secim_kurali([_aday("eksik", 9, [5, 5, 5, 5], 4)]) is None


def test_kimse_gecmezse_secim_yok():
    adaylar = [_aday("kucuk", 4, [31, 6, 5, 7, 6], 5), _aday("buyuk", 9, [5, 5, 5, 5, 5], 3)]
    assert secim_kurali(adaylar) is None
