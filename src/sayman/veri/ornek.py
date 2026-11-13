"""Testler için küçük örnek veri: UCI biçiminde ham satırlar ve sentetik kurlar, kod içinde üretilir.

Gerçek dosyadaki tuhaflıkları taşır: iki sayfa ve çakışan günler, C ile başlayan iptal faturaları,
müşterisiz (perakende) satırlar, A düzeltme faturası, satış dışı kodlar, sıfır fiyat, eksi miktarlı
depo düzeltmesi, artı miktarlı iptal ve okunamayan satır. İndirme gerektirmez.
"""

from __future__ import annotations

import datetime as dt
import random
from decimal import Decimal

from sayman.veri.kurlar import Kurlar

UCI_BAS = dt.datetime(2009, 12, 1, 8, 26)
UCI_BIT = dt.datetime(2011, 12, 9, 12, 50)
SAYFA_1, SAYFA_2 = "Year 2009-2010", "Year 2010-2011"
SAYFA_1_SON = dt.datetime(2010, 12, 9, 23, 59)
SAYFA_2_ILK = dt.datetime(2010, 12, 1)
ULKELER = (("United Kingdom", 0.80), ("Germany", 0.06), ("France", 0.05), ("EIRE", 0.03), ("USA", 0.03),
           ("Australia", 0.03))

# Beklenen atılma sayıları (ornek_ham_satirlar'ın eklediği özel satırlar).
OZEL_SATIRLAR = {"duzeltme_faturasi": 1, "satis_disi_kod": 6, "gecersiz_fiyat": 1, "stok_duzeltmesi": 1,
                 "tutarsiz_iade": 1, "okunamayan_satir": 1}


def ornek_kurlar(bas: dt.date = dt.date(2024, 9, 2), bit: dt.date = dt.date(2026, 11, 30)) -> Kurlar:
    """İş günlerinde yavaşça artan sentetik GBP, EUR ve USD alış kurları."""
    satirlar, d, i = [], bas, 0
    while d <= bit:
        if d.weekday() < 5:
            gbp = Decimal("44") + Decimal(i) * Decimal("0.025")
            for pb, k in (("GBP", gbp), ("EUR", gbp * Decimal("0.84")), ("USD", gbp * Decimal("0.77"))):
                satirlar.append((d, pb, k.quantize(Decimal("0.0001"))))
            i += 1
        d += dt.timedelta(days=1)
    return Kurlar(satirlar)


def _satir(sayfa, fatura, stok, miktar, tarih, fiyat, musteri, ulke) -> dict:
    return {"sayfa": sayfa, "Invoice": fatura, "StockCode": stok, "Description": f"URUN {stok}",
            "Quantity": str(miktar), "InvoiceDate": tarih if isinstance(tarih, str) else tarih.isoformat(sep=" "),
            "Price": str(fiyat), "Customer ID": musteri, "Country": ulke}


