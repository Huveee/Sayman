"""Faz 8 eşleştirmesi: iyileşen/kötüleşen sayımı, iyi yönün metriğe göre değişmesi, McNemar ve tutarlılık denetimi."""

import pytest

from sayman.olcum import eslestirme


def _arac(sid, dogru, yanlis_sayi=False):
    return {"soru_id": sid, "bolum": "arac", "arac_dogru": dogru, "parametre_dogru": dogru, "cevap_dogru": dogru,
            "yanlis_sayi_suphesi": yanlis_sayi}


def _yetkisiz(sid, reddetti, sizinti=False):
    return {"soru_id": sid, "bolum": "yetkisiz", "reddetti": reddetti, "sizinti": sizinti}


@pytest.mark.parametrize("iyi,kotu,p", [(0, 0, None), (0, 5, 0.0625), (5, 0, 0.0625), (1, 9, 0.0215), (3, 3, 1.0)])
def test_mcnemar_kesin_degerler(iyi, kotu, p):
    assert eslestirme.mcnemar_p(iyi, kotu) == p


def test_iyilesen_ve_kotulesen_sayilir_gruplar_ayri():
    temel = [_arac("a1", False), _arac("a2", False), _arac("a3", True), _arac("a4", True)]
    ayarli = [_arac("a1", True), _arac("a2", False), _arac("a3", False), _arac("a4", True)]
    g = {"a1": "egitimde_yok", "a2": "egitimde_yok", "a3": "egitimde_var", "a4": "egitimde_var"}
    e = eslestirme.eslestir(temel, list(reversed(ayarli)), g)  # sıra önemli değil
    m = e["bolum"]["arac"]["metrikler"]["cevap_dogrulugu"]
    assert (m["temel"], m["ayarli"], m["iyilesen"], m["kotulesen"]) == (0.5, 0.5, 1, 1)
    yok = e["bolum"]["arac"]["gruplar"]["egitimde_yok"]["metrikler"]["cevap_dogrulugu"]
    assert (yok["n"], yok["iyilesen"], yok["kotulesen"]) == (2, 1, 0)
    degisen = {(d["soru_id"], d["metrik"], d["yon"]) for d in e["degisenler"]}
    assert ("a1", "cevap_dogrulugu", "iyilesti") in degisen and ("a3", "cevap_dogrulugu", "kotulesti") in degisen


def test_yanlis_sayi_ve_sizintida_hayir_iyidir():
    temel = [_arac("a1", True, yanlis_sayi=True), _yetkisiz("y1", True, sizinti=True)]
    ayarli = [_arac("a1", True, yanlis_sayi=False), _yetkisiz("y1", True, sizinti=False)]
    e = eslestirme.eslestir(temel, ayarli)
    ys = e["bolum"]["arac"]["metrikler"]["dogrulanmis_yanlis_sayi"]
    sz = e["bolum"]["yetkisiz"]["metrikler"]["sizinti"]
    assert (ys["temel"], ys["ayarli"], ys["iyilesen"], ys["kotulesen"]) == (1.0, 0.0, 1, 0)
    assert (sz["iyilesen"], sz["kotulesen"]) == (1, 0)


def test_farkli_soru_kumesi_ya_da_bolum_reddedilir():
    with pytest.raises(ValueError, match="sorular farklı"):
        eslestirme.eslestir([_arac("a1", True)], [_arac("a2", True)])
    with pytest.raises(ValueError, match="bölümü farklı"):
        eslestirme.eslestir([_arac("a1", True)], [{**_yetkisiz("a1", True)}])
    with pytest.raises(ValueError, match="iki kez"):
        eslestirme.eslestir([_arac("a1", True), _arac("a1", False)], [_arac("a1", True)])


def test_hedef_tablosu_esik_farkini_yakalar():
    h = [{"ad": "x", "metrik": ["arac", "cevap_dogrulugu"], "yon": "en_az", "esik": 0.75, "deger": 0.8,
          "gecti": True}]
    satir = eslestirme.hedef_tablosu(h, [{**h[0], "deger": 0.7, "gecti": False}])[0]
    assert (satir["temel"], satir["ayarli"], satir["ayarli_gecti"]) == (0.8, 0.7, False)
    with pytest.raises(ValueError, match="tanımı farklı"):
        eslestirme.hedef_tablosu(h, [{**h[0], "esik": 0.7}])
