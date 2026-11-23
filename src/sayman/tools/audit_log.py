"""Audit log: every tool call (including refusals and errors) is written as one row.

The log is append-only (JSON lines). Each row carries the digest of the previous one (a chain): if a row in the
middle is deleted or changed, `verify_chain` finds it. The identity card itself is never written.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path

INITIAL_DIGEST = "0" * 64


def _digest(row: dict) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
                                     default=str).encode("utf-8")).hexdigest()


class AuditLog:
    """If `path` is None the log is kept in memory only (for tests)."""

    def __init__(self, path: Path | None = None, clock=None):
        self.path = Path(path) if path else None
        self.clock = clock or (lambda: dt.datetime.now(dt.timezone.utc))
        self.rows: list[dict] = []
        self._last_digest, self._sequence = INITIAL_DIGEST, 0
        if self.path and self.path.exists():
            previous = self.read(self.path)
            if previous:
                self._last_digest, self._sequence = previous[-1]["ozet"], previous[-1]["sira"]

    def write(self, **fields) -> dict:
        if "kart" in fields:
            raise ValueError("kimlik kartı denetim kaydına yazılmaz")
        self._sequence += 1
        row = {"sira": self._sequence, "zaman": self.clock().isoformat(timespec="seconds"), **fields,
                 "onceki_ozet": self._last_digest}
        row["ozet"] = _digest(row)
        self._last_digest = row["ozet"]
        self.rows.append(row)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        return row

    @staticmethod
    def read(path: Path) -> list[dict]:
        return [json.loads(s) for s in Path(path).read_text(encoding="utf-8").splitlines() if s.strip()]


def verify_chain(rows: list[dict]) -> list[str]:
    """Checks the chain; returns a description of each broken row (an empty list means intact)."""
    errors, previous = [], INITIAL_DIGEST
    for i, row in enumerate(rows, start=1):
        body = {k: v for k, v in row.items() if k != "ozet"}
        if row.get("sira") != i:
            errors.append(f"{i}. satırın sırası {row.get('sira')}")
        if row.get("onceki_ozet") != previous:
            errors.append(f"{i}. satır bir önceki satıra bağlı değil")
        if _digest(body) != row.get("ozet"):
            errors.append(f"{i}. satırın içeriği değişmiş")
        previous = row.get("ozet")
    return errors
