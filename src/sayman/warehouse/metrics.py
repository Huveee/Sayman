"""Financial metrics: computed with SQL from the tables in the warehouse, as of the end of a given day (as-of).

Every metric takes a `tarih` (date) and uses only the records that had happened by that day; no row dated later
enters the result. Amounts are integer cents. The TL equivalent of foreign-currency amounts is rounded half to
even (same rule as sayman.data.money.to_cents).

Definitions (D = date):
- Open invoice: a sales invoice (fatura_tarihi <= D) with amount - collections up to D - the applied part of the linked
  return invoices up to D > 0. A cheque does not close the invoice until it is collected (Phase 1 rule).
- Unmatched collection: a collection not linked to an invoice; it is deducted from the customer's receivable.
- Rate: the latest TCMB buying rate on or before D (D included). If there is no rate an error is raised; it is not silently skipped.
"""

from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_EVEN, Decimal

from sayman.warehouse.loader import query_rows

BUCKETS = (  # (column, min days overdue, max days overdue); None = unbounded
    ("vadesi_gelmemis", None, 0),
    ("gecikme_1_30", 1, 30),
    ("gecikme_31_60", 31, 60),
    ("gecikme_61_90", 61, 90),
    ("gecikme_91_180", 91, 180),
    ("gecikme_180_ustu", 181, None),
)
DOUBTFUL_DAYS = 90  # in the cash forecast: a receivable more than this many days past due has an uncertain collection date
HISTORY_WINDOW = 365  # in the cash forecast: a customer's average delay comes from the invoices closed in the last this-many days
EXCLUDED_ITEMS = (
    "Henüz kesilmemiş satışlar ve onlardan gelecek tahsilatlar (peşin satışlar ve POS dahil)",
    "Maaş, SGK ve vergi ödemeleri ile alış faturası olmayan diğer ödemeler",
    "Müşterilere yapılacak iade ödemeleri ve banka masrafları",
    "Hesaplar arası virman ve döviz bozdurma (şirketin toplam nakdini değiştirmez)",
)


def _rate_sql(expression: str, currency: str) -> str:
    return f"CASE WHEN {expression} IS NULL THEN error('kur bulunamadı: ' || {currency}) ELSE {expression} END"


RATES_AT_D = """
kur_d AS (
    SELECT para_birimi, arg_max(alis, tarih) AS kur FROM doviz_kurlari WHERE tarih <= $d GROUP BY para_birimi
    UNION ALL SELECT 'TRY', CAST(1 AS DECIMAL(18,6))
)"""

OPEN_INVOICES = """
tah AS (
    SELECT fatura_id, sum(tutar_kurus) AS s FROM tahsilatlar
    WHERE tarih <= $d AND fatura_id IS NOT NULL GROUP BY fatura_id
),
iad AS (
    SELECT iliskili_fatura_id AS fatura_id, sum(coalesce(uygulanan_kurus, 0)) AS s FROM faturalar
    WHERE tur = 'iade' AND fatura_tarihi <= $d AND iliskili_fatura_id IS NOT NULL GROUP BY iliskili_fatura_id
),
acik AS (
    SELECT f.fatura_id, f.musteri_id, f.fatura_tarihi, f.vade_tarihi, f.para_birimi, f.kur,
           f.tutar_kurus - coalesce(tah.s, 0) - coalesce(iad.s, 0) AS acik_kurus,
           date_diff('day', f.vade_tarihi, CAST($d AS DATE)) AS gecikme_gun
    FROM faturalar f
    LEFT JOIN tah ON tah.fatura_id = f.fatura_id
    LEFT JOIN iad ON iad.fatura_id = f.fatura_id
    WHERE f.tur = 'satis' AND f.fatura_tarihi <= $d
),
acik_pozitif AS (
    SELECT *, kur_carp(acik_kurus, kur) AS acik_try_kurus FROM acik WHERE acik_kurus > 0
)"""

UNMATCHED = f"""
eslesmemis AS (
    SELECT t.tahsilat_id, t.musteri_id, t.para_birimi, t.tutar_kurus,
           kur_carp(t.tutar_kurus, CASE WHEN t.para_birimi = 'TRY' THEN CAST(1 AS DECIMAL(18,6))
                                        ELSE {_rate_sql('k.alis', 't.para_birimi')} END) AS try_kurus
    FROM tahsilatlar t
    ASOF LEFT JOIN doviz_kurlari k ON k.para_birimi = t.para_birimi AND k.tarih <= t.tarih
    WHERE t.fatura_id IS NULL AND t.tarih <= $d
)"""

