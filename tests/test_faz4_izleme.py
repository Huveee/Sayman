"""Faz 4 birim testleri: puanlama kuralları, olasılık hesaplamaları, durum başlangıçları, uyarı kimliği, depo."""

import datetime as dt
import itertools
import json
import math

import faz4_puanlama as puan
import pytest

from sayman.izleme.kurallar import (Ayarlar, baslangiclar, poisson_binom_kuyruk, poisson_kuyruk, tl_metin, uyari,
                                    yuzdelik)
from sayman.izleme.zamanlayici import Depo, DepoHatasi, calistir
from sayman.veri.saat import Saat

G = dt.date.fromisoformat
BITIS = G("2026-11-12")


def _uyari(kural, gun, varlik, onem="uyari", kaynak=None):
    return uyari(kural, G(gun), onem, "x", varlik, "b", "a", {}, {}, kaynak or {})


def _cek_olayi(olay_id, tarih, cek_id):
    return {"olay_id": olay_id, "tur": "karsiliksiz_cek", "tarih": tarih, "cek_id": cek_id}


# ---------------------------------------------------------------- puanlama


def test_dogru_kural_kimlik_ve_pencere_yakalar():
    p = puan.puanla([_uyari("karsiliksiz_cek", "2025-09-02", "C1")], [_cek_olayi("E1", "2025-09-01", "C1")], BITIS)
    assert p["yakalanan"] == 1 and p["olaylar"][0]["gun_sonra"] == 1 and p["olaya_denk_gelmeyen_alarm"] == 0


def test_yanlis_kural_yakalamaz():
    p = puan.puanla([_uyari("mukerrer_odeme", "2025-09-01", "C1")], [_cek_olayi("E1", "2025-09-01", "C1")], BITIS)
    assert p["yakalanan"] == 0 and p["olaya_denk_gelmeyen_alarm"] == 1


def test_yanlis_kimlik_yakalamaz():
    p = puan.puanla([_uyari("karsiliksiz_cek", "2025-09-01", "C2")], [_cek_olayi("E1", "2025-09-01", "C1")], BITIS)
    assert p["yakalanan"] == 0


def test_kaynaktaki_kimlik_de_sayilir():
    u = _uyari("mukerrer_odeme", "2025-06-13", "A1", kaynak={"odemeler": ["O2"]})
    o = {"olay_id": "E1", "tur": "mukerrer_odeme", "tarih": "2025-06-13", "mukerrer_odeme_id": "O2",
         "asil_odeme_id": "O1", "alis_fatura_id": "A9"}
    assert puan.puanla([u], [o], BITIS)["yakalanan"] == 1


@pytest.mark.parametrize("gun", ["2025-08-31", "2025-09-05"])
def test_pencere_disi_yakalamaz(gun):
    p = puan.puanla([_uyari("karsiliksiz_cek", gun, "C1")], [_cek_olayi("E1", "2025-09-01", "C1")], BITIS)
    assert p["yakalanan"] == 0


def test_bir_uyari_tek_olay_yakalar():
    olaylar = [_cek_olayi("E1", "2025-09-01", "C1"), _cek_olayi("E2", "2025-09-02", "C1")]
    p = puan.puanla([_uyari("karsiliksiz_cek", "2025-09-02", "C1")], olaylar, BITIS)
    assert p["yakalanan"] == 1 and [r["yakalandi"] for r in p["olaylar"]] == [True, False]


def test_bilgi_ne_yakalar_ne_yanlis_alarm_sayilir():
    u = [_uyari("karsiliksiz_cek", "2025-09-01", "C1", onem="bilgi")]
    assert puan.puanla(u, [_cek_olayi("E1", "2025-09-01", "C1")], BITIS)["yakalanan"] == 0
    y = puan.yanlis_alarm(u, G("2025-01-01"), G("2025-12-31"))
    assert y["alarm"] == 0 and y["bilgi"] == 1


def test_pencere_veri_bitisinde_kesilir():
    o = {"olay_id": "E1", "tur": "buyuk_musteri_gecikmesi", "tarih": "2026-10-20", "musteri_id": "M1",
         "gecikme_gun": 80}
    assert puan.pencere(o, BITIS) == (G("2026-10-20"), BITIS)


def test_aylik_yanlis_alarm():
    u = [_uyari("nakit_esigi", f"2025-{a:02d}-01", "A_TRY") for a in range(1, 7)]
    y = puan.yanlis_alarm(u, G("2025-01-01"), G("2025-12-31"))
    assert y["alarm"] == 6 and y["ay"] == pytest.approx(12.0, abs=0.01) and y["aylik"] == pytest.approx(0.5, abs=1e-3)


# ---------------------------------------------------------------- olasılıklar ve yardımcılar


@pytest.mark.parametrize("n,lam", [(0, 2.0), (1, 0.5), (5, 1.3), (12, 3.0)])
def test_poisson_kuyrugu(n, lam):
    beklenen = 1 - sum(math.exp(-lam) * lam ** k / math.factorial(k) for k in range(n))
    assert poisson_kuyruk(n, lam) == pytest.approx(beklenen, abs=1e-12)


