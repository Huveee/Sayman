"""Monitoring rules: each looks at the data and decides "has a new problem started today".

Every rule works on the records up to a day; it does not see anything after that day (the scheduler gives the same
result as if it had run every day using only the data up to that day). While the same situation continues the alert is
not repeated daily; it is raised once, on the day the situation started: if the situation stops for a while (the merge
gap, `merge_gap` of `onset_days`) and starts again, it is a new alert.

Six rules (the rule names are data keys and stay in Turkish):
- `nakit_esigi` (rule): the lowest intraday balance of the main TL account fell below the safety threshold (10% of
  the average monthly sales of the last 12 months).
- `buyuk_cikis` (rule + statistic): an outflow without an invoice, to a counterparty not paid before, larger than the
  95th percentile of the supplier payments of the last year.
- `mukerrer_odeme` (rule): a supplier invoice was paid more than its amount.
- `karsiliksiz_cek` (rule + statistic): a customer cheque bounced. "uyari" if its amount is in the top quartile of the
  cheques of the last year, otherwise "bilgi".
- `eslesmeyen_birikim` (statistic): the number of collections without a known invoice in the last 14 days is too high
  to be explained by chance, against the average of a past year (Poisson, p < 0.001).
- `musteri_gecikmesi` (statistic): of the invoices paid by transfer by one of the 20 largest customers of the last 12
  months, too many stayed open to be explained by chance, given the customer's own payment habit (Poisson-binomial, p < 0.01).

The thresholds are in `RuleSettings`; every trial is written to reports/faz4_ayar_denemeleri.json.
"""

from __future__ import annotations

import bisect
import datetime as dt
import hashlib
import heapq
import math
from collections import defaultdict
from dataclasses import asdict, dataclass

from sayman.data.as_of import closing_dates

DAY = dt.timedelta(days=1)
DAYS_PER_MONTH = 30.4375


@dataclass(frozen=True)
class RuleSettings:
    isinma_gun: int = 30  # no alert in the first this-many days of the data: there is no history to compare with
    gecmis_gun: int = 365  # the history the statistics look at
    # nakit_esigi
    nakit_hesabi: str = "A_TRY"
    asgari_nakit_orani: float = 0.10  # this share of the average monthly net sales of the last 12 months (safety threshold)
    nakit_birlestirme: int = 7
    # buyuk_cikis
    cikis_yuzdeligi: float = 0.95
    cikis_en_az_gecmis: int = 20
    # karsiliksiz_cek
    cek_yuzdeligi: float = 0.75
    cek_en_az_gecmis: int = 20
    # eslesmeyen_birikim
    birikim_pencere: int = 14
    birikim_p: float = 0.001
    birikim_en_az: int = 5
    birikim_en_az_gecmis: int = 60
    birikim_birlestirme: int = 14
    # musteri_gecikmesi
    buyuk_musteri_sayisi: int = 20
    gecikme_pencere: int = 45  # invoices whose due date fell in the last this-many days are tested
    gecikme_p: float = 0.01
    gecikme_fatura_sayisi: int = 2
    gecikme_en_az_gecmis: int = 10  # at least this many invoices paid by transfer in the base
    gecikme_birlestirme: int = 30

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- helpers


def format_tl(cents: int) -> str:
    k = abs(int(cents))
    whole_part = f"{k // 100:,}".replace(",", ".")
    return f"{'-' if cents < 0 else ''}{whole_part},{k % 100:02d} TL"


def plain_tl(cents: int) -> str:
    k = int(cents)
    return f"{'-' if k < 0 else ''}{abs(k) // 100}.{abs(k) % 100:02d}"


def format_number(x: float, digits: int) -> str:
    """Number with a decimal comma: 0.00047 -> "0,00047"."""
    return f"{x:.{digits}f}".replace(".", ",")


def percentile(values: list[int], ratio: float) -> int:
    s = sorted(values)
    return s[min(int(len(s) * ratio), len(s) - 1)]