OPEN_PAYABLES = """
od AS (
    SELECT alis_fatura_id, sum(tutar_kurus) AS s FROM odemeler
    WHERE tarih <= $d AND alis_fatura_id IS NOT NULL GROUP BY alis_fatura_id
),
acik_borc AS (
    SELECT a.alis_fatura_id, a.tedarikci_id, a.kategori, a.tarih, a.vade_tarihi,
           a.tutar_kurus - coalesce(od.s, 0) AS acik_kurus
    FROM alis_faturalari a LEFT JOIN od ON od.alis_fatura_id = a.alis_fatura_id
    WHERE a.tarih <= $d
)"""

BANK = """
har AS (SELECT hesap_id, sum(tutar_kurus) AS s FROM banka_hareketleri WHERE tarih <= $d GROUP BY hesap_id),
bakiye AS (
    SELECT h.hesap_id, h.banka, h.para_birimi, h.acilis_bakiye_kurus + coalesce(har.s, 0) AS bakiye_kurus
    FROM banka_hesaplari h LEFT JOIN har ON har.hesap_id = h.hesap_id
    WHERE h.acilis_tarihi <= $d
)"""


# Closing day of the sales invoices up to D, and the delay of those closed in the last `pencere` days (below 0 counts as 0).
PAYMENT_HISTORY = """
olgu AS (
    SELECT fatura_id, tarih, tutar_kurus AS tutar FROM tahsilatlar WHERE fatura_id IS NOT NULL AND tarih <= $d
    UNION ALL
    SELECT iliskili_fatura_id, fatura_tarihi, coalesce(uygulanan_kurus, 0) FROM faturalar
    WHERE tur = 'iade' AND iliskili_fatura_id IS NOT NULL AND fatura_tarihi <= $d
),
kumulatif AS (
    SELECT fatura_id, tarih, sum(sum(tutar)) OVER (PARTITION BY fatura_id ORDER BY tarih) AS toplam
    FROM olgu GROUP BY fatura_id, tarih
),
ilk_kapanis AS (
    SELECT k.fatura_id, min(k.tarih) AS gun
    FROM kumulatif k JOIN faturalar f ON f.fatura_id = k.fatura_id
    WHERE k.toplam >= f.tutar_kurus GROUP BY k.fatura_id
),
kapanis AS (
    SELECT f.musteri_id, f.vade_tarihi,
           CASE WHEN f.tutar_kurus <= 0 THEN f.fatura_tarihi ELSE ik.gun END AS kapanis_tarihi
    FROM faturalar f LEFT JOIN ilk_kapanis ik ON ik.fatura_id = f.fatura_id
    WHERE f.tur = 'satis' AND f.fatura_tarihi <= $d
),
gecmis AS (
    SELECT musteri_id, greatest(0, date_diff('day', vade_tarihi, kapanis_tarihi)) AS gecikme
    FROM kapanis
    WHERE kapanis_tarihi BETWEEN CAST($d AS DATE) - CAST($pencere AS INTEGER) + 1 AND $d
)"""


def _require_date(tarih) -> dt.date:
    if isinstance(tarih, dt.datetime) or not isinstance(tarih, dt.date):
        raise TypeError("tarih bir datetime.date olmalı")
    return tarih


def _to_int(row: dict) -> dict:
    """DuckDB HUGEINT sums arrive as Python int; integers that are still Decimal are converted to int."""
    return {k: (int(v) if isinstance(v, Decimal) and k.endswith(("_kurus", "_gun")) else v) for k, v in row.items()}


# ---------------------------------------------------------------- 1. cash position


def cash_position(con, tarih: dt.date) -> dict:
    """Balance of each bank account at the end of day D (opening balance + movements up to D) and its TL equivalent."""
    sql = f"""
    WITH {RATES_AT_D}, {BANK}
    SELECT b.hesap_id, b.banka, b.para_birimi, b.bakiye_kurus, {_rate_sql('k.kur', 'b.para_birimi')} AS kur,
           kur_carp(b.bakiye_kurus, {_rate_sql('k.kur', 'b.para_birimi')}) AS bakiye_try_kurus
    FROM bakiye b LEFT JOIN kur_d k ON k.para_birimi = b.para_birimi
    ORDER BY b.hesap_id"""
    accounts = [_to_int(s) for s in query_rows(con, sql, {"d": _require_date(tarih)})]
    return {"tarih": tarih, "hesaplar": accounts, "toplam_try_kurus": sum(h["bakiye_try_kurus"] for h in accounts)}


