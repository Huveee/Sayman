"""Üretilen tabloların şeması, CSV olarak yazılıp okunması ve içerik özeti (hash).

Özet dosya baytlarından değil içerikten hesaplanır: satırlar birincil anahtara göre
sıralanır, sütunlar sabit sırada yazılır. Böylece satır sonu farkları (Windows/Linux)
özeti değiştirmez.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
from pathlib import Path

# tablo -> sütunlar (ilk sütun birincil anahtar)
SEMA: dict[str, list[str]] = {
    "musteriler": ["musteri_id", "ad", "ulke", "yurtici", "para_birimi", "vade_gun", "ilk_islem_tarihi"],
    "faturalar": [
        "fatura_id", "kaynak_fatura_no", "tur", "musteri_id", "fatura_tarihi", "vade_tarihi", "para_birimi",
        "tutar_kurus", "kur", "tutar_try_kurus", "iliskili_fatura_id", "iade_eslesme", "uygulanan_kurus",
        "kalem_sayisi",
    ],
    "pos_islemleri": ["pos_id", "kaynak_fatura_no", "islem_tarihi", "tutar_kurus", "komisyon_kurus", "valor_tarihi"],
    "tahsilatlar": [
        "tahsilat_id", "musteri_id", "fatura_id", "tarih", "para_birimi", "tutar_kurus", "yontem", "cek_id",
        "banka_hesap_id", "eslesme",
    ],
    "cekler": ["cek_id", "musteri_id", "cek_no", "kesideci_banka", "alis_tarihi", "vade_tarihi", "tutar_kurus"],
    "cek_hareketleri": ["cek_hareket_id", "cek_id", "tarih", "durum"],
    "tedarikciler": ["tedarikci_id", "ad", "kategori", "vade_gun"],
    "alis_faturalari": ["alis_fatura_id", "tedarikci_id", "tarih", "vade_tarihi", "tutar_kurus", "kategori"],
    "odemeler": [
        "odeme_id", "tur", "tedarikci_id", "alis_fatura_id", "musteri_id", "iade_fatura_id", "tarih", "tutar_kurus", "yontem",
        "banka_hesap_id", "aciklama",
    ],
    "banka_hesaplari": ["hesap_id", "banka", "para_birimi", "acilis_tarihi", "acilis_bakiye_kurus"],
    "banka_hareketleri": [
        "hareket_id", "hesap_id", "tarih", "tutar_kurus", "bakiye_kurus", "aciklama", "kaynak_tur", "kaynak_id",
    ],
    "doviz_kurlari": ["kur_id", "tarih", "para_birimi", "alis"],
}

TAM_SAYI_SUTUNLARI = {
    s for sutunlar in SEMA.values() for s in sutunlar if s.endswith("_kurus") or s in {"vade_gun", "kalem_sayisi"}
}
MANTIKSAL_SUTUNLAR = {"yurtici"}


def _metin(deger) -> str:
    if deger is None:
        return ""
    if isinstance(deger, bool):
        return "1" if deger else "0"
    if isinstance(deger, (dt.date, dt.datetime)):
        return deger.isoformat()
    return str(deger)


def tablo_hash(ad: str, satirlar: list[dict]) -> str:
    sutunlar = SEMA[ad]
    h = hashlib.sha256()
    h.update(("\t".join(sutunlar) + "\n").encode("utf-8"))
    for satir in sorted(satirlar, key=lambda s: s[sutunlar[0]]):
        h.update(("\t".join(_metin(satir.get(s)) for s in sutunlar) + "\n").encode("utf-8"))
    return h.hexdigest()


def veri_hash(tablolar: dict[str, list[dict]]) -> tuple[str, dict[str, str]]:
    tablo_hashleri = {ad: tablo_hash(ad, tablolar[ad]) for ad in sorted(SEMA)}
    h = hashlib.sha256()
    for ad, th in tablo_hashleri.items():
        h.update(f"{ad}:{th}\n".encode("utf-8"))
    return h.hexdigest(), tablo_hashleri


def dosya_hash(yol: Path) -> str:
    h = hashlib.sha256()
    with open(yol, "rb") as f:
        for parca in iter(lambda: f.read(1 << 20), b""):
            h.update(parca)
    return h.hexdigest()


def csv_yaz(tablolar: dict[str, list[dict]], klasor: Path) -> None:
    klasor.mkdir(parents=True, exist_ok=True)
    for ad, sutunlar in SEMA.items():
        with open(klasor / f"{ad}.csv", "w", encoding="utf-8", newline="") as f:
            yazici = csv.writer(f, lineterminator="\n")
            yazici.writerow(sutunlar)
            for satir in sorted(tablolar[ad], key=lambda s: s[sutunlar[0]]):
                yazici.writerow([_metin(satir.get(s)) for s in sutunlar])


def _cevir(sutun: str, deger: str):
    if deger == "":
        return None
    if sutun in TAM_SAYI_SUTUNLARI:
        return int(deger)
    if sutun in MANTIKSAL_SUTUNLAR:
        return deger == "1"
    if sutun.endswith("tarihi") or sutun == "tarih":
        return dt.date.fromisoformat(deger)
    return deger


def csv_oku(klasor: Path) -> dict[str, list[dict]]:
    tablolar = {}
    for ad, sutunlar in SEMA.items():
        with open(klasor / f"{ad}.csv", encoding="utf-8", newline="") as f:
            tablolar[ad] = [{s: _cevir(s, satir[s]) for s in sutunlar} for satir in csv.DictReader(f)]
    return tablolar