def poisson_tail(n: int, lam: float) -> float:
    """P(X >= n), X ~ Poisson(lam)."""
    if n <= 0:
        return 1.0
    term, total = math.exp(-lam), 0.0
    for k in range(n):
        total += term
        term *= lam / (k + 1)
    return max(0.0, 1.0 - total)


def poisson_binomial_tail(k: int, probabilities: list[float]) -> float:
    """P(X >= k), X: the number of independent events, each occurring with its own probability."""
    distribution = [1.0] + [0.0] * len(probabilities)
    for p in probabilities:
        for j in range(len(distribution) - 1, 0, -1):
            distribution[j] = distribution[j] * (1 - p) + distribution[j - 1] * p
        distribution[0] *= 1 - p
    return min(1.0, sum(distribution[k:]))


def iter_days(start: dt.date, end: dt.date):
    g = start
    while g <= end:
        yield g
        g += DAY


def make_alert(rule: str, day: dt.date, severity: str, entity_kind: str, entity_id: str, title: str, description: str,
          measurement: dict, threshold: dict, source: dict, customer_id: str | None = None) -> dict:
    digest = hashlib.sha256(f"{rule}|{entity_id}|{day.isoformat()}".encode("utf-8")).hexdigest()[:12]
    return {"uyari_id": f"U{digest}", "kural": rule, "gun": day.isoformat(), "onem": severity,
            "varlik": {"tur": entity_kind, "id": entity_id}, "musteri_id": customer_id, "baslik": title,
            "aciklama": description, "olcum": measurement, "esik": threshold, "kaynak": {k: sorted(v) for k, v in source.items()}}


def onset_days(condition_days, merge_gap: int) -> list[dt.date]:
    """From the days on which the condition is true, the days on which the situation started: if the condition was not true
    at all within the previous `merge_gap` days, that day is a new onset. It only looks at past days."""
    result, previous = [], None
    for g in sorted(condition_days):
        if previous is None or (g - previous).days > merge_gap:
            result.append(g)
        previous = g
    return result


# ---------------------------------------------------------------- data


