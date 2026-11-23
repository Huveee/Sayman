"""Sentetik şirketin kalibrasyon bantlarını gerçek kaynaklardan türetir: data/kaynak/kalibrasyon.json

Bantlar üretici ayarlanmadan önce yazılır; kabul testi üretilen tablolardan hesaplanan
değerleri bu bantlarla karşılaştırır.

Kaynaklar:
1. TCMB Sektör Bilançoları (EVDS, kategori 500403 "Oranlar"), "Alacak Devir Hızı",
   seriler TP.SEKBILAC02A.46 (Toptan Ticaret), .G, .GENEL. EVDS'den indirilir (anahtar gerekir).
2. Atradius Payment Practices Barometer, Türkiye 2026 (210 görüşme, 2026 1. ve 2. çeyrek).
   Rakamlar rapordan elle aktarılmıştır (sayfa 4, "Key insights"):
   https://atradius.de/dam/jcr:b1f513aa-5b1e-4ed0-bb09-b1d3fe4eece2/payment-practices-barometer-central-and-eastern-europe-2026-turkiye-en.pdf
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]

from dotenv import dotenv_values  # noqa: E402

SERILER = ["TP.SEKBILAC02A.46", "TP.SEKBILAC02A.G", "TP.SEKBILAC02A.GENEL"]
ADRES = (
    "https://evds3.tcmb.gov.tr/igmevdsms-dis/series={s}&startDate=01-01-2023&endDate=31-12-2025"
    "&type=json&frequency=8"
)
ATRADIUS_URL = (
    "https://atradius.de/dam/jcr:b1f513aa-5b1e-4ed0-bb09-b1d3fe4eece2/"
    "payment-practices-barometer-central-and-eastern-europe-2026-turkiye-en.pdf"
)

# Atradius Türkiye 2026, sayfa 4: "Payment behaviour of B2B customers (12 months)".
# Katılımcıların, geç ödenen B2B fatura payını hangi aralıkta bildirdiği.
GEC_ODEME_KATILIMCI_DAGILIMI = [  # (alt sınır, üst sınır, katılımcı payı)
    (0.00, 0.00, 0.14),
    (0.01, 0.30, 0.32),
    (0.31, 0.60, 0.35),
    (0.61, 1.00, 0.19),
]
# Aynı sayfa: "Breakdown of past due payments", vadesi geçen faturaların gecikme süresine göre dağılımı.
GECIKME_DAGILIMI = {"1-30": 0.53, "31-60": 0.24, "61-90": 0.12, "90+": 0.11}
GECIKME_DAGILIMI_TOLERANS = 0.10


def main() -> int:
    for akis in (sys.stdout, sys.stderr):
        akis.reconfigure(encoding="utf-8", errors="replace")
    anahtar = (dotenv_values(KOK / ".env").get("EVDS_API_KEY") or "").strip()
    if not anahtar:
        print("EVDS_API_KEY .env dosyasında boş.", file=sys.stderr)
        return 2
    url = ADRES.format(s="-".join(SERILER))
    istek = urllib.request.Request(url, headers={"key": anahtar, "User-Agent": "sayman-faz1"})
    with urllib.request.urlopen(istek, timeout=60) as yanit:
        veri = json.loads(yanit.read().decode("utf-8"))
    devir = {}
    for kayit in veri["items"]:
        yil = str(kayit["Tarih"])
        for seri in SERILER:
            deger = kayit.get(seri.replace(".", "_"))
            if deger not in (None, ""):
                devir.setdefault(seri, {})[yil] = float(deger)

    toptan = devir["TP.SEKBILAC02A.46"]
    dso_yillik = {yil: round(365 / v, 2) for yil, v in sorted(toptan.items())}
    dso_alt = round(min(dso_yillik.values()) * 0.8, 1)
    dso_ust = round(max(dso_yillik.values()) * 1.2, 1)

    gec_alt = round(sum(a * p for a, _, p in GEC_ODEME_KATILIMCI_DAGILIMI), 3)
    gec_ust = round(sum(u * p for _, u, p in GEC_ODEME_KATILIMCI_DAGILIMI), 3)
    gec_orta = round(sum((a + u) / 2 * p for a, u, p in GEC_ODEME_KATILIMCI_DAGILIMI), 3)

    sonuc = {
        "olusturulma": dt.date.today().isoformat(),
        "dso_gun": {
            "kaynak": "TCMB Sektör Bilançoları, Alacak Devir Hızı, NACE 46 Toptan Ticaret (EVDS)",
            "seriler": devir,
            "toptan_ticaret_dso_gun": dso_yillik,
            "bant": [dso_alt, dso_ust],
            "turetme": (
                "DSO = 365 / alacak devir hızı. Bant, 2024-2025 toptan ticaret değerlerinin en küçüğünün %80'i "
                "ile en büyüğünün %120'si arası. Pay, tek bir şirketin sektör ortalamasından sapabilmesi için "
                "bırakılmış bir tasarım kararıdır."
            ),
            "hesap": "365 x (yıl sonu açık ticari alacak) / (yıllık net satış), TRY karşılıklarıyla",
        },
        "gec_odenen_fatura_orani": {
            "kaynak": "Atradius Payment Practices Barometer Türkiye 2026, sayfa 4",
            "url": ATRADIUS_URL,
            "katilimci_dagilimi": [
                {"aralik": [a, u], "katilimci_payi": p} for a, u, p in GEC_ODEME_KATILIMCI_DAGILIMI
            ],
            "metindeki_ifade": "over one third of invoices overdue",
            "bant": [gec_alt, gec_ust],
            "nokta_tahmini": gec_orta,
            "turetme": (
                "Katılımcı payıyla ağırlıklı ortalama, aralıkların alt sınırları kullanılınca en az, "
                "üst sınırları kullanılınca en çok bu değeri alır; bant bu iki değerdir."
            ),
            "hesap": "Vadesinden 180 gün geçmiş satış faturalarından vadesinden sonra kapanan ya da hiç kapanmayanların payı (adet)",
        },
        "gecikme_dagilimi": {
            "kaynak": "Atradius Payment Practices Barometer Türkiye 2026, sayfa 4, Breakdown of past due payments",
            "url": ATRADIUS_URL,
            "paylar": GECIKME_DAGILIMI,
            "tolerans": GECIKME_DAGILIMI_TOLERANS,
            "turetme": "Her gecikme diliminin payı kaynaktaki paydan en çok 10 puan sapabilir (tasarım kararı).",
            "hesap": "Geç kapanan faturaların, son tahsilatın vadeden kaç gün sonra geldiğine göre dağılımı",
        },
        "karsiliksiz_cek_orani": {
            "kaynak": None,
            "not": "EVDS3'te karşılıksız çek serisi bulunamadı (yalnızca tahsile alınan senetler var). Oran sentetik varsayımdır.",
        },
    }
    cikti = KOK / "data" / "kaynak" / "kalibrasyon.json"
    cikti.parent.mkdir(parents=True, exist_ok=True)
    cikti.write_text(json.dumps(sonuc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"DSO bandı: {dso_alt}-{dso_ust} gün (toptan ticaret DSO: {dso_yillik})")
    print(f"Geç ödenen fatura bandı: {gec_alt}-{gec_ust} (nokta {gec_orta})")
    print(f"Yazıldı: {cikti}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
