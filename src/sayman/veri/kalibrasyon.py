"""Kalibrasyon ölçümleri: üretilen tablolardan hesaplanır, data/kaynak/kalibrasyon.json bantlarıyla karşılaştırılır.

Bantlar üretici ayarlanmadan önce gerçek kaynaklardan yazıldı (scripts/faz1_kalibrasyon_kaynaklari.py).
Ayar yapılırken bantlar değişmez; yalnızca üretici ayarları değişir.
"""

from __future__ import annotations

import bisect
import datetime as dt
from decimal import Decimal

from sayman.veri.gorunum import acik_tutarlar, kapanis_tarihleri
from sayman.veri.para import kurusa, kurustan

GECIKME_DILIMLERI = (("1-30", 1, 30), ("31-60", 31, 60), ("61-90", 61, 90), ("90+", 91, 10**6))


def _kur_bulucu(tablolar):
    seriler: dict[str, tuple[list, list]] = {}
    for k in sorted(tablolar["doviz_kurlari"], key=lambda k: (k["para_birimi"], k["tarih"])):
        t, d = seriler.setdefault(k["para_birimi"], ([], []))
        t.append(k["tarih"])
        d.append(Decimal(k["alis"]))

    def kur(pb: str, tarih: dt.date) -> Decimal:
        if pb == "TRY":
            return Decimal(1)
        t, d = seriler[pb]
        return d[max(0, bisect.bisect_right(t, tarih) - 1)]

    return kur


def acik_alacak_try(tablolar, tarih: dt.date) -> int:
    """Verilen gündeki açık ticari alacak (TRY kuruş). Açık tutar faturanın kuruyla çevrilir;
    hangi faturaya ait olduğu bilinmeyen tahsilatlar alacaktan düşülür (müşteri hesabına alacak kaydı)."""
    kur = _kur_bulucu(tablolar)
    fatura_kuru = {f["fatura_id"]: Decimal(f["kur"]) for f in tablolar["faturalar"]}
    toplam = sum(kurusa(kurustan(a) * fatura_kuru[fid]) for fid, a in acik_tutarlar(tablolar, tarih).items() if a > 0)
    for t in tablolar["tahsilatlar"]:
        if t["fatura_id"] is None and t["tarih"] <= tarih:
            toplam -= kurusa(kurustan(t["tutar_kurus"]) * kur(t["para_birimi"], t["tarih"]))
    return toplam


def net_satis_try(tablolar, bas: dt.date, bit: dt.date) -> int:
    """Satış ve iade faturaları ile POS satışlarının TRY toplamı."""
    faturali = sum(f["tutar_try_kurus"] for f in tablolar["faturalar"] if bas <= f["fatura_tarihi"] <= bit)
    pos = sum(p["tutar_kurus"] for p in tablolar["pos_islemleri"] if bas <= p["islem_tarihi"] <= bit)
    return faturali + pos


def dso(tablolar, yil: int) -> float:
    """365 x (yıl sonu açık ticari alacak) / (yıllık net satış)."""
    alacak = acik_alacak_try(tablolar, dt.date(yil, 12, 31))
    satis = net_satis_try(tablolar, dt.date(yil, 1, 1), dt.date(yil, 12, 31))
    return round(365 * alacak / satis, 2)


def _olgun_faturalar(tablolar, bitis: dt.date, bekleme: int):
    return [f for f in tablolar["faturalar"]
            if f["tur"] == "satis" and f["vade_tarihi"] + dt.timedelta(days=bekleme) <= bitis]


def gec_odeme_orani(tablolar, bitis: dt.date, bekleme: int = 180) -> tuple[float, int]:
    """Vadesinden en az `bekleme` gün geçmiş satış faturalarından vadeden sonra kapanan ya da hiç
    kapanmayanların payı (adet). (oran, fatura sayısı) döner."""
    kapanis = kapanis_tarihleri(tablolar, bitis)
    olgun = _olgun_faturalar(tablolar, bitis, bekleme)
    gec = sum(1 for f in olgun if kapanis[f["fatura_id"]] is None or kapanis[f["fatura_id"]] > f["vade_tarihi"])
    return (round(gec / len(olgun), 4) if olgun else 0.0), len(olgun)


def gecikme_dagilimi(tablolar, bitis: dt.date, bekleme: int = 180) -> tuple[dict[str, float], int]:
    """Geç kapanan faturaların, kapanış gününün vadeden kaç gün sonra olduğuna göre dağılımı."""
    kapanis = kapanis_tarihleri(tablolar, bitis)
    gunler = []
    for f in _olgun_faturalar(tablolar, bitis, bekleme):
        k = kapanis[f["fatura_id"]]
        if k is not None and k > f["vade_tarihi"]:
            gunler.append((k - f["vade_tarihi"]).days)
    paylar = {ad: round(sum(1 for g in gunler if alt <= g <= ust) / len(gunler), 4) if gunler else 0.0
              for ad, alt, ust in GECIKME_DILIMLERI}
    return paylar, len(gunler)


def olc(tablolar, bitis: dt.date, dso_yili: int) -> dict:
    oran, n = gec_odeme_orani(tablolar, bitis)
    dagilim, n_gec = gecikme_dagilimi(tablolar, bitis)
    return {"dso_gun": dso(tablolar, dso_yili), "dso_yili": dso_yili, "gec_odenen_fatura_orani": oran,
            "olgun_fatura_sayisi": n, "gecikme_dagilimi": dagilim, "gec_kapanan_fatura_sayisi": n_gec}


def bantlarla_karsilastir(olcum: dict, kalibrasyon: dict) -> list[dict]:
    """Her ölçüm için {ad, deger, alt, ust, tamam} satırları."""
    sonuc = []
    alt, ust = kalibrasyon["dso_gun"]["bant"]
    sonuc.append({"ad": "dso_gun", "deger": olcum["dso_gun"], "alt": alt, "ust": ust, "tamam": alt <= olcum["dso_gun"] <= ust})
    alt, ust = kalibrasyon["gec_odenen_fatura_orani"]["bant"]
    d = olcum["gec_odenen_fatura_orani"]
    sonuc.append({"ad": "gec_odenen_fatura_orani", "deger": d, "alt": alt, "ust": ust, "tamam": alt <= d <= ust})
    tolerans = kalibrasyon["gecikme_dagilimi"]["tolerans"]
    for dilim, pay in kalibrasyon["gecikme_dagilimi"]["paylar"].items():
        d = olcum["gecikme_dagilimi"][dilim]
        alt, ust = round(pay - tolerans, 4), round(pay + tolerans, 4)
        sonuc.append({"ad": f"gecikme_{dilim}", "deger": d, "alt": alt, "ust": ust, "tamam": alt <= d <= ust})
    return sonuc
