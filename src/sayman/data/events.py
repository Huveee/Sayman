"""Event layer: adds the number of events given in the settings to a copy of the base data.

The correct answer of every event (what happened, which records were affected) is written to the answer key; the key
never enters the tables the tools read. The last event of each kind falls after "now": the as-of view
does not show them, they appear once the clock is advanced.

Order: first non-cash events, then permanent cash outflows, last the temporary cash squeezes. Outflows and
squeezes are sized from the ledger as it is at that moment, so no account goes negative. A cash squeeze is not a
permanent outflow: supplier payments are pulled forward, and once their old due dates pass the balance returns to the base data.
"""

from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal

from sayman.data.money import to_cents, from_cents
from sayman.data.generator import NAME_WORDS, NAME_SUFFIXES, DAY, rng_stream, roll_to_business_day, new_id

COUNTERPARTIES = ["Vega", "Orion", "Delta", "Lima", "Sigma", "Kappa", "Nova", "Polar"]
OUTFLOW_REASONS = ["DANIŞMANLIK BEDELİ", "MAKİNE AVANSI", "GAYRİMENKUL KAPORASI", "ORTAK CARİ HESAP"]
NON_CASH_EVENTS = ("buyuk_musteri_gecikmesi", "karsiliksiz_cek", "mukerrer_odeme", "eslesmeyen_tahsilat_birikimi")
CASH_EVENTS = ("olagandisi_buyuk_cikis", "nakit_sikismasi")
OUTFLOW_MULTIPLES = (Decimal("2.0"), Decimal("3.0"))  # unusual outflow: between these multiples of the 95th percentile of supplier payments
OUTFLOW_CEILING = Decimal("0.8")  # unusual outflow: cannot exceed this share of the lowest A_TRY balance after that day
SQUEEZE_WINDOW = 14  # days: how long a cash squeeze keeps the balance below the threshold
PULL_FORWARD_HORIZON = 120  # days: the farthest date of the supplier payments that can be pulled forward


class NoCandidateError(RuntimeError):
    pass


def threshold_cents(u) -> int:
    """Cash squeeze threshold: 5% of the average monthly sales."""
    return to_cents(from_cents(u.average_monthly_sales) * Decimal("0.05"))


def _pick_day(u, rng, after: bool) -> tuple[dt.date, dt.date, dt.date]:
    """(event day, earliest day of the search, latest day of the search)."""
    if after:
        lower, upper = u.settings.simdi + 7 * DAY, u.end - 30 * DAY
        earliest, limit = u.settings.simdi + DAY, u.end
    else:
        lower, upper = u.start + 90 * DAY, u.settings.simdi - 45 * DAY
        earliest, limit = u.start + 60 * DAY, u.settings.simdi - DAY
    upper = max(upper, lower)
    return roll_to_business_day(lower + rng.randint(0, (upper - lower).days) * DAY), earliest, limit


def a_try_daily(u, records, opening) -> dict[dt.date, tuple[int, int]]:
    """Balance of A_TRY for every day (lowest within the day, end of day). The lowest within the day also covers the
    opening of the day (the closing of the previous day)."""
    rows = [s for s in u.ledger(records["tahsilatlar"], records["odemeler"], opening) if s["hesap_id"] == "A_TRY"]
    daily, i, closing, day = {}, 0, opening["A_TRY"], u.start
    while day <= u.end:
        lowest = closing
        while i < len(rows) and rows[i]["tarih"] == day:
            closing = rows[i]["bakiye_kurus"]
            lowest = min(lowest, closing)
            i += 1
        daily[day] = (lowest, closing)
        day += DAY
    return daily


def a_try_daily_lowest(u, records, opening) -> dict[dt.date, int]:
    return {g: v[0] for g, v in a_try_daily(u, records, opening).items()}


def _overlaps(used: set, start: dt.date, end: dt.date) -> bool:
    """Impact ranges of the cash outflow events must not overlap: every squeeze should be the result of a single event."""
    return any(isinstance(k, tuple) and k[0] == "nakit" and k[1] <= end and start <= k[2] for k in used)


