"""As-of görünüm: verinin belli bir gündeki hâli.

Tablolarda yalnızca tarihli olgular saklanır (fatura kesildi, tahsilat geldi, çek tahsile verildi).
Durumlar (fatura açık mı, çek nerede) bu olgulardan, verilen güne kadar olanlar kullanılarak türetilir.
Böylece "şimdi"den sonraki hiçbir kayıt araçlara sızmaz.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict

# tablo -> görünürlüğü belirleyen tarih sütunu (None: ana kayıt, her zaman görünür)
TARIH_SUTUNU = {
    "musteriler": "ilk_islem_tarihi",
    "faturalar": "fatura_tarihi",
    "pos_islemleri": "islem_tarihi",
    "tahsilatlar": "tarih",
    "cekler": "alis_tarihi",
    "cek_hareketleri": "tarih",
    "tedarikciler": None,
    "alis_faturalari": "tarih",
    "odemeler": "tarih",
    "banka_hesaplari": "acilis_tarihi",
    "banka_hareketleri": "tarih",
    "doviz_kurlari": "tarih",
}


def gorunum(tablolar: dict[str, list[dict]], tarih: dt.date) -> dict[str, list[dict]]:
    """Verilen gün sonuna kadar gerçekleşmiş kayıtlar."""
    sonuc = {}
    for ad, satirlar in tablolar.items():
        sutun = TARIH_SUTUNU[ad]
        sonuc[ad] = list(satirlar) if sutun is None else [s for s in satirlar if s[sutun] <= tarih]
    return sonuc


def acik_tutarlar(tablolar: dict[str, list[dict]], tarih: dt.date) -> dict[str, int]:
    """Satış faturalarının verilen gündeki açık tutarı (fatura para biriminde, kuruş).
    Açık = tutar - eşleşen tahsilatlar - bağlı iade faturalarının uygulanan kısmı."""
    g = gorunum(tablolar, tarih)
    acik = {f["fatura_id"]: f["tutar_kurus"] for f in g["faturalar"] if f["tur"] == "satis"}
    for t in g["tahsilatlar"]:
        if t["fatura_id"] in acik:
            acik[t["fatura_id"]] -= t["tutar_kurus"]
    for f in g["faturalar"]:
        if f["tur"] == "iade" and f["iliskili_fatura_id"] in acik:
            acik[f["iliskili_fatura_id"]] -= f["uygulanan_kurus"] or 0
    return acik


def kapanis_tarihleri(tablolar: dict[str, list[dict]], tarih: dt.date) -> dict[str, dt.date | None]:
    """Her satış faturasının açık tutarının sıfıra indiği gün (verilen güne kadar kapanmadıysa None)."""
    hareket = defaultdict(list)
    for t in tablolar["tahsilatlar"]:
        if t["fatura_id"] and t["tarih"] <= tarih:
            hareket[t["fatura_id"]].append((t["tarih"], t["tutar_kurus"]))
    for f in tablolar["faturalar"]:
        if f["tur"] == "iade" and f["iliskili_fatura_id"] and f["fatura_tarihi"] <= tarih:
            hareket[f["iliskili_fatura_id"]].append((f["fatura_tarihi"], f["uygulanan_kurus"] or 0))
    sonuc = {}
    for f in tablolar["faturalar"]:
        if f["tur"] != "satis" or f["fatura_tarihi"] > tarih:
            continue
        kalan, kapanis = f["tutar_kurus"], f["fatura_tarihi"] if f["tutar_kurus"] <= 0 else None
        for gun, tutar in sorted(hareket[f["fatura_id"]]):
            if kapanis is not None:
                break
            kalan -= tutar
            if kalan <= 0:
                kapanis = gun
        sonuc[f["fatura_id"]] = kapanis
    return sonuc


def cek_durumlari(tablolar: dict[str, list[dict]], tarih: dt.date) -> dict[str, str]:
    """Her çekin verilen gündeki son durumu (portfoyde, tahsilde, tahsil_edildi, karsiliksiz)."""
    son: dict[str, tuple] = {}
    for h in tablolar["cek_hareketleri"]:
        if h["tarih"] <= tarih and (h["cek_id"] not in son or (h["tarih"], h["cek_hareket_id"]) > son[h["cek_id"]][:2]):
            son[h["cek_id"]] = (h["tarih"], h["cek_hareket_id"], h["durum"])
    return {c: v[2] for c, v in son.items()}


def banka_bakiyeleri(tablolar: dict[str, list[dict]], tarih: dt.date) -> dict[str, int]:
    """Her hesabın verilen gün sonundaki bakiyesi: açılış + o güne kadarki hareketler."""
    bakiye = {h["hesap_id"]: h["acilis_bakiye_kurus"] for h in tablolar["banka_hesaplari"] if h["acilis_tarihi"] <= tarih}
    for s in tablolar["banka_hareketleri"]:
        if s["tarih"] <= tarih:
            bakiye[s["hesap_id"]] += s["tutar_kurus"]
    return bakiye