class VisibleData:
    """Records visible up to a day (end) and the indexes shared by the rules."""

    def __init__(self, tables: dict[str, list[dict]], start: dt.date, end: dt.date):
        self.t, self.start, self.end = tables, start, end
        self.customer_names = {m["musteri_id"]: m["ad"] for m in tables["musteriler"]}
        self.supplier_names = {t["tedarikci_id"]: t["ad"] for t in tables["tedarikciler"]}
        self.daily_sales: dict[dt.date, int] = defaultdict(int)  # TL, net (returns negative), POS included
        self.customer_daily_sales: dict[dt.date, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for f in tables["faturalar"]:
            self.daily_sales[f["fatura_tarihi"]] += f["tutar_try_kurus"]
            self.customer_daily_sales[f["fatura_tarihi"]][f["musteri_id"]] += f["tutar_try_kurus"]
        for p in tables["pos_islemleri"]:
            self.daily_sales[p["islem_tarihi"]] += p["tutar_kurus"]

    def period_label(self, g: dt.date, history_days: int) -> str:
        """The history a rule looks at: the text "son 12 ayın" if a full year is available, otherwise as many months as there are
    ("son N ayın")."""
        day = (g - self.start).days
        return "son 12 ayın" if day >= history_days else f"son {max(1, round(day / DAYS_PER_MONTH))} ayın"


# ---------------------------------------------------------------- rules


def cash_threshold(v: VisibleData, a: RuleSettings, start: dt.date, end: dt.date) -> list[dict]:
    account = next(h for h in v.t["banka_hesaplari"] if h["hesap_id"] == a.nakit_hesabi)
    daily = defaultdict(list)
    for h in v.t["banka_hareketleri"]:
        if h["hesap_id"] == a.nakit_hesabi:
            daily[h["tarih"]].append(h)
    closing, window_total = account["acilis_bakiye_kurus"], 0
    hit_days, measurements = [], {}
    for g in iter_days(v.start, end):
        window_total += v.daily_sales.get(g, 0)
        old_day = g - a.gecmis_gun * DAY
        window_total -= v.daily_sales.get(old_day, 0)
        rows = daily.get(g, [])
        lowest = min([closing] + [s["bakiye_kurus"] for s in rows])
        closing += sum(s["tutar_kurus"] for s in rows)
        if g < account["acilis_tarihi"] or (g - v.start).days < a.isinma_gun:
            continue
        day_count = min(a.gecmis_gun, (g - v.start).days + 1)
        monthly = window_total / day_count * DAYS_PER_MONTH
        threshold = int(monthly * a.asgari_nakit_orani)
        if lowest < threshold:
            hit_days.append(g)
            measurements[g] = (lowest, threshold, int(monthly), [s["hareket_id"] for s in rows])
    result = []
    for g in onset_days(hit_days, a.nakit_birlestirme):
        if not start <= g <= end:
            continue
        lowest, threshold, monthly, movements = measurements[g]
        result.append(make_alert(
            "nakit_esigi", g, "uyari", "banka_hesabi", a.nakit_hesabi, "Ana hesapta nakit güvenlik eşiğinin altına indi",
            f"{a.nakit_hesabi} hesabının gün içi en düşük bakiyesi {format_tl(lowest)}; güvenlik eşiği "
            f"{format_tl(threshold)} ({v.period_label(g, a.gecmis_gun)} ortalama aylık satışı {format_tl(monthly)}'nin %{a.asgari_nakit_orani * 100:g}'u).",
            {"en_dusuk_bakiye_tl": plain_tl(lowest)}, {"guvenlik_esigi_tl": plain_tl(threshold), "ortalama_aylik_satis_tl": plain_tl(monthly)},
            {"banka_hareketleri": movements}))
    return result


def large_outflow(v: VisibleData, a: RuleSettings, start: dt.date, end: dt.date) -> list[dict]:
    payments = sorted(v.t["odemeler"], key=lambda o: (o["tarih"], o["odeme_id"]))
    supplier_payments = [(o["tarih"], o["tutar_kurus"]) for o in payments if o["tur"] == "tedarikci"]
    supplier_days = [t for t, _ in supplier_payments]
    seen: dict[str, dt.date] = {}  # description -> day first seen
    for o in payments:
        seen.setdefault(o["aciklama"], o["tarih"])
    result = []
    for o in payments:
        g = o["tarih"]
        if o["tur"] != "diger" or not start <= g <= end or (g - v.start).days < a.isinma_gun:
            continue
        if seen[o["aciklama"]] < g:  # this counterparty was paid before
            continue
        i, j = bisect.bisect_left(supplier_days, g - a.gecmis_gun * DAY), bisect.bisect_left(supplier_days, g)
        history = [t for _, t in supplier_payments[i:j]]
        if len(history) < a.cikis_en_az_gecmis:
            continue
        threshold = percentile(history, a.cikis_yuzdeligi)
        if o["tutar_kurus"] < threshold:
            continue
        result.append(make_alert(
            "buyuk_cikis", g, "uyari", "odeme", o["odeme_id"], "Yeni bir karşı tarafa olağandışı büyük ödeme",
            f"{format_tl(o['tutar_kurus'])} tutarında, bir faturaya bağlı olmayan ödeme ({o['aciklama']}). Bu karşı "
            f"tarafa daha önce ödeme yapılmamış; tutar {v.period_label(g, a.gecmis_gun)} tedarikçi ödemelerinin %95'inden "
            f"({format_tl(threshold)}) büyük.",
            {"tutar_tl": plain_tl(o["tutar_kurus"]), "kat": f"{o['tutar_kurus'] / threshold:.2f}"},
            {"tedarikci_odemeleri_yuzde_95_tl": plain_tl(threshold)}, {"odemeler": [o["odeme_id"]]}))
    return result


def duplicate_payment(v: VisibleData, a: RuleSettings, start: dt.date, end: dt.date) -> list[dict]:
    invoices = {f["alis_fatura_id"]: f for f in v.t["alis_faturalari"]}
    payments = defaultdict(list)
    for o in v.t["odemeler"]:
        if o["alis_fatura_id"]:
            payments[o["alis_fatura_id"]].append(o)
    result = []
    for invoice_id, entries in sorted(payments.items()):
        f = invoices.get(invoice_id)
        if f is None:
            continue
        total, used = 0, []
        for o in sorted(entries, key=lambda o: (o["tarih"], o["odeme_id"])):
            total += o["tutar_kurus"]
            used.append(o["odeme_id"])
            if total > f["tutar_kurus"]:
                g = max(o["tarih"], f["tarih"])  # if the invoice becomes visible after the payment, it is understood on that day
                if start <= g <= end:
                    result.append(make_alert(
                        "mukerrer_odeme", g, "uyari", "alis_faturasi", invoice_id, "Tedarikçi faturasına fazla ödeme",
                        f"{v.supplier_names.get(f['tedarikci_id'], f['tedarikci_id'])} tedarikçisinin {invoice_id} numaralı "
                        f"{format_tl(f['tutar_kurus'])} tutarındaki faturasına toplam "
                        f"{format_tl(total)} ödendi ({len(used)} ödeme); fazla ödenen {format_tl(total - f['tutar_kurus'])}.",
                        {"odenen_tl": plain_tl(total), "fazla_tl": plain_tl(total - f["tutar_kurus"])},
                        {"fatura_tutari_tl": plain_tl(f["tutar_kurus"])},
                        {"alis_faturalari": [invoice_id], "odemeler": used}))
                break
    return result


def bounced_cheque(v: VisibleData, a: RuleSettings, start: dt.date, end: dt.date) -> list[dict]:
    cheques = {c["cek_id"]: c for c in v.t["cekler"]}
    received_cheques = sorted((c["alis_tarihi"], c["tutar_kurus"]) for c in v.t["cekler"])
    received_days = [t for t, _ in received_cheques]
    result = []
    for h in sorted(v.t["cek_hareketleri"], key=lambda h: (h["tarih"], h["cek_hareket_id"])):
        g = h["tarih"]
        if h["durum"] != "karsiliksiz" or not start <= g <= end:
            continue
        c = cheques[h["cek_id"]]
        i, j = bisect.bisect_left(received_days, g - (a.gecmis_gun - 1) * DAY), bisect.bisect_right(received_days, g)
        history = [t for _, t in received_cheques[i:j]]
        threshold = percentile(history, a.cek_yuzdeligi) if len(history) >= a.cek_en_az_gecmis else None
        severity = "uyari" if threshold is None or c["tutar_kurus"] >= threshold else "bilgi"
        threshold_text = f"{v.period_label(g, a.gecmis_gun)} çeklerinin üst çeyreği {format_tl(threshold)}'den başlıyor" if threshold else \
            "karşılaştıracak yeterli çek yok"
        result.append(make_alert(
            "karsiliksiz_cek", g, severity, "cek", c["cek_id"], "Müşteri çeki karşılıksız çıktı",
            f"{v.customer_names.get(c['musteri_id'], c['musteri_id'])} müşterisinin {c['kesideci_banka']} çeki ({format_tl(c['tutar_kurus'])}, vade "
            f"{c['vade_tarihi'].isoformat()}) karşılıksız çıktı; {threshold_text}.",
            {"tutar_tl": plain_tl(c["tutar_kurus"])}, {"ust_ceyrek_tl": plain_tl(threshold) if threshold else None},
            {"cekler": [c["cek_id"]], "cek_hareketleri": [h["cek_hareket_id"]], "musteriler": [c["musteri_id"]]},
            customer_id=c["musteri_id"]))
    return result


def unmatched_buildup(v: VisibleData, a: RuleSettings, start: dt.date, end: dt.date) -> list[dict]:
    unmatched = sorted((t["tarih"], t["tahsilat_id"]) for t in v.t["tahsilatlar"] if t["fatura_id"] is None)
    dates = [t for t, _ in unmatched]
    hit_days, measurements = [], {}
    for g in iter_days(v.start + a.birikim_en_az_gecmis * DAY, end):
        window_start = g - (a.birikim_pencere - 1) * DAY
        i, j = bisect.bisect_left(dates, window_start), bisect.bisect_right(dates, g)
        n = j - i
        if n < a.birikim_en_az:
            continue
        history_start = max(v.start, g - a.gecmis_gun * DAY)
        k = bisect.bisect_left(dates, history_start)
        history_days = (window_start - history_start).days
        lam = (i - k) / history_days * a.birikim_pencere if history_days > 0 else 0.0
        p = poisson_tail(n, lam)
        if p < a.birikim_p:
            hit_days.append(g)
            measurements[g] = (n, lam, p, [collection_id for _, collection_id in unmatched[i:j]])
    result = []
    for g in onset_days(hit_days, a.birikim_birlestirme):
        if not start <= g <= end:
            continue
        n, lam, p, ids = measurements[g]
        result.append(make_alert(
            "eslesmeyen_birikim", g, "uyari", "sirket", "tahsilatlar", "Faturası belli olmayan tahsilatlar birikti",
            f"Son {a.birikim_pencere} günde faturası belli olmayan {n} tahsilat geldi; {v.period_label(g, a.gecmis_gun)} "
            f"ortalamasına göre beklenen {format_number(lam, 1)}. Bu kadarının şansla gelme olasılığı {format_number(p, 5)}.",
            {"tahsilat_sayisi": n}, {"beklenen": f"{lam:.2f}", "olasilik": f"{p:.6f}", "p_esigi": str(a.birikim_p)},
            {"tahsilatlar": ids}))
    return result


def customer_delay(v: VisibleData, a: RuleSettings, start: dt.date, end: dt.date) -> list[dict]:
    """Did too many of a large customer's invoices paid by transfer stay open to be explained by chance, given that
    customer's own habit?

    Invoices paid by cheque are kept apart: the due date of a cheque is known from the start, the delay shows in the invoices paid by transfer.
    For a day g:
    - Base: invoices whose due date fell between 12 months ago and `gecikme_pencere` days ago. For each, the delay known on g
      (closing - due date; infinite if not closed).
    - Test: invoices whose due date fell in the last `gecikme_pencere` days. Each is compared with the probability
      of normally still being open at this age (the share in the base that was late by more than that many days, with Laplace smoothing).
    - If the probability of the observed number of open invoices being this high under these probabilities (the Poisson-binomial tail)
      is below the threshold, the condition holds.
    Invoices paid by cheque (closed by a cheque collection) and open invoices covered by a cheque of the same amount in the portfolio
    are removed from both.
    """
    # Day each sales invoice closed; None if it was not closed by `end`. The closing day is known only from the records up to
    # that day; nothing after it is used.
    closing = closing_dates(v.t, end)
    paid_by_cheque: dict[str, dt.date] = {}
    for t in v.t["tahsilatlar"]:
        if t["cek_id"] and t["fatura_id"]:
            paid_by_cheque[t["fatura_id"]] = min(t["tarih"], paid_by_cheque.get(t["fatura_id"], t["tarih"]))
    cheque_end: dict[str, dt.date] = {}
    for h in v.t["cek_hareketleri"]:
        if h["durum"] in ("tahsil_edildi", "karsiliksiz"):
            cheque_end[h["cek_id"]] = min(h["tarih"], cheque_end.get(h["cek_id"], h["tarih"]))
    customer_cheques = defaultdict(list)  # customer -> (received, collected/bounced day, amount)
    for c in v.t["cekler"]:
        customer_cheques[c["musteri_id"]].append((c["alis_tarihi"], cheque_end.get(c["cek_id"]), c["tutar_kurus"]))
    customer_invoices = defaultdict(list)  # customer -> sorted by due date (due date, invoice, issue day, amount)
    for f in v.t["faturalar"]:
        if f["tur"] == "satis" and f["tutar_kurus"] > 0:
            customer_invoices[f["musteri_id"]].append(
                (f["vade_tarihi"], f["fatura_id"], f["fatura_tarihi"], f["tutar_kurus"]))
    for m in customer_invoices:
        customer_invoices[m].sort()
    due_dates = {m: [x[0] for x in entries] for m, entries in customer_invoices.items()}

    window: dict[str, int] = defaultdict(int)
    hit_days, measurements = defaultdict(list), {}
    for g in iter_days(v.start, end):
        for m, amount in v.customer_daily_sales.get(g, {}).items():
            window[m] += amount
        for m, amount in v.customer_daily_sales.get(g - a.gecmis_gun * DAY, {}).items():
            window[m] -= amount
        if (g - v.start).days < a.isinma_gun:
            continue
        for _, m in heapq.nlargest(a.buyuk_musteri_sayisi, ((t, m) for m, t in window.items() if t > 0)):
            entries, customer_due_dates = customer_invoices.get(m, []), due_dates.get(m, [])
            in_portfolio: dict[int, int] = defaultdict(int)  # amounts of the cheques in the portfolio or at collection on day g
            for received, end_day, amount in customer_cheques.get(m, []):
                if received <= g and (end_day is None or end_day > g):
                    in_portfolio[amount] += 1

            def is_transfer_paid(invoice_id: str, amount: int) -> bool:
                k = closing[invoice_id]
                if k is not None and k <= g:
                    return not (invoice_id in paid_by_cheque and paid_by_cheque[invoice_id] <= g)
                return in_portfolio[amount] == 0

            i0 = bisect.bisect_left(customer_due_dates, g - a.gecmis_gun * DAY)
            i1 = bisect.bisect_left(customer_due_dates, g - a.gecikme_pencere * DAY)
            i2 = bisect.bisect_left(customer_due_dates, g)
            base = sorted((closing[invoice_id] - due).days if closing[invoice_id] is not None and closing[invoice_id] <= g else 10**6
                           for due, invoice_id, _, amount in entries[i0:i1] if is_transfer_paid(invoice_id, amount))
            if len(base) < a.gecikme_en_az_gecmis:
                continue
            probabilities, open_invoices = [], []
            for due, invoice_id, issue_date, amount in entries[i1:i2]:
                if issue_date > g or not is_transfer_paid(invoice_id, amount):
                    continue
                age_days = (g - due).days
                probabilities.append((len(base) - bisect.bisect_right(base, age_days) + 1) / (len(base) + 2))
                if closing[invoice_id] is None or closing[invoice_id] > g:
                    open_invoices.append(invoice_id)
            if len(open_invoices) < a.gecikme_fatura_sayisi:
                continue
            p = poisson_binomial_tail(len(open_invoices), probabilities)
            if p < a.gecikme_p:
                hit_days[m].append(g)
                measurements[(m, g)] = (sorted(open_invoices), len(probabilities), sum(probabilities), p, len(base))
    result = []
    for m, day_list in sorted(hit_days.items()):
        for g in onset_days(day_list, a.gecikme_birlestirme):
            if not start <= g <= end:
                continue
            open_invoices, n, expected, p, base = measurements[(m, g)]
            result.append(make_alert(
                "musteri_gecikmesi", g, "uyari", "musteri", m, "Büyük bir müşteri alışılmışın dışında gecikiyor",
                f"{v.customer_names.get(m, m)} {v.period_label(g, a.gecmis_gun)} en büyük {a.buyuk_musteri_sayisi} "
                f"müşterisinden biri. Vadesi son "
                f"{a.gecikme_pencere} günde dolan ve çekle karşılanmamış {n} faturasından {len(open_invoices)} tanesi hâlâ "
                f"açık. Müşterinin havale ile ödediği önceki {base} faturasındaki alışkanlığına göre beklenen "
                f"{format_number(expected, 1)}; bu kadarının şansla açık kalma olasılığı {format_number(p, 4)}.",
                {"acik_fatura": len(open_invoices), "incelenen_fatura": n},
                {"beklenen_acik": f"{expected:.2f}", "olasilik": f"{p:.6f}", "p_esigi": str(a.gecikme_p),
                 "taban_fatura": base},
                {"faturalar": open_invoices, "musteriler": [m]}, customer_id=m))
    return result


RULES = {
    "nakit_esigi": cash_threshold,
    "buyuk_cikis": large_outflow,
    "mukerrer_odeme": duplicate_payment,
    "karsiliksiz_cek": bounced_cheque,
    "eslesmeyen_birikim": unmatched_buildup,
    "musteri_gecikmesi": customer_delay,
}