def _supplier_p95(records) -> int:
    amounts = sorted(o["tutar_kurus"] for o in records["odemeler"] if o["tur"] == "tedarikci")
    return amounts[len(amounts) * 95 // 100]


# ---------------------------------------------------------------- non-cash events


def large_customer_delay(u, records, opening, rng, day, earliest, limit, used, rng_ids):
    for start in (day, earliest):
        end = min(start + 90 * DAY, limit)
        candidates = {}
        for t in records["tahsilatlar"]:
            if (start <= t["tarih"] <= end and t["yontem"] in ("havale", "swift") and t["eslesme"] == "eslesti"
                    and t["musteri_id"] not in used and not u.customers_by_id[t["musteri_id"]]["_batik"]):
                candidates.setdefault(t["musteri_id"], []).append(t)
        candidates = {m: v for m, v in candidates.items() if len(v) >= 3}
        if candidates:
            break
    else:
        raise NoCandidateError("büyük müşteri gecikmesi için aday yok")
    ordered = sorted(candidates, key=lambda m: (-u.customer_totals[m], m))
    customer = ordered[rng.randrange(min(3, len(ordered)))]
    used.add(customer)
    delay = rng.randint(45, 90)
    affected, removed = [], set()
    for t in sorted(candidates[customer], key=lambda t: (t["tarih"], t["tahsilat_id"])):
        old_date, new_date = t["tarih"], roll_to_business_day(t["tarih"] + delay * DAY)
        affected.append({"tahsilat_id": t["tahsilat_id"], "fatura_id": t["fatura_id"], "eski_tarih": old_date.isoformat(),
                          "yeni_tarih": new_date.isoformat() if new_date <= u.end else None})
        if new_date > u.end:
            removed.add(t["tahsilat_id"])
        else:
            t["tarih"] = new_date
    records["tahsilatlar"] = [t for t in records["tahsilatlar"] if t["tahsilat_id"] not in removed]
    return {
        "tarih": affected[0]["eski_tarih"], "musteri_id": customer, "gecikme_gun": delay, "tahsilatlar": affected,
        "aciklama": f"Büyük bir müşteri ödemelerini {delay} gün geciktirmeye başladı.",
        "etkilenen": {"tahsilatlar": [e["tahsilat_id"] for e in affected]},
    }


def bounced_cheque(u, records, opening, rng, day, earliest, limit, used, rng_ids):
    amounts = sorted(c["tutar_kurus"] for c in records["cekler"])
    if not amounts:
        raise NoCandidateError("çek yok")
    threshold = amounts[len(amounts) * 3 // 4]
    cheques_by_id = {c["cek_id"]: c for c in records["cekler"]}
    for start in (day, earliest):
        candidates = sorted(
            (h for h in records["cek_hareketleri"]
             if h["durum"] == "tahsil_edildi" and start <= h["tarih"] <= limit and h["cek_id"] not in used
             and cheques_by_id[h["cek_id"]]["tutar_kurus"] >= threshold),
            key=lambda h: (h["tarih"], h["cek_id"]))
        if candidates:
            break
    else:
        raise NoCandidateError("karşılıksız çek için aday yok")
    movement = candidates[rng.randrange(min(5, len(candidates)))]
    cheque = cheques_by_id[movement["cek_id"]]
    used.add(cheque["cek_id"])
    movement["durum"] = "karsiliksiz"
    removed = [t["tahsilat_id"] for t in records["tahsilatlar"] if t["cek_id"] == cheque["cek_id"]]
    records["tahsilatlar"] = [t for t in records["tahsilatlar"] if t["cek_id"] != cheque["cek_id"]]
    return {
        "tarih": movement["tarih"].isoformat(), "cek_id": cheque["cek_id"], "musteri_id": cheque["musteri_id"],
        "tutar_kurus": cheque["tutar_kurus"],
        "aciklama": "Büyük tutarlı bir müşteri çeki vadesinde karşılıksız çıktı; fatura açık kaldı.",
        "etkilenen": {"cek_hareketleri": [movement["cek_hareket_id"]], "tahsilatlar": removed},
    }


def duplicate_payment(u, records, opening, rng, day, earliest, limit, used, rng_ids):
    amounts = sorted(o["tutar_kurus"] for o in records["odemeler"] if o["tur"] == "tedarikci")
    median = amounts[len(amounts) // 2]
    for start in (day, earliest):
        candidates = sorted(
            (o for o in records["odemeler"]
             if o["tur"] == "tedarikci" and o["yontem"] == "havale" and start <= o["tarih"] <= limit
             and o["tutar_kurus"] >= median and o["odeme_id"] not in used),
            key=lambda o: (o["tarih"], o["odeme_id"]))
        if candidates:
            break
    else:
        raise NoCandidateError("mükerrer ödeme için aday yok")
    original = candidates[rng.randrange(min(10, len(candidates)))]
    used.add(original["odeme_id"])
    duplicate = copy.deepcopy(original)
    duplicate["odeme_id"] = new_id("O", rng_ids, {o["odeme_id"] for o in records["odemeler"]})
    new_day = roll_to_business_day(original["tarih"] + rng.randint(0, 2) * DAY)
    duplicate["tarih"] = new_day if new_day <= limit else original["tarih"]
    records["odemeler"].append(duplicate)
    return {
        "tarih": duplicate["tarih"].isoformat(), "asil_odeme_id": original["odeme_id"], "mukerrer_odeme_id": duplicate["odeme_id"],
        "tedarikci_id": original["tedarikci_id"], "alis_fatura_id": original["alis_fatura_id"], "tutar_kurus": original["tutar_kurus"],
        "aciklama": "Aynı tedarikçi faturası iki kez ödendi.",
        "etkilenen": {"odemeler": [duplicate["odeme_id"]]},
    }


def unmatched_collection_buildup(u, records, opening, rng, day, earliest, limit, used, rng_ids):
    for start in (day, earliest):
        end = min(start + 21 * DAY, limit)
        candidates = {}
        for t in records["tahsilatlar"]:
            if (start <= t["tarih"] <= end and t["yontem"] == "havale" and t["eslesme"] == "eslesti"
                    and t["musteri_id"] not in used):
                candidates.setdefault(t["musteri_id"], []).append(t)
        if sum(len(v) for v in candidates.values()) >= 4:
            break
    else:
        raise NoCandidateError("eşleşmeyen tahsilat birikimi için aday yok")
    customers = sorted(candidates, key=lambda m: (-len(candidates[m]), m))[:3]
    chosen = sorted((t for m in customers for t in candidates[m]), key=lambda t: (t["tarih"], t["tahsilat_id"]))[:8]
    for t in chosen:
        t["fatura_id"] = None
        t["eslesme"] = "bilerek_eslesmemis"
    used.update(customers)
    return {
        "tarih": chosen[0]["tarih"].isoformat(), "bitis": chosen[-1]["tarih"].isoformat(),
        "tahsilatlar": [{"tahsilat_id": t["tahsilat_id"], "gercek_fatura_id": t["_gercek_fatura"]} for t in chosen],
        "aciklama": "Kısa sürede, açıklamasında fatura bilgisi olmayan çok sayıda havale geldi.",
        "etkilenen": {"tahsilatlar": [t["tahsilat_id"] for t in chosen]},
    }


# ---------------------------------------------------------------- events that create a cash outflow


def unusual_large_outflow(u, records, opening, rng, day, earliest, limit, used, rng_ids):
    """Permanent outflow: the amount cannot exceed 80% of the lowest A_TRY balance from that day to the end of the data."""
    p95 = _supplier_p95(records)
    multiple = Decimal(rng.randint(int(OUTFLOW_MULTIPLES[0] * 100), int(OUTFLOW_MULTIPLES[1] * 100))) / 100
    amount = to_cents(from_cents(p95) * multiple)
    if _overlaps(used, day - SQUEEZE_WINDOW * DAY, day + SQUEEZE_WINDOW * DAY):
        raise NoCandidateError("başka bir para çıkışı olayına çok yakın")
    daily = a_try_daily_lowest(u, records, opening)
    if amount > to_cents(from_cents(min(b for g, b in daily.items() if g >= day)) * OUTFLOW_CEILING):
        raise NoCandidateError("olağandışı çıkış için bakiye yetersiz")
    counterparty = f"{rng.choice(COUNTERPARTIES)} {rng.choice(NAME_WORDS)} {rng.choice(NAME_SUFFIXES)}"
    payment = {
        "odeme_id": new_id("O", rng_ids, {o["odeme_id"] for o in records["odemeler"]}), "tur": "diger",
        "tedarikci_id": None, "alis_fatura_id": None, "musteri_id": None, "iade_fatura_id": None, "tarih": day,
        "tutar_kurus": amount, "yontem": "havale", "banka_hesap_id": "A_TRY",
        "aciklama": f"EFT GİDEN {counterparty} {rng.choice(OUTFLOW_REASONS)}",
    }
    records["odemeler"].append(payment)
    used.add(("nakit", day, day))
    return {
        "tarih": day.isoformat(), "odeme_id": payment["odeme_id"], "tutar_kurus": amount,
        "tedarikci_odemesi_p95_kurus": p95, "p95_kati": str(multiple),
        "aciklama": f"Daha önce görülmemiş bir karşı tarafa, tedarikçi ödemelerinin 95. yüzdeliğinin {multiple} katı "
                    "tutarında faturasız para çıkışı.",
        "etkilenen": {"odemeler": [payment["odeme_id"]]},
    }


def cash_squeeze(u, records, opening, rng, day, earliest, limit, used, rng_ids):
    """Temporary squeeze: supplier payments with later due dates are pulled to the event day (early payment). Over the window
    A_TRY drops below the threshold; once the old dates of the pulled-forward payments pass, the balance returns to the base data."""
    threshold = threshold_cents(u)
    daily_both = a_try_daily(u, records, opening)
    daily = {g: v[0] for g, v in daily_both.items()}
    window_end = min(day + SQUEEZE_WINDOW * DAY, u.end)
    window_lowest = min(b for g, b in daily.items() if day <= g <= window_end)
    if window_lowest < threshold:
        raise NoCandidateError("bakiye olaydan önce zaten eşiğin altında")
    # Payments are pulled forward one by one, starting with the nearest due date. A pulled-forward payment lowers every day
    # between the event day and its old date by its own amount (the old day too, to be conservative). A payment that would
    # push a day negative is skipped; it stops when the lowest balance in the window falls below the threshold. Payments
    # pulled forward on the event day are deducted after that day's incoming money: the closing of the event day drops, not its opening.
    horizon = min(day + PULL_FORWARD_HORIZON * DAY, u.end)
    days = [g for g in sorted(daily) if day <= g <= horizon]
    drop = {g: 0 for g in days}

    def window_value(g):
        return (daily_both[g][1] if g == day else daily[g]) - drop[g]

    # A payment that belongs to another event is not a candidate: original payments sit in `used` by plain identifier, payments
    # affected by events (e.g. the copy of a duplicate payment) sit under the tag ("olay_odemesi", identifier). If they were pulled
    # forward, the date in that event's answer key would not match the date in the data.
    candidates = sorted((o for o in records["odemeler"]
                      if o["tur"] == "tedarikci" and window_end < o["tarih"] <= horizon and o["odeme_id"] not in used
                      and ("olay_odemesi", o["odeme_id"]) not in used),
                     key=lambda o: (o["tarih"], o["odeme_id"]))
    chosen, total = [], 0
    for o in candidates:
        affected_days = [g for g in days if g <= o["tarih"]]
        if any(daily[g] - drop[g] - o["tutar_kurus"] < 0 for g in affected_days):
            continue
        for g in affected_days:
            drop[g] += o["tutar_kurus"]
        chosen.append(o)
        total += o["tutar_kurus"]
        if min(window_value(g) for g in days if g <= window_end) < threshold:
            break
    else:
        raise NoCandidateError("öne çekilecek tedarikçi ödemesi yetmiyor")
    ends_on = max(o["tarih"] for o in chosen)  # old date of the last pulled-forward payment: the squeeze ends at the latest here
    if _overlaps(used, day - SQUEEZE_WINDOW * DAY, ends_on + SQUEEZE_WINDOW * DAY):
        raise NoCandidateError("başka bir para çıkışı olayıyla çakışıyor")
    moved = [{"odeme_id": o["odeme_id"], "eski_tarih": o["tarih"].isoformat()} for o in chosen]
    for o in chosen:
        o["tarih"] = day
        used.add(o["odeme_id"])
    used.add(("nakit", day, ends_on))
    after = a_try_daily_lowest(u, records, opening)
    below = sorted(g for g, b in after.items() if day <= g <= ends_on and b < threshold)
    return {
        "tarih": day.isoformat(), "bitis": ends_on.isoformat(), "esik_kurus": threshold, "one_cekilen_toplam_kurus": total,
        "odemeler": moved,
        "olay_oncesi_pencere_en_dusuk_kurus": window_lowest,
        "pencere_en_dusuk_kurus": min(b for g, b in after.items() if day <= g <= window_end),
        "esik_alti_ilk_gun": below[0].isoformat() if below else None,
        "esik_alti_son_gun": below[-1].isoformat() if below else None,
        "aciklama": "İleri vadeli tedarikçi ödemeleri erken yapıldı; A_TRY bakiyesi ortalama aylık satışın %5'inin "
                    "altına indi.",
        "etkilenen": {"odemeler": [o["odeme_id"] for o in chosen]},
    }


EVENT_FUNCTIONS = {
    "buyuk_musteri_gecikmesi": large_customer_delay,
    "karsiliksiz_cek": bounced_cheque,
    "mukerrer_odeme": duplicate_payment,
    "eslesmeyen_tahsilat_birikimi": unmatched_collection_buildup,
    "olagandisi_buyuk_cikis": unusual_large_outflow,
    "nakit_sikismasi": cash_squeeze,
}


def apply_events(u, records: dict, opening: dict[str, int]) -> dict:
    counts = dict(u.settings.olay_sayilari)
    if set(counts) - set(EVENT_FUNCTIONS):
        raise ValueError(f"bilinmeyen olay türü: {set(counts) - set(EVENT_FUNCTIONS)}")
    rng, rng_ids = rng_stream(u.settings, "olay"), rng_stream(u.settings, "kimlik:olay")
    used: set = set()
    results = []

    def plan_events(kinds):
        plans = []
        for kind in kinds:
            count = counts.get(kind, 0)
            for i in range(count):
                after = kind in u.settings.simdiden_sonra_olay and i == count - 1
                plans.append((kind, after, _pick_day(u, rng, after)))
        return plans

    # Order: non-cash events (in kind order), permanent outflows, temporary squeezes (both in date order).
    # Squeezes go last: the balance they lower on purpose must not set the ceiling of a permanent outflow added later.
    ordered = plan_events(NON_CASH_EVENTS)
    for kind in CASH_EVENTS:
        ordered += sorted(plan_events([kind]), key=lambda p: p[2][0])
    for kind, after, (day, earliest, limit) in ordered:
        for attempt in range(20):  # if no candidate is found, another day is tried
            if attempt:
                day, earliest, limit = _pick_day(u, rng, after)
            try:
                result = EVENT_FUNCTIONS[kind](u, records, opening, rng, day, earliest, limit, used, rng_ids)
                results.append((kind, result))
                # Payments affected by an event are marked with a tag; later cash squeezes do not pull them forward.
                # A tag, not a plain identifier: a plain identifier would also narrow the candidate list of later duplicate payment events.
                used.update(("olay_odemesi", i) for i in result["etkilenen"].get("odemeler", []))
                break
            except NoCandidateError:
                if attempt == 19:
                    raise
    for i, (kind, result) in enumerate(sorted(results, key=lambda x: (x[1]["tarih"], x[0])), start=1):
        u.key["olaylar"].append({"olay_id": f"E{i:02d}", "tur": kind,
                                     "simdiden_sonra": dt.date.fromisoformat(result["tarih"]) > u.settings.simdi, **result})
    return records
