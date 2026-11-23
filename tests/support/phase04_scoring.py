"""Phase 4 scoring: matches the monitoring alerts against the events in the answer key.

This file was written before the rules and was not changed while the rules were tuned. The monitoring code
(src/sayman/monitoring) never reads the answer key; only this scoring does.

The counting unit is one alert: while the same condition persists the monitoring engine alerts once, on the day the
condition starts, not every day. Only those whose severity is "uyari" count as alarms; "bilgi" records neither catch an
event nor count as a false alarm.

An event counts as caught if some alert satisfies all three at once:
1. It comes from the rule that corresponds to the event kind (EVENT_RULE).
2. One of the alert's identifiers is one of the event's identifiers (e.g. the same cheque, customer or payment).
3. The alert's day falls within the event's window (`window`).
An alert can catch at most one event; events are processed in date order, each matched with the earliest eligible alert.
"""

from __future__ import annotations

import datetime as dt

parse_date = dt.date.fromisoformat
DAYS_PER_MONTH = 30.4375

EVENT_RULE = {
    "olagandisi_buyuk_cikis": "buyuk_cikis",
    "mukerrer_odeme": "mukerrer_odeme",
    "karsiliksiz_cek": "karsiliksiz_cek",
    "nakit_sikismasi": "nakit_esigi",
    "eslesmeyen_tahsilat_birikimi": "eslesmeyen_birikim",
    "buyuk_musteri_gecikmesi": "musteri_gecikmesi",
}


def event_identifiers(o: dict) -> set[str]:
    kind = o["tur"]
    if kind == "olagandisi_buyuk_cikis":
        return {o["odeme_id"]}
    if kind == "mukerrer_odeme":
        return {o["mukerrer_odeme_id"], o["asil_odeme_id"], o["alis_fatura_id"]}
    if kind == "karsiliksiz_cek":
        return {o["cek_id"]}
    if kind == "nakit_sikismasi":
        return {"A_TRY"}
    if kind == "eslesmeyen_tahsilat_birikimi":
        return {t["tahsilat_id"] for t in o["tahsilatlar"]}
    if kind == "buyuk_musteri_gecikmesi":
        return {o["musteri_id"]}
    raise ValueError(kind)


def window(o: dict, data_end: dt.date) -> tuple[dt.date, dt.date]:
    """The days an alert counts for the event (both ends included)."""
    start, kind = parse_date(o["tarih"]), o["tur"]
    if kind in ("olagandisi_buyuk_cikis", "karsiliksiz_cek"):
        last = start + dt.timedelta(days=3)
    elif kind == "mukerrer_odeme":
        last = start + dt.timedelta(days=14)  # if the invoice becomes visible after the payment, the alert comes that day
    elif kind == "nakit_sikismasi":
        last = parse_date(o["bitis"])
    elif kind == "eslesmeyen_tahsilat_birikimi":
        last = parse_date(o["bitis"]) + dt.timedelta(days=14)
    else:  # buyuk_musteri_gecikmesi: as long as the delay lasts
        last = start + dt.timedelta(days=o["gecikme_gun"])
    return start, min(last, data_end)


def alert_identifiers(u: dict) -> set[str]:
    identifiers = {u["varlik"]["id"]}
    for items in u.get("kaynak", {}).values():
        identifiers.update(items)
    return identifiers


def alarms(alerts: list[dict]) -> list[dict]:
    return [u for u in alerts if u["onem"] == "uyari"]


def score(alerts: list[dict], events: list[dict], data_end: dt.date) -> dict:
    """On data with events: which event was caught, in how many days, and which alarms matched no event."""
    candidates = sorted(alarms(alerts), key=lambda u: (u["gun"], u["uyari_id"]))
    used, result = set(), []
    for o in sorted(events, key=lambda o: (o["tarih"], o["olay_id"])):
        start, last = window(o, data_end)
        identifiers = event_identifiers(o)
        matched = next((u for u in candidates if u["uyari_id"] not in used and u["kural"] == EVENT_RULE[o["tur"]]
                        and start <= parse_date(u["gun"]) <= last and alert_identifiers(u) & identifiers), None)
        if matched:
            used.add(matched["uyari_id"])
        result.append({"olay_id": o["olay_id"], "tur": o["tur"], "tarih": o["tarih"], "pencere": [str(start), str(last)],
                      "yakalandi": matched is not None, "uyari_id": matched["uyari_id"] if matched else None,
                      "gun_sonra": (parse_date(matched["gun"]) - start).days if matched else None})
    unmatched_alerts = [u for u in candidates if u["uyari_id"] not in used]
    return {
        "olay": len(result), "yakalanan": sum(r["yakalandi"] for r in result),
        "geri_cagirma": round(sum(r["yakalandi"] for r in result) / len(result), 4) if result else None,
        "olaylar": result, "alarm": len(candidates), "olaya_denk_gelmeyen_alarm": len(unmatched_alerts),
        "olaya_denk_gelmeyen_kurala_gore": _count_by_rule(unmatched_alerts),
    }


def false_alarms(alerts: list[dict], start_date: dt.date, end_date: dt.date) -> dict:
    """On event-free (clean) data every alarm is a false alarm."""
    a = alarms(alerts)
    months = ((end_date - start_date).days + 1) / DAYS_PER_MONTH
    return {"alarm": len(a), "ay": round(months, 2), "aylik": round(len(a) / months, 3), "kurala_gore": _count_by_rule(a),
            "bilgi": len(alerts) - len(a)}


def _count_by_rule(alerts: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for u in alerts:
        counts[u["kural"]] = counts.get(u["kural"], 0) + 1
    return dict(sorted(counts.items()))