# ---------------------------------------------------------------- 2. receivables aging


def _bucket_case(expression: str) -> str:
    parts = []
    for name, lower, upper in BUCKETS:
        if upper is None:
            parts.append(f"ELSE '{name}'")
        else:
            parts.append(f"WHEN {expression} <= {upper} THEN '{name}'")
    return "CASE " + " ".join(parts) + " END"


def aging(con, tarih: dt.date) -> dict:
    """Open receivable per customer (TL, at the invoice rate), split into buckets by the days since the due date.
    Unmatched collections stand as a negative amount in a separate column; total = buckets + unmatched."""
    bucket_columns = ",\n".join(
        f"sum(CASE WHEN x.dilim = '{name}' THEN x.tutar ELSE 0 END) AS {name}_kurus" for name, _, _ in BUCKETS)
    sql = f"""
    WITH {OPEN_INVOICES}, {UNMATCHED},
    x AS (
        SELECT musteri_id, {_bucket_case('gecikme_gun')} AS dilim, acik_try_kurus AS tutar FROM acik_pozitif
        UNION ALL
        SELECT musteri_id, 'eslesmemis_tahsilat', -try_kurus FROM eslesmemis
    )
    SELECT x.musteri_id, any_value(m.ad) AS ad,
           {bucket_columns},
           sum(CASE WHEN x.dilim = 'eslesmemis_tahsilat' THEN x.tutar ELSE 0 END) AS eslesmemis_tahsilat_kurus,
           sum(x.tutar) AS toplam_kurus
    FROM x LEFT JOIN musteriler m ON m.musteri_id = x.musteri_id
    GROUP BY x.musteri_id
    ORDER BY toplam_kurus DESC, x.musteri_id"""
    customers = [_to_int(s) for s in query_rows(con, sql, {"d": _require_date(tarih)})]
    columns = [f"{name}_kurus" for name, _, _ in BUCKETS] + ["eslesmemis_tahsilat_kurus", "toplam_kurus"]
    total = {s: sum(m[s] for m in customers) for s in columns}
    return {"tarih": tarih, "musteriler": customers, "toplam": total}


# ---------------------------------------------------------------- 3. DSO


def dso_days(receivable_cents: int, sales_cents: int, gun: int) -> Decimal | None:
    """gun x receivable / sales, rounded half to even to 2 decimals. None if there are no sales (or they are negative)."""
    if sales_cents <= 0:
        return None
    return (Decimal(gun * receivable_cents) / Decimal(sales_cents)).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


def net_sales(con, start: dt.date, end: dt.date) -> int:
    """TL amount of sales and return invoices plus POS sales (start and end inclusive)."""
    sql = """
    SELECT coalesce((SELECT sum(tutar_try_kurus) FROM faturalar WHERE fatura_tarihi BETWEEN $bas AND $bit), 0)
         + coalesce((SELECT sum(tutar_kurus) FROM pos_islemleri WHERE islem_tarihi BETWEEN $bas AND $bit), 0) AS s"""
    return int(query_rows(con, sql, {"bas": start, "bit": end})[0]["s"])


def dso(con, tarih: dt.date, gun: int = 365) -> dict:
    """Days sales outstanding: gun x (open receivable at D) / (net sales of the `gun` days ending at D).
    With D = 31 December and gun = 365 it is the same as the annual DSO definition of Phase 1."""
    tarih = _require_date(tarih)
    receivable = aging(con, tarih)["toplam"]["toplam_kurus"]
    sales = net_sales(con, tarih - dt.timedelta(days=gun - 1), tarih)
    return {"tarih": tarih, "gun": gun, "alacak_kurus": receivable, "satis_kurus": sales,
            "dso_gun": dso_days(receivable, sales, gun)}


# ---------------------------------------------------------------- 4. overdue receivables


def overdue_receivables(con, tarih: dt.date, en_az_gun: int = 1) -> dict:
    """Open invoices at least `en_az_gun` days past due, by TL amount from largest to smallest."""
    sql = f"""
    WITH {OPEN_INVOICES}
    SELECT a.fatura_id, a.musteri_id, m.ad, a.fatura_tarihi, a.vade_tarihi, a.gecikme_gun, a.para_birimi,
           a.acik_kurus, a.acik_try_kurus
    FROM acik_pozitif a LEFT JOIN musteriler m ON m.musteri_id = a.musteri_id
    WHERE a.gecikme_gun >= $en_az
    ORDER BY a.acik_try_kurus DESC, a.fatura_id"""
    invoices = [_to_int(s) for s in query_rows(con, sql, {"d": _require_date(tarih), "en_az": en_az_gun})]
    return {"tarih": tarih, "en_az_gun": en_az_gun, "faturalar": invoices,
            "toplam_try_kurus": sum(f["acik_try_kurus"] for f in invoices)}


