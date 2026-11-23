"""Scheduler: runs the rules up to the day the clock shows and adds the new alerts to the alert store.

On every run the rules are recomputed from scratch on the data visible up to that day; the store only keeps the alerts
already reported and the last day that was examined. An alert's day comes from the data, not from the store. So:
- a second run with the same clock adds nothing,
- whether the clock advances day by day or in one jump, the store ends up with the same alerts.
The records carry no wall-clock time (the moment of the run); the same data and the same clock always give the same file.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path

from sayman.monitoring.rules import RULES, RuleSettings, VisibleData
from sayman.data.as_of import as_of_view
from sayman.data.clock import Clock

DAY = dt.timedelta(days=1)


def first_day(tables: dict[str, list[dict]]) -> dt.date:
    """First day of the data: the first sales invoice."""
    return min(f["fatura_tarihi"] for f in tables["faturalar"])


def scan_alerts(tables: dict[str, list[dict]], end: dt.date, settings: RuleSettings | None = None,
         start: dt.date | None = None) -> list[dict]:
    """Alerts that start between start and end; only records visible up to the end day are used."""
    a = settings or RuleSettings()
    g = as_of_view(tables, end)
    v = VisibleData(g, first_day(g), end)
    start = start or v.start
    result = []
    for rule in RULES.values():
        result.extend(rule(v, a, start, end))
    return sorted(result, key=lambda u: (u["gun"], u["kural"], u["varlik"]["id"], u["uyari_id"]))


class AlertStoreError(RuntimeError):
    pass


@dataclass
class AlertStore:
    """data/izleme/: uyarilar.jsonl (one alert per line, append-only) and durum.json (the last day examined)."""

    directory: Path

    @property
    def alerts_path(self) -> Path:
        return Path(self.directory) / "uyarilar.jsonl"

    @property
    def state_path(self) -> Path:
        return Path(self.directory) / "durum.json"

    def status(self) -> dict | None:
        if not self.state_path.exists():
            return None
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def alerts(self) -> list[dict]:
        if not self.alerts_path.exists():
            return []
        with open(self.alerts_path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def add(self, alerts: list[dict], last_day: dt.date, settings: RuleSettings) -> int:
        Path(self.directory).mkdir(parents=True, exist_ok=True)
        existing_ids = {u["uyari_id"] for u in self.alerts()}
        new_alerts = [u for u in alerts if u["uyari_id"] not in existing_ids]
        with open(self.alerts_path, "a", encoding="utf-8", newline="\n") as f:
            for u in new_alerts:
                f.write(json.dumps(u, ensure_ascii=False, sort_keys=True) + "\n")
        self.state_path.write_text(json.dumps({"son_gun": last_day.isoformat(), "ayarlar": settings.to_dict()},
                                              ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        return len(new_alerts)


def run_scheduler(tables: dict[str, list[dict]], saat: Clock, store: AlertStore, settings: RuleSettings | None = None) -> dict:
    """Scans the days after the last examined day up to the day the clock shows and adds the new alerts to the store."""
    a = settings or RuleSettings()
    state = store.status()
    if state and state["ayarlar"] != a.to_dict():
        raise AlertStoreError("Depo başka kural ayarlarıyla oluşturulmuş; yeni ayarlarla baştan başlamak için depoyu silin.")
    last = dt.date.fromisoformat(state["son_gun"]) if state else None
    if last is not None and saat.simdi < last:
        raise AlertStoreError(f"Saat ({saat.simdi}) depodaki son günün ({last}) gerisinde; saat geri alınamaz.")
    if last is not None and saat.simdi == last:
        return {"onceki_son_gun": last.isoformat(), "son_gun": last.isoformat(), "yeni": [], "toplam": len(store.alerts())}
    start = last + DAY if last else None
    alerts = scan_alerts(tables, saat.simdi, a, start)
    store.add(alerts, saat.simdi, a)
    return {"onceki_son_gun": last.isoformat() if last else None, "son_gun": saat.simdi.isoformat(),
            "yeni": alerts, "toplam": len(store.alerts())}
