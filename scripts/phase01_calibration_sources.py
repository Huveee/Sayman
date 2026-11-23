"""Derives the calibration bands of the synthetic company from real sources: data/kaynak/kalibrasyon.json

The bands are written before the generator is tuned; the acceptance test compares the values computed from the
generated tables with these bands.

Sources:
1. CBRT (TCMB) Sector Balance Sheets (EVDS, category 500403 "Oranlar" = ratios), "Alacak Devir Hızı"
   (receivables turnover), series TP.SEKBILAC02A.46 (Wholesale Trade), .G, .GENEL. Downloaded from EVDS
   (an API key is required).
2. Atradius Payment Practices Barometer, Turkey 2026 (210 interviews, 2026 Q1 and Q2).
   The figures were transcribed from the report by hand (page 4, "Key insights"):
   https://atradius.de/dam/jcr:b1f513aa-5b1e-4ed0-bb09-b1d3fe4eece2/payment-practices-barometer-central-and-eastern-europe-2026-turkiye-en.pdf
"""

from __future__ import annotations

import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from dotenv import dotenv_values  # noqa: E402

SERIES = ["TP.SEKBILAC02A.46", "TP.SEKBILAC02A.G", "TP.SEKBILAC02A.GENEL"]
EVDS_URL_TEMPLATE = (
    "https://evds3.tcmb.gov.tr/igmevdsms-dis/series={s}&startDate=01-01-2023&endDate=31-12-2025"
    "&type=json&frequency=8"
)
ATRADIUS_URL = (
    "https://atradius.de/dam/jcr:b1f513aa-5b1e-4ed0-bb09-b1d3fe4eece2/"
    "payment-practices-barometer-central-and-eastern-europe-2026-turkiye-en.pdf"
)

# Atradius Turkey 2026, page 4: "Payment behaviour of B2B customers (12 months)".
# The range in which respondents reported their share of late-paid B2B invoices.
LATE_PAYMENT_RESPONDENT_DISTRIBUTION = [  # (lower bound, upper bound, respondent share)
    (0.00, 0.00, 0.14),
    (0.01, 0.30, 0.32),
    (0.31, 0.60, 0.35),
    (0.61, 1.00, 0.19),
]
# Same page: "Breakdown of past due payments", overdue invoices by length of delay.
DELAY_DISTRIBUTION = {"1-30": 0.53, "31-60": 0.24, "61-90": 0.12, "90+": 0.11}
DELAY_DISTRIBUTION_TOLERANCE = 0.10


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    api_key = (dotenv_values(ROOT / ".env").get("EVDS_API_KEY") or "").strip()
    if not api_key:
        print("EVDS_API_KEY .env dosyasında boş.", file=sys.stderr)
        return 2
    url = EVDS_URL_TEMPLATE.format(s="-".join(SERIES))
    request = urllib.request.Request(url, headers={"key": api_key, "User-Agent": "sayman-faz1"})
    with urllib.request.urlopen(request, timeout=60) as response:
        data = json.loads(response.read().decode("utf-8"))
    turnover = {}
    for record in data["items"]:
        year = str(record["Tarih"])
        for series in SERIES:
            value = record.get(series.replace(".", "_"))
            if value not in (None, ""):
                turnover.setdefault(series, {})[year] = float(value)

    wholesale = turnover["TP.SEKBILAC02A.46"]
    dso_by_year = {year: round(365 / v, 2) for year, v in sorted(wholesale.items())}
    dso_lower = round(min(dso_by_year.values()) * 0.8, 1)
    dso_upper = round(max(dso_by_year.values()) * 1.2, 1)

    late_lower = round(sum(a * p for a, _, p in LATE_PAYMENT_RESPONDENT_DISTRIBUTION), 3)
    late_upper = round(sum(u * p for _, u, p in LATE_PAYMENT_RESPONDENT_DISTRIBUTION), 3)
    late_mid = round(sum((a + u) / 2 * p for a, u, p in LATE_PAYMENT_RESPONDENT_DISTRIBUTION), 3)

    result = {
        "olusturulma": dt.date.today().isoformat(),
        "dso_gun": {
            "kaynak": "TCMB Sektör Bilançoları, Alacak Devir Hızı, NACE 46 Toptan Ticaret (EVDS)",
            "seriler": turnover,
            "toptan_ticaret_dso_gun": dso_by_year,
            "bant": [dso_lower, dso_upper],
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
                {"aralik": [a, u], "katilimci_payi": p} for a, u, p in LATE_PAYMENT_RESPONDENT_DISTRIBUTION
            ],
            "metindeki_ifade": "over one third of invoices overdue",
            "bant": [late_lower, late_upper],
            "nokta_tahmini": late_mid,
            "turetme": (
                "Katılımcı payıyla ağırlıklı ortalama, aralıkların alt sınırları kullanılınca en az, "
                "üst sınırları kullanılınca en çok bu değeri alır; bant bu iki değerdir."
            ),
            "hesap": "Vadesinden 180 gün geçmiş satış faturalarından vadesinden sonra kapanan ya da hiç kapanmayanların payı (adet)",
        },
        "gecikme_dagilimi": {
            "kaynak": "Atradius Payment Practices Barometer Türkiye 2026, sayfa 4, Breakdown of past due payments",
            "url": ATRADIUS_URL,
            "paylar": DELAY_DISTRIBUTION,
            "tolerans": DELAY_DISTRIBUTION_TOLERANCE,
            "turetme": "Her gecikme diliminin payı kaynaktaki paydan en çok 10 puan sapabilir (tasarım kararı).",
            "hesap": "Geç kapanan faturaların, son tahsilatın vadeden kaç gün sonra geldiğine göre dağılımı",
        },
        "karsiliksiz_cek_orani": {
            "kaynak": None,
            "not": "EVDS3'te karşılıksız çek serisi bulunamadı (yalnızca tahsile alınan senetler var). Oran sentetik varsayımdır.",
        },
    }
    output = ROOT / "data" / "kaynak" / "kalibrasyon.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"DSO bandı: {dso_lower}-{dso_upper} gün (toptan ticaret DSO: {dso_by_year})")
    print(f"Geç ödenen fatura bandı: {late_lower}-{late_upper} (nokta {late_mid})")
    print(f"Yazıldı: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