# ---------------------------------------------------------------- 5. upcoming payments


def upcoming_payments(con, tarih: dt.date, gun: int = 30) -> dict:
    """Unpaid purchase invoices due by D + gun (including overdue unpaid ones).
    Open = amount - payments up to D; an overpaid (duplicate) invoice with a negative open amount is not listed."""
    sql = f"""
    WITH {OPEN_PAYABLES}
    SELECT b.alis_fatura_id, b.tedarikci_id, t.ad, b.kategori, b.tarih, b.vade_tarihi,
           date_diff('day', CAST($d AS DATE), b.vade_tarihi) AS kalan_gun, b.acik_kurus
    FROM acik_borc b LEFT JOIN tedarikciler t ON t.tedarikci_id = b.tedarikci_id
    WHERE b.acik_kurus > 0 AND b.vade_tarihi <= CAST($d AS DATE) + CAST($gun AS INTEGER)
    ORDER BY b.vade_tarihi, b.alis_fatura_id"""
    payments = [_to_int(s) for s in query_rows(con, sql, {"d": _require_date(tarih), "gun": gun})]
    return {"tarih": tarih, "gun": gun, "odemeler": payments, "toplam_kurus": sum(o["acik_kurus"] for o in payments)}


# ---------------------------------------------------------------- 6. FX risk


def fx_risk(con, tarih: dt.date) -> dict:
    """Open position per currency: FX receivable (open invoices - unmatched FX collections) + FX
    bank balance; its TL equivalent at the D rate, the TL effect if the rate moves 10%, and the exchange difference
    of the open receivables against the invoice rate (TL at the D rate - TL at the invoice rate)."""
    sql = f"""
    WITH {RATES_AT_D}, {OPEN_INVOICES}, {BANK},
    pb AS (
        SELECT para_birimi FROM musteriler WHERE ilk_islem_tarihi <= $d AND para_birimi <> 'TRY'
        UNION SELECT para_birimi FROM banka_hesaplari WHERE acilis_tarihi <= $d AND para_birimi <> 'TRY'
    ),
    alacak AS (
        SELECT a.para_birimi, sum(a.acik_kurus) AS doviz,
               sum(kur_carp(a.acik_kurus, {_rate_sql('k.kur', 'a.para_birimi')}) - kur_carp(a.acik_kurus, a.kur)) AS fark
        FROM acik_pozitif a LEFT JOIN kur_d k ON k.para_birimi = a.para_birimi
        WHERE a.para_birimi <> 'TRY' GROUP BY a.para_birimi
    ),
    esl AS (
        SELECT para_birimi, sum(tutar_kurus) AS s FROM tahsilatlar
        WHERE fatura_id IS NULL AND tarih <= $d GROUP BY para_birimi
    ),
    banka AS (SELECT para_birimi, sum(bakiye_kurus) AS s FROM bakiye GROUP BY para_birimi),
    poz AS (
        SELECT pb.para_birimi, coalesce(alacak.doviz, 0) - coalesce(esl.s, 0) AS alacak_doviz_kurus,
               coalesce(banka.s, 0) AS banka_doviz_kurus, coalesce(alacak.fark, 0) AS alacak_kur_farki_kurus,
               {_rate_sql('k.kur', 'pb.para_birimi')} AS kur
        FROM pb
        LEFT JOIN alacak ON alacak.para_birimi = pb.para_birimi
        LEFT JOIN esl ON esl.para_birimi = pb.para_birimi
        LEFT JOIN banka ON banka.para_birimi = pb.para_birimi
        LEFT JOIN kur_d k ON k.para_birimi = pb.para_birimi
    )
    SELECT para_birimi, alacak_doviz_kurus, banka_doviz_kurus,
           alacak_doviz_kurus + banka_doviz_kurus AS toplam_doviz_kurus, kur,
           kur_carp(alacak_doviz_kurus + banka_doviz_kurus, kur) AS toplam_try_kurus,
           yuvarla_cift(CAST(alacak_doviz_kurus + banka_doviz_kurus AS DECIMAL(38,0)) * kur
                        * CAST(0.1 AS DECIMAL(2,1))) AS yuzde_10_etki_kurus,
           alacak_kur_farki_kurus
    FROM poz ORDER BY para_birimi"""
    currencies = [_to_int(s) for s in query_rows(con, sql, {"d": _require_date(tarih)})]
    return {"tarih": tarih, "dovizler": currencies, "toplam_try_kurus": sum(d["toplam_try_kurus"] for d in currencies)}


