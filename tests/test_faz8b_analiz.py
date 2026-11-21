"""Faz 8b analizi: eski düzenin sonucunu aynı koşunun kayıtlarından çıkarma (scripts/faz8b_analiz.py)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from faz8b_analiz import eski_kayit  # noqa: E402


def taslak(metin, eski_gecerli=True, cagri_no=2, arac_cagrisi=1, kesildi=False, sahipsiz=False, kimliksiz=(),
           kapsam_eksik=None):
    return {"metin": metin, "gecerli": eski_gecerli and not sahipsiz and not kimliksiz, "eski_gecerli": eski_gecerli,
            "kaynaksiz": [] if eski_gecerli else ["1"], "gecersiz_alinti": [], "alinti_eksik": False,
            "kimliksiz": list(kimliksiz), "sahipsiz": sahipsiz, "kapsam_eksik": kapsam_eksik, "cagri_no": cagri_no,
            "arac_cagrisi": arac_cagrisi, "kesildi": kesildi}


def kayit(taslaklar, durum="tamam", cevap="son", cagrilar=2, sureler=(5.0, 10.0, 20.0, 30.0)):
    return {"soru_id": "X", "durum": durum, "cevap": cevap, "sure_s": sum(sureler) + 1.0,
            "cagrilar": [{"arac": f"a{i}", "argumanlar": {}, "hata": None, "yanit": {}} for i in range(cagrilar)],
            "taslaklar": taslaklar, "tokenlar": [{"istem": 1, "uretim": 1, "sure_s": s} for s in sureler[:4]],
            "kapsam_notu": False}


def test_ilk_taslak_iki_duzende_de_gecerse_ayni_sonuc():
    k = kayit([taslak("Cevap 1", cagri_no=2)], cevap="Cevap 1", sureler=(5.0, 10.0))
    eski, ayrisma = eski_kayit(k)
    assert ayrisma is None and eski["cevap"] == "Cevap 1" and eski["sure_s"] == k["sure_s"]


def test_eski_duzen_yeni_kuralin_takildigi_taslakta_durur():
    k = kayit([taslak("Portföy toplamı 7 milyon", sahipsiz=True, cagri_no=2, arac_cagrisi=1),
               taslak("Müşterinin 2 çeki var", cagri_no=4, arac_cagrisi=2)], cevap="Müşterinin 2 çeki var")
    eski, ayrisma = eski_kayit(k)
    assert ayrisma is None
    assert eski["cevap"] == "Portföy toplamı 7 milyon" and eski["durum"] == "tamam"
    assert len(eski["cagrilar"]) == 1 and len(eski["taslaklar"]) == 1
    assert eski["sure_s"] == k["sure_s"] - 20.0 - 30.0  # 3. ve 4. model çağrıları eski düzende yok


def test_kapsam_eksik_taslak_eski_duzende_notsuz_gosterilir():
    k = kayit([taslak("Toplam 51 milyon", kapsam_eksik="yalnızca yurtiçi müşteriler")], cevap="Toplam 51 milyon\n\nNot")
    k["kapsam_notu"] = True
    eski, _ = eski_kayit(k)
    assert eski["cevap"] == "Toplam 51 milyon" and eski["kapsam_notu"] is False


def test_once_yeni_kural_da_takildiysa_ayrisan():
    # 1. taslağı eski kurallar da reddetti ama yeni kural düzeltme mesajına satır ekledi: sonrası karşılaştırılamaz
    k = kayit([taslak("Yanlış 1", eski_gecerli=False, sahipsiz=True), taslak("Cevap", cagri_no=3)], cevap="Cevap")
    eski, ayrisma = eski_kayit(k)
    assert eski is None and ayrisma.startswith("1. taslak")


def test_eski_kurallarin_reddettigi_ama_yeni_kuralin_dokunmadigi_taslak_ayni_yoldan_gider():
    k = kayit([taslak("Yanlış 1", eski_gecerli=False), taslak("Cevap", cagri_no=3, arac_cagrisi=2)], cevap="Cevap")
    eski, ayrisma = eski_kayit(k)
    assert ayrisma is None and eski["cevap"] == "Cevap" and eski["sure_s"] == k["sure_s"] - 30.0


def test_bos_ya_da_kesik_taslakta_eski_duzen_durmaz():
    k = kayit([taslak("", cagri_no=2), taslak("yarım", kesildi=True, cagri_no=3), taslak("Cevap", cagri_no=4)],
              cevap="Cevap")
    eski, ayrisma = eski_kayit(k)
    assert ayrisma is None and eski["cevap"] == "Cevap"


def test_hic_kabul_yoksa_yeni_sonuc_aynen():
    k = kayit([taslak("Yanlış", eski_gecerli=False), taslak("Yanlış 2", eski_gecerli=False, cagri_no=3)],
              durum="uretemedim", cevap="üretemedim metni")
    eski, ayrisma = eski_kayit(k)
    assert ayrisma is None and eski is k