def test_poisson_binom_kuyrugu_tum_durumlarla_ayni():
    ps = [0.1, 0.5, 0.03, 0.7, 0.25]
    for k in range(len(ps) + 1):
        beklenen = sum(math.prod(p if x else 1 - p for p, x in zip(ps, durum))
                       for durum in itertools.product((0, 1), repeat=len(ps)) if sum(durum) >= k)
        assert poisson_binom_kuyruk(k, ps) == pytest.approx(beklenen, abs=1e-12)


def test_poisson_binom_esit_olasilikta_binom():
    n, p, k = 10, 0.2, 4
    beklenen = sum(math.comb(n, j) * p ** j * (1 - p) ** (n - j) for j in range(k, n + 1))
    assert poisson_binom_kuyruk(k, [p] * n) == pytest.approx(beklenen, abs=1e-12)


def test_durum_baslangiclari():
    gunler = [G("2025-01-01"), G("2025-01-02"), G("2025-01-09"), G("2025-01-20"), G("2025-01-21")]
    assert baslangiclar(gunler, 7) == [G("2025-01-01"), G("2025-01-20")]
    assert baslangiclar(gunler, 10) == [G("2025-01-01"), G("2025-01-20")]  # 9 → 20 Ocak: 11 gün ara
    assert baslangiclar(gunler, 11) == [G("2025-01-01")]
    assert baslangiclar([], 7) == []


def test_yuzdelik():
    assert yuzdelik(list(range(100)), 0.95) == 95
    assert yuzdelik([5], 0.75) == 5


def test_tl_metni():
    assert tl_metin(123456789) == "1.234.567,89 TL"
    assert tl_metin(-5) == "-0,05 TL"


def test_uyari_kimligi_kural_varlik_ve_gune_bagli():
    a = _uyari("nakit_esigi", "2025-06-01", "A_TRY")
    assert a["uyari_id"] == _uyari("nakit_esigi", "2025-06-01", "A_TRY")["uyari_id"]
    assert a["uyari_id"] != _uyari("nakit_esigi", "2025-06-02", "A_TRY")["uyari_id"]
    assert a["uyari_id"] != _uyari("buyuk_cikis", "2025-06-01", "A_TRY")["uyari_id"]


def test_kaynak_kimlikleri_sirali():
    u = _uyari("mukerrer_odeme", "2025-06-01", "A1", kaynak={"odemeler": ["O9", "O1"]})
    assert u["kaynak"]["odemeler"] == ["O1", "O9"]


# ---------------------------------------------------------------- depo


def _kucuk_tablolar():
    """Kuralların hiçbirinin uyarı vermediği en küçük veri: bir fatura, bir hesap."""
    bos = {ad: [] for ad in ("musteriler", "pos_islemleri", "tahsilatlar", "cekler", "cek_hareketleri",
                             "tedarikciler", "alis_faturalari", "odemeler", "banka_hareketleri", "doviz_kurlari")}
    return {**bos,
            "faturalar": [{"fatura_id": "F1", "tur": "satis", "musteri_id": "M1", "fatura_tarihi": G("2025-01-01"),
                           "vade_tarihi": G("2025-01-31"), "tutar_kurus": 100, "tutar_try_kurus": 100,
                           "iliskili_fatura_id": None, "uygulanan_kurus": None}],
            "banka_hesaplari": [{"hesap_id": "A_TRY", "acilis_tarihi": G("2025-01-01"),
                                 "acilis_bakiye_kurus": 10**9}]}


def test_depo_ayni_saatte_bir_sey_eklemez(tmp_path):
    t, depo = _kucuk_tablolar(), Depo(tmp_path)
    calistir(t, Saat(G("2025-03-01"), BITIS), depo)
    once = (depo.uyari_yolu.read_bytes() if depo.uyari_yolu.exists() else b"", depo.durum_yolu.read_bytes())
    sonuc = calistir(t, Saat(G("2025-03-01"), BITIS), depo)
    assert sonuc["yeni"] == []
    assert (depo.uyari_yolu.read_bytes() if depo.uyari_yolu.exists() else b"", depo.durum_yolu.read_bytes()) == once


def test_depo_saat_geri_alinamaz(tmp_path):
    t, depo = _kucuk_tablolar(), Depo(tmp_path)
    calistir(t, Saat(G("2025-03-01"), BITIS), depo)
    with pytest.raises(DepoHatasi):
        calistir(t, Saat(G("2025-02-01"), BITIS), depo)


def test_depo_baska_ayarla_devam_etmez(tmp_path):
    t, depo = _kucuk_tablolar(), Depo(tmp_path)
    calistir(t, Saat(G("2025-03-01"), BITIS), depo)
    with pytest.raises(DepoHatasi):
        calistir(t, Saat(G("2025-04-01"), BITIS), depo, Ayarlar(gecikme_p=0.05))


def test_depoda_duvar_saati_yok(tmp_path):
    t, depo = _kucuk_tablolar(), Depo(tmp_path)
    calistir(t, Saat(G("2025-03-01"), BITIS), depo)
    durum = json.loads(depo.durum_yolu.read_text(encoding="utf-8"))
    assert set(durum) == {"son_gun", "ayarlar"} and durum["son_gun"] == "2025-03-01"