# ---------------------------------------------------------------- 7. cash flow forecast


def cash_flow_forecast(con, tarih: dt.date, hafta: int = 13) -> dict:
    """Known cash inflows and outflows of the `hafta` weeks after D (week k: D+7(k-1)+1 ... D+7k).

    Inflows:
    - Open receivables: expected day = due date + the customer's average delay (from the invoices that closed in the
      365 days before D, rounded down to whole days; the company average if there is no history), at the earliest D+1.
      A receivable more than 90 days past due is not forecast; it is reported separately as "doubtful".
      FX receivables are converted to TL at the D rate.
    - Cheques in the portfolio and at collection: on the cheque due date. A customer's pending cheques and unmatched
      collections count as having closed that customer's open invoices with the earliest due dates, so that the same
      money is not counted twice.
    - POS sales whose value date has not come: on the value date, net of commission.
    Outflows:
    - Unpaid purchase invoices: on the due date, or on D+1 if it is overdue.
    Excluded items are in EXCLUDED_ITEMS; opening = the TL total of the cash position.

    "bilinen_kalemlerle_bakiye_kurus" is not a balance forecast: items that are not included, such as salaries, taxes and
    new sales, are missing, so the real balance will differ. Receivables that are overdue (by at most 90 days)
    and past their expected day are written to week 1; that is why the inflows of week 1 are larger than the later ones.
    """
    tarih = _require_date(tarih)
    sql = f"""
    WITH {RATES_AT_D}, {OPEN_INVOICES}, {OPEN_PAYABLES},
    cek_son AS (
        SELECT cek_id, durum FROM (
            SELECT cek_id, durum,
                   row_number() OVER (PARTITION BY cek_id ORDER BY tarih DESC, cek_hareket_id DESC) AS sira
            FROM cek_hareketleri WHERE tarih <= $d)
        WHERE sira = 1
    ),
    bekleyen_cek AS (
        SELECT c.cek_id, c.musteri_id, c.vade_tarihi, c.tutar_kurus
        FROM cekler c JOIN cek_son s ON s.cek_id = c.cek_id
        WHERE c.alis_tarihi <= $d AND s.durum IN ('portfoyde', 'tahsilde')
    ),
    havuz AS (
        SELECT musteri_id, sum(tutar_kurus) AS havuz_kurus FROM (
            SELECT musteri_id, tutar_kurus FROM bekleyen_cek
            UNION ALL
            SELECT musteri_id, tutar_kurus FROM tahsilatlar WHERE fatura_id IS NULL AND tarih <= $d)
        GROUP BY musteri_id
    ),
    sirali AS (
        SELECT a.*, coalesce(sum(a.acik_kurus) OVER (
                   PARTITION BY a.musteri_id ORDER BY a.vade_tarihi, a.fatura_id
                   ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING), 0) AS onceki_kurus
        FROM acik_pozitif a
    ),
    kalan AS (
        SELECT s.*, s.acik_kurus - least(s.acik_kurus, greatest(0, coalesce(h.havuz_kurus, 0) - s.onceki_kurus))
                    AS kalan_kurus
        FROM sirali s LEFT JOIN havuz h ON h.musteri_id = s.musteri_id
    ),
    {PAYMENT_HISTORY},
    musteri_ort AS (SELECT musteri_id, CAST(sum(gecikme) // count(*) AS INTEGER) AS ort FROM gecmis GROUP BY musteri_id),
    sirket_ort AS (SELECT CAST(coalesce(sum(gecikme) // count(*), 0) AS INTEGER) AS ort FROM gecmis),
    kalemler AS (
        SELECT 'alacak' AS tur,
               CASE WHEN k.gecikme_gun > $supheli THEN NULL
                    ELSE greatest(k.vade_tarihi + coalesce(mo.ort, so.ort), CAST($d AS DATE) + 1) END AS gun,
               kur_carp(k.kalan_kurus, {_rate_sql('kd.kur', 'k.para_birimi')}) AS tutar
        FROM kalan k
        LEFT JOIN musteri_ort mo ON mo.musteri_id = k.musteri_id
        CROSS JOIN sirket_ort so
        LEFT JOIN kur_d kd ON kd.para_birimi = k.para_birimi
        WHERE k.kalan_kurus > 0
        UNION ALL
        SELECT 'cek', greatest(c.vade_tarihi, CAST($d AS DATE) + 1),
               kur_carp(c.tutar_kurus, {_rate_sql('kd.kur', 'm.para_birimi')})
        FROM bekleyen_cek c
        LEFT JOIN musteriler m ON m.musteri_id = c.musteri_id
        LEFT JOIN kur_d kd ON kd.para_birimi = m.para_birimi
        UNION ALL
        SELECT 'pos', valor_tarihi, tutar_kurus - komisyon_kurus FROM pos_islemleri
        WHERE islem_tarihi <= $d AND valor_tarihi > $d
        UNION ALL
        SELECT 'tedarikci', greatest(vade_tarihi, CAST($d AS DATE) + 1), acik_kurus FROM acik_borc WHERE acik_kurus > 0
    )
    SELECT tur,
           CASE WHEN gun IS NULL THEN -1
                WHEN (date_diff('day', CAST($d AS DATE), gun) - 1) // 7 + 1 > $hafta THEN 0
                ELSE (date_diff('day', CAST($d AS DATE), gun) - 1) // 7 + 1 END AS hafta,
           sum(tutar) AS tutar
    FROM kalemler GROUP BY ALL ORDER BY tur, hafta"""
    parameters = {"d": tarih, "hafta": hafta, "supheli": DOUBTFUL_DAYS, "pencere": HISTORY_WINDOW}
    totals = {(s["tur"], s["hafta"]): int(s["tutar"]) for s in query_rows(con, sql, parameters)}

    opening = cash_position(con, tarih)["toplam_try_kurus"]
    weeks, balance = [], opening
    for k in range(1, hafta + 1):
        receivable, cheque, pos, supplier = (totals.get((kind, k), 0) for kind in ("alacak", "cek", "pos", "tedarikci"))
        net = receivable + cheque + pos - supplier
        balance += net
        weeks.append({
            "hafta": k, "bas": tarih + dt.timedelta(days=7 * (k - 1) + 1), "bit": tarih + dt.timedelta(days=7 * k),
            "alacak_girisi_kurus": receivable, "cek_girisi_kurus": cheque, "pos_girisi_kurus": pos,
            "tedarikci_cikisi_kurus": supplier, "net_kurus": net, "bilinen_kalemlerle_bakiye_kurus": balance,
        })
    return {
        "tarih": tarih, "hafta_sayisi": hafta, "acilis_kurus": opening, "haftalar": weeks,
        "ufuk_disi_giris_kurus": sum(totals.get((kind, 0), 0) for kind in ("alacak", "cek", "pos")),
        "ufuk_disi_cikis_kurus": totals.get(("tedarikci", 0), 0),
        "supheli_alacak_kurus": totals.get(("alacak", -1), 0),
        "dahil_olmayanlar": list(EXCLUDED_ITEMS),
    }


