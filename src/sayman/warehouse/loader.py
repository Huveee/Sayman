"""Data warehouse: loads the data/uretilen/*.csv tables into a single DuckDB file.

The warehouse holds only the tables the tools may read (sayman.data.table_io.SCHEMA); the answer key
(data/anahtar) and the simulation clock (data/saat.json) never enter it. The clock is passed to every metric as a date
parameter; so data/saat.json is the single clock and the warehouse can be opened read-only.

Column types are built explicitly from the names in SCHEMA, not guessed from the CSV: amounts are integer cents
(BIGINT), rates are exact decimals (DECIMAL), dates are DATE. Floating-point numbers are never used.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from sayman.data.table_io import BOOLEAN_COLUMNS, SCHEMA, INTEGER_COLUMNS

RATE_COLUMNS = {"kur", "alis"}
RATE_SQL_TYPE = "DECIMAL(18,6)"

# Round half to even (same rule as sayman.data.money.to_cents). DuckDB's round_even is not used because it
# converts to floating point; here it is done with exact decimal arithmetic.
MACROS = (
    "CREATE OR REPLACE MACRO yuvarla_cift(x) AS CAST(floor(x) + CASE WHEN x - floor(x) > 0.5 THEN 1 "
    "WHEN x - floor(x) < 0.5 THEN 0 WHEN floor(x) % 2 = 0 THEN 0 ELSE 1 END AS BIGINT)",
    # cents x rate -> cents (TL equivalent)
    "CREATE OR REPLACE MACRO kur_carp(kurus, kur) AS "
    "yuvarla_cift(CAST(kurus AS DECIMAL(38,0)) * CAST(kur AS DECIMAL(18,6)))",
)


def sql_type(column: str) -> str:
    if column in INTEGER_COLUMNS:
        return "BIGINT"
    if column in BOOLEAN_COLUMNS:
        return "BOOLEAN"
    if column in RATE_COLUMNS:
        return RATE_SQL_TYPE
    if column.endswith("tarihi") or column == "tarih":
        return "DATE"
    return "VARCHAR"


def build_warehouse(directory: Path, path: Path | str = ":memory:", data_digest: str | None = None) -> duckdb.DuckDBPyConnection:
    """Builds the warehouse from a CSV folder and returns the connection. An existing file is rewritten from scratch."""
    if str(path) != ":memory:":
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        for old in (path, path.with_name(path.name + ".wal")):
            old.unlink(missing_ok=True)
    con = duckdb.connect(str(path))
    for name, columns in SCHEMA.items():
        definition = ", ".join(f"{s} {sql_type(s)}" + (" PRIMARY KEY" if i == 0 else "") for i, s in enumerate(columns))
        con.execute(f"CREATE TABLE {name} ({definition})")
        csv_file = (Path(directory) / f"{name}.csv").as_posix().replace("'", "''")
        # An empty cell becomes NULL (same as sayman.data.table_io.read_csv); the header row checks the column order.
        con.execute(f"COPY {name} FROM '{csv_file}' (HEADER, DELIMITER ',', QUOTE '\"')")
        header = (Path(directory) / f"{name}.csv").read_text(encoding="utf-8").split("\n", 1)[0].strip().split(",")
        if header != columns:
            raise ValueError(f"{name}.csv sütunları şemayla uyuşmuyor: {header}")
    for macro in MACROS:
        con.execute(macro)
    con.execute("CREATE TABLE ambar_bilgisi (anahtar VARCHAR PRIMARY KEY, deger VARCHAR)")
    con.execute("INSERT INTO ambar_bilgisi VALUES ('veri_hash', ?)", [data_digest])
    return con


def open_warehouse(path: Path | str, read_only: bool = True) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(path), read_only=read_only)


def query_rows(con: duckdb.DuckDBPyConnection, sql: str, parameters: dict | None = None) -> list[dict]:
    cursor = con.execute(sql, parameters or {})
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, s)) for s in cursor.fetchall()]