def ornek_ham_satirlar(tohum: int = 7, musteri_sayisi: int = 80) -> list[dict]:
    rng = random.Random(f"ornek:{tohum}")
    sure = int((UCI_BIT - UCI_BAS).total_seconds())
    stoklar = [str(20000 + 37 * i) for i in range(150)]
    fiyat = {s: Decimal(rng.randint(29, 1275)) / 100 for s in stoklar}

    def zaman(alt: dt.datetime = UCI_BAS) -> dt.datetime:
        z = alt + dt.timedelta(seconds=rng.randint(0, int((UCI_BIT - alt).total_seconds())))
        z = z.replace(hour=rng.randint(8, 18), second=0)
        return min(max(z, UCI_BAS), UCI_BIT)

    basliklar = []  # (zaman, müşteri, ülke, iptal mi, satırlar)
    for i in range(musteri_sayisi):
        musteri = str(12346 + i)
        r, toplam, ulke = rng.random(), 0.0, ULKELER[-1][0]
        for u, p in ULKELER:
            toplam += p
            if r < toplam:
                ulke = u
                break
        ilk = UCI_BAS + dt.timedelta(seconds=rng.randint(0, sure // 2))
        for _ in range(min(120, int(rng.expovariate(1 / 18)) + 1)):
            satirlar = [(s, rng.randint(1, 48)) for s in rng.sample(stoklar, rng.randint(1, 12))]
            z = zaman(ilk)
            basliklar.append((z, musteri, ulke, False, satirlar))
            if rng.random() < 0.08:
                iade = [(s, -rng.randint(1, q)) for s, q in satirlar[: rng.randint(1, 2)]]
                basliklar.append((min(z + dt.timedelta(days=rng.randint(1, 20)), UCI_BIT), musteri, ulke, True, iade))
    for _ in range(musteri_sayisi * 2):  # müşterisiz perakende satışlar ve birkaç iadesi
        satirlar = [(s, rng.randint(1, 6)) for s in rng.sample(stoklar, rng.randint(1, 5))]
        z = zaman()
        basliklar.append((z, "", "United Kingdom", False, satirlar))
        if rng.random() < 0.05:
            basliklar.append((z + dt.timedelta(hours=2), "", "United Kingdom", True, [(satirlar[0][0], -1)]))
    # Uç noktalar: ilk ve son gün, ve ilk işlemi iade olan bir müşteri (bağlanacak fatura yok).
    basliklar.append((UCI_BAS, "12346", "United Kingdom", False, [(stoklar[0], 6)]))
    basliklar.append((UCI_BIT, "12347", "United Kingdom", False, [(stoklar[1], 12)]))
    basliklar.append((dt.datetime(2010, 3, 1, 10), str(12346 + musteri_sayisi), "United Kingdom", True,
                      [(stoklar[2], -3)]))
    # Yan hesap açıkları: büyük bir POS iadesi (B_TRY'yi eksiye iter) ve ilk işlemi büyük bir iade olan
    # bir avro müşterisi (geri ödemesi ay başında B_EUR'dan çıkar). İkisi de A_TRY'den karşılanmalı.
    basliklar.append((dt.datetime(2010, 7, 6, 11), "", "United Kingdom", True, [(stoklar[7], -400)]))
    basliklar.append((dt.datetime(2010, 6, 1, 10), str(12347 + musteri_sayisi), "Germany", True,
                      [(stoklar[8], -2000)]))

    ham, no = [], 489434
    for z, musteri, ulke, iptal, satirlar in sorted(basliklar, key=lambda b: (b[0], b[1], b[3])):
        fatura = ("C" if iptal else "") + str(no)
        no += 1
        for stok, miktar in satirlar:
            kayit = (fatura, stok, miktar, z, fiyat[stok], musteri, ulke)
            if z <= SAYFA_1_SON:
                ham.append(_satir(SAYFA_1, *kayit))
            if z >= SAYFA_2_ILK:  # çakışan günler iki sayfada da var
                ham.append(_satir(SAYFA_2, *kayit))

    t = dt.datetime(2011, 6, 1, 10)
    ham += [
        _satir(SAYFA_2, "A563185", "B", 1, t, "11062.06", "", "United Kingdom"),
        _satir(SAYFA_2, "C563186", "AMAZONFEE", -1, t, "5575.28", "", "United Kingdom"),
        _satir(SAYFA_2, "563187", "BANK CHARGES", 1, t, "15.00", "12350", "United Kingdom"),
        # Elle düzeltme çifti (tutar bir kayıttan iptal edilip müşterisiz yeniden girilir), numune ve komisyon.
        _satir(SAYFA_2, "C563192", "M", -1, t, "2511.09", "12351", "United Kingdom"),
        _satir(SAYFA_2, "563193", "M", 1, t, "2511.09", "", "United Kingdom"),
        _satir(SAYFA_2, "C563194", "S", -1, t, "60.65", "", "United Kingdom"),
        _satir(SAYFA_2, "C563195", "CRUK", -1, t, "79.33", "12351", "United Kingdom"),
        _satir(SAYFA_2, "563188", stoklar[3], 4, t, "0", "12350", "United Kingdom"),
        _satir(SAYFA_2, "563189", stoklar[4], -24, t, "0.85", "", "United Kingdom"),
        _satir(SAYFA_2, "C563190", stoklar[5], 2, t, "1.25", "12350", "United Kingdom"),
        _satir(SAYFA_2, "563191", stoklar[6], 2, "tarih yok", "1.25", "12350", "United Kingdom"),
    ]
    return ham
