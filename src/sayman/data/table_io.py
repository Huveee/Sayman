"""Schema of the generated tables, writing and reading them as CSV, and the content digest (hash).

The digest is computed from the content, not from the file bytes: rows are sorted by primary key and
columns are written in a fixed order. So line-ending differences (Windows/Linux) do not change the digest.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
from pathlib import Path

# table -> columns (the first column is the primary key)
SCHEMA: dict[str, list[str]] = {
    "musteriler": ["musteri_id", "ad", "ulke", "yurtici", "para_birimi", "vade_gun", "ilk_islem_tarihi"],
    "faturalar": [
        "fatura_id", "kaynak_fatura_no", "tur", "musteri_id", "fatura_tarihi", "vade_tarihi", "para_birimi",
        "tutar_kurus", "kur", "tutar_try_kurus", "iliskili_fatura_id", "iade_eslesme", "uygulanan_kurus",
        "kalem_sayisi",
    ],
    "pos_islemleri": ["pos_id", "kaynak_fatura_no", "islem_tarihi", "tutar_kurus", "komisyon_kurus", "valor_tarihi"],
    "tahsilatlar": [
        "tahsilat_id", "musteri_id", "fatura_id", "tarih", "para_birimi", "tutar_kurus", "yontem", "cek_id",
        "banka_hesap_id", "eslesme",
    ],
    "cekler": ["cek_id", "musteri_id", "cek_no", "kesideci_banka", "alis_tarihi", "vade_tarihi", "tutar_kurus"],
    "cek_hareketleri": ["cek_hareket_id", "cek_id", "tarih", "durum"],
    "tedarikciler": ["tedarikci_id", "ad", "kategori", "vade_gun"],
    "alis_faturalari": ["alis_fatura_id", "tedarikci_id", "tarih", "vade_tarihi", "tutar_kurus", "kategori"],
    "odemeler": [
        "odeme_id", "tur", "tedarikci_id", "alis_fatura_id", "musteri_id", "iade_fatura_id", "tarih", "tutar_kurus", "yontem",
        "banka_hesap_id", "aciklama",
    ],
    "banka_hesaplari": ["hesap_id", "banka", "para_birimi", "acilis_tarihi", "acilis_bakiye_kurus"],
    "banka_hareketleri": [
        "hareket_id", "hesap_id", "tarih", "tutar_kurus", "bakiye_kurus", "aciklama", "kaynak_tur", "kaynak_id",
    ],
    "doviz_kurlari": ["kur_id", "tarih", "para_birimi", "alis"],
}

INTEGER_COLUMNS = {
    s for columns in SCHEMA.values() for s in columns if s.endswith("_kurus") or s in {"vade_gun", "kalem_sayisi"}
}
BOOLEAN_COLUMNS = {"yurtici"}


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return str(value)


def table_digest(name: str, rows: list[dict]) -> str:
    columns = SCHEMA[name]
    h = hashlib.sha256()
    h.update(("\t".join(columns) + "\n").encode("utf-8"))
    for row in sorted(rows, key=lambda s: s[columns[0]]):
        h.update(("\t".join(_text(row.get(s)) for s in columns) + "\n").encode("utf-8"))
    return h.hexdigest()


def data_digest(tables: dict[str, list[dict]]) -> tuple[str, dict[str, str]]:
    table_digests = {name: table_digest(name, tables[name]) for name in sorted(SCHEMA)}
    h = hashlib.sha256()
    for name, th in table_digests.items():
        h.update(f"{name}:{th}\n".encode("utf-8"))
    return h.hexdigest(), table_digests


def file_digest(path: Path, as_text: bool = False) -> str:
    """SHA-256 digest of a file. With as_text=True the line ending (CRLF or LF) does not change the digest: git may
    write text files with CRLF on Windows, but the content is the same."""
    if as_text:
        return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_csv(tables: dict[str, list[dict]], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, columns in SCHEMA.items():
        with open(directory / f"{name}.csv", "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerow(columns)
            for row in sorted(tables[name], key=lambda s: s[columns[0]]):
                writer.writerow([_text(row.get(s)) for s in columns])


def _convert(column: str, value: str):
    if value == "":
        return None
    if column in INTEGER_COLUMNS:
        return int(value)
    if column in BOOLEAN_COLUMNS:
        return value == "1"
    if column.endswith("tarihi") or column == "tarih":
        return dt.date.fromisoformat(value)
    return value


def read_csv(directory: Path) -> dict[str, list[dict]]:
    tables = {}
    for name, columns in SCHEMA.items():
        with open(directory / f"{name}.csv", encoding="utf-8", newline="") as f:
            tables[name] = [{s: _convert(s, row[s]) for s in columns} for row in csv.DictReader(f)]
    return tables
