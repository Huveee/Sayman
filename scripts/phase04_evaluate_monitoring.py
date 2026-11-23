"""Phase 4 evaluation: runs the monitoring rules on data with events and without events, scores and reports.

- On the data with events: how many of the events in the answer key were caught, and within how many days
  (tests/support/phase04_scoring.py).
- On the event-free twin: how many false alarms per month (only those with severity "uyari" count).
The rules do not read the answer key; only this script reads the key, for scoring.

Seed 42 is the data the rules were tuned on. Seeds 43 and 44 are held-out data: they are run with --ayrilmis after
the rules are frozen; the rules are never changed after looking at their result. If --ayrilmis is not given, the
earlier held-out results in the report are kept.

Inputs: data/uretilen, data/anahtar/olaylar.json, data/izleme_veri (scripts/phase04_generate_data.py), data/saat.json
Output:   reports/faz4_izleme.json

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase04_evaluate_monitoring.py
    .venv\\Scripts\\python -I scripts\\phase04_evaluate_monitoring.py --ayrilmis
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import support.phase04_scoring as puan  # noqa: E402

from sayman.monitoring.rules import RuleSettings  # noqa: E402
from sayman.monitoring.scheduler import first_day, scan_alerts  # noqa: E402
from sayman.data.table_io import read_csv, data_digest  # noqa: E402
from sayman.data.clock import read_clock  # noqa: E402

REPORT = ROOT / "reports" / "faz4_izleme.json"
TUNING_SETS = [("tohum42", "tohum42_temiz")]
HELD_OUT_SETS = [("tohum43", "tohum43_temiz"), ("tohum44", "tohum44_temiz")]


def _digest(alerts: list[dict]) -> str:
    return hashlib.sha256(json.dumps(alerts, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _run(folder: Path, end: dt.date) -> tuple[dict, list[dict], dict]:
    t = read_csv(folder)
    start = time.perf_counter()
    alerts = scan_alerts(t, end)
    duration = time.perf_counter() - start
    return t, alerts, {"klasor": str(folder.relative_to(ROOT)).replace("\\", "/"), "veri_hash": data_digest(t)[0],
                         "baslangic": first_day(t).isoformat(), "sure_s": round(duration, 2),
                         "uyari": len(alerts), "uyari_kurala_gore": dict(sorted(Counter(
                             f"{u['kural']}/{u['onem']}" for u in alerts).items())),
                         "uyari_ozeti": _digest(alerts)}


def _cheque_shares(t: dict, alerts: list[dict], events: list[dict]) -> list[dict]:
    """For bounced-cheque (`karsiliksiz_cek`) events: the cheque amount as a share of the rule's threshold (upper
    quartile of the last 12 months' cheques). The generator uses the upper quartile of the whole period; the rule
    sees only the past. If the share is small, the event can drop to "bilgi" on held-out data; that is why it is
    written for every event."""
    amounts = sorted(c["tutar_kurus"] for c in t["cekler"])
    full_p75 = amounts[len(amounts) * 3 // 4]
    cheque_alerts = {u["varlik"]["id"]: u for u in alerts if u["kural"] == "karsiliksiz_cek"}
    result = []
    for o in events:
        if o["tur"] != "karsiliksiz_cek":
            continue
        u = cheque_alerts.get(o["cek_id"])
        threshold = u["esik"]["ust_ceyrek_tl"] if u else None
        amount = o["tutar_kurus"] / 100
        result.append({"olay_id": o["olay_id"], "cek_id": o["cek_id"], "tutar_tl": f"{amount:.2f}",
                      "kural_esigi_tl": threshold, "butun_donem_esigi_tl": f"{full_p75 / 100:.2f}",
                      "pay": f"{amount / float(threshold):.3f}" if threshold else None, "onem": u["onem"] if u else None})
    return result


def _interactions(events: list[dict]) -> list[dict]:
    """When a cash squeeze event pulled another event's payment forward, that event's day in the data differs from
    its day in the key. The score is not changed; this is written only to explain why an event looks missed."""
    pulled_forward = {p["odeme_id"]: (o["olay_id"], p["eski_tarih"], o["tarih"])
               for o in events if o["tur"] == "nakit_sikismasi" for p in o["odemeler"]}
    result = []
    for o in events:
        for field in ("mukerrer_odeme_id", "asil_odeme_id", "odeme_id"):
            if o.get(field) in pulled_forward:
                cash_event_id, old_date, new_date = pulled_forward[o[field]]
                result.append({"olay_id": o["olay_id"], "tur": o["tur"], "odeme_id": o[field], "cekan_olay": cash_event_id,
                              "anahtardaki_gun": old_date, "verideki_gun": new_date})
    return result


def _false_alarm_listing(alerts: list[dict], profiles: dict) -> list[dict]:
    rows = []
    for u in puan.alarms(alerts):
        row = {"gun": u["gun"], "kural": u["kural"], "varlik": u["varlik"]["id"]}
        if u["kural"] == "musteri_gecikmesi":
            p = profiles.get(u["varlik"]["id"], {})
            row["musteri_profili"] = p.get("profil")
            row["odemeyi_kesti"] = p.get("batik_baslangic")
        rows.append(row)
    return rows


def _samples(alerts: list[dict], event_alert_ids: set[str]) -> dict:
    """One sample from each rule (an alert that caught an event comes first)."""
    result = {}
    for u in sorted(alerts, key=lambda u: (u["uyari_id"] not in event_alert_ids, u["gun"])):
        if u["onem"] == "uyari" and u["kural"] not in result:
            result[u["kural"]] = {k: u[k] for k in ("uyari_id", "kural", "gun", "onem", "baslik", "aciklama", "olcum",
                                                   "esik")}
    return dict(sorted(result.items()))


def evaluate(with_events: str, clean: str, sets: dict, end: dt.date, held_out: bool) -> dict:
    events_set, clean_set = sets[with_events], sets[clean]
    key_path = ROOT / "data" / "anahtar" / "olaylar.json" if with_events == "tohum42" else ROOT / events_set["klasor"] / "olaylar.json"
    key = json.loads(key_path.read_text(encoding="utf-8"))
    t, alerts_events, info_events = _run(ROOT / events_set["klasor"], end)
    if info_events["veri_hash"] != events_set["veri_hash"]:
        raise SystemExit(f"{with_events}: veri özeti setler.json ile aynı değil; önce scripts/phase04_generate_data.py")
    p = puan.score(alerts_events, key["olaylar"], end)
    t2, alerts_clean, info_clean = _run(ROOT / clean_set["klasor"], end)
    y = puan.false_alarms(alerts_clean, first_day(t2), end)
    delays = sorted(r["gun_sonra"] for r in p["olaylar"] if r["yakalandi"])
    return {
        "tohum": events_set["tohum"], "ayrilmis": held_out,
        "olayli": {**info_events, "puan": p, "cek_olaylari": _cheque_shares(t, alerts_events, key["olaylar"]),
                   "olay_etkilesimleri": _interactions(key["olaylar"]),
                   "yakalama_gecikmesi_gun": {"en_az": delays[0], "ortanca": delays[len(delays) // 2],
                                              "en_cok": delays[-1]} if delays else None},
        "temiz": {**info_clean, "yanlis_alarm": y,
                  "karsiliksiz_cek_hareketi": sum(h["durum"] == "karsiliksiz" for h in t2["cek_hareketleri"]),
                  "alarmlar": _false_alarm_listing(alerts_clean, key.get("musteri_profilleri", {}))},
        "ornek_uyarilar": _samples(alerts_events, {r["uyari_id"] for r in p["olaylar"] if r["uyari_id"]}) if not held_out else None,
    }


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser()
    parser.add_argument("--ayrilmis", action="store_true", help="tohum 43 ve 44'ü de çalıştır (kurallar donduktan sonra)")
    args = parser.parse_args()
    sets = json.loads((ROOT / "data" / "izleme_veri" / "setler.json").read_text(encoding="utf-8"))
    end = read_clock(ROOT / "data" / "saat.json").veri_bitis
    previous = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {}

    result = {}
    for with_events, clean in TUNING_SETS + (HELD_OUT_SETS if args.ayrilmis else []):
        result[with_events] = evaluate(with_events, clean, sets, end, held_out=with_events != "tohum42")
        s = result[with_events]
        print(f"{with_events}: {s['olayli']['puan']['yakalanan']}/{s['olayli']['puan']['olay']} olay, "
              f"olaysız ikizde ayda {s['temiz']['yanlis_alarm']['aylik']} yanlış alarm "
              f"({s['temiz']['yanlis_alarm']['kurala_gore']}), süre {s['olayli']['sure_s']} s")
    if not args.ayrilmis:
        for with_events, _ in HELD_OUT_SETS:
            if with_events in previous.get("setler", {}):
                result[with_events] = previous["setler"][with_events]

    # The same data and the same day twice: the same alerts.
    t = read_csv(ROOT / "data" / "uretilen")
    repeat_same = _digest(scan_alerts(t, end)) == result["tohum42"]["olayli"]["uyari_ozeti"]

    report = {
        "aciklama": "Faz 4 izleme ölçümü. Olaylar ve kurallar aynı kişi tarafından yazıldı: yüksek yakalama oranı "
                    "kuralların olay türlerini bildiğini gösterir, gerçek dünyadaki başarısını göstermez. Bunu "
                    "biraz dengelemek için kurallar tohum 42'de ayarlandı, tohum 43 ve 44'te bir kez ölçüldü.",
        "veri_bitis": end.isoformat(),
        "ayarlar": RuleSettings().to_dict(),
        "kabul": {"geri_cagirma_en_az": 0.90, "aylik_yanlis_alarm_en_cok": 2.0},
        "ayni_gun_iki_calisma_ayni": repeat_same,
        "setler": dict(sorted(result.items())),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Aynı gün iki çalışma aynı: {repeat_same}. Yazıldı: {REPORT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