# ---------------------------------------------------------------- helper: payment history


def payment_history(con, tarih: dt.date, gun: int = HISTORY_WINDOW) -> dict:
    """Per customer: the number of sales invoices closed in the `gun` days ending at D, how many of them
    closed after the due date, and the average delay (days, rounded down; early payment counts as 0). The cash flow
    forecast uses the same definition."""
    sql = f"""
    WITH {PAYMENT_HISTORY}
    SELECT musteri_id, count(*) AS fatura_sayisi, sum(CASE WHEN gecikme > 0 THEN 1 ELSE 0 END) AS gec_kapanan,
           CAST(sum(gecikme) // count(*) AS INTEGER) AS ortalama_gecikme_gun
    FROM gecmis GROUP BY musteri_id ORDER BY musteri_id"""
    customers = {s.pop("musteri_id"): {k: int(v) for k, v in s.items()}
                  for s in query_rows(con, sql, {"d": _require_date(tarih), "pencere": gun})}
    return {"tarih": tarih, "gun": gun, "musteriler": customers,
            "kapanan_fatura": sum(m["fatura_sayisi"] for m in customers.values())}


METRICS = {
    "nakit_pozisyonu": cash_position,
    "yaslandirma": aging,
    "dso": dso,
    "geciken_alacaklar": overdue_receivables,
    "yaklasan_odemeler": upcoming_payments,
    "kur_riski": fx_risk,
    "nakit_akis_tahmini": cash_flow_forecast,
    "odeme_gecmisi": payment_history,
}
