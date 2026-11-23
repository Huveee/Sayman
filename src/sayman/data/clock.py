"""Simulation clock: the day Sayman treats as "today".

The data is generated up to the data end date in one go, but the tools and monitoring only see records up to
the day the clock shows. "Advance time" moves the clock forward and makes the records of the new days visible.
The past never changes.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Clock:
    simdi: dt.date
    veri_bitis: dt.date

    def __post_init__(self):
        if self.simdi > self.veri_bitis:
            raise ValueError("Saat veri bitiş tarihini geçemez")

    def advance(self, days: int) -> "Clock":
        if days < 0:
            raise ValueError("Saat geri alınamaz")
        return Clock(min(self.simdi + dt.timedelta(days=days), self.veri_bitis), self.veri_bitis)


def read_clock(path: Path) -> Clock:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    return Clock(dt.date.fromisoformat(d["simdi"]), dt.date.fromisoformat(d["veri_bitis"]))


def write_clock(clock: Clock, path: Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps({"simdi": clock.simdi.isoformat(), "veri_bitis": clock.veri_bitis.isoformat()}) + "\n",
        encoding="utf-8",
    )
