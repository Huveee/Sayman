"""Data scope: each role runs on a read-only database that holds only the rows it may see.

When the server starts, a separate copy of the warehouse is built for each sales role: only that manager's
customers (domestic or foreign, from the musteriler.yurtici column), their invoices, collections and cheques,
plus all exchange rates (public). The bank, POS, supplier and payment tables are copied empty. After the copy is
built it is opened read-only. The CFO opens the warehouse itself read-only.

So in the sales manager's connection another customer's row physically does not exist: even if there is a bug
in a tool, out-of-scope data cannot enter the result.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from sayman.warehouse.loader import MACROS, open_warehouse
from sayman.data.table_io import SCHEMA

CUSTOMER_TABLES = ("faturalar", "tahsilatlar", "cekler")
PUBLIC_TABLES = ("doviz_kurlari",)


def scope_filters(scope: str) -> dict[str, str]:
    """The WHERE condition for each table (in the source warehouse)."""
    if scope == "hepsi":
        return {table: "TRUE" for table in SCHEMA}
    if scope not in ("yurtici", "ihracat"):
        raise ValueError(f"bilinmeyen kapsam: {scope}")
    customer_condition = "yurtici" if scope == "yurtici" else "NOT yurtici"
    customer_ids = f"SELECT musteri_id FROM kaynak.musteriler WHERE {customer_condition}"
    result = {}
    for table in SCHEMA:
        if table == "musteriler":
            result[table] = customer_condition
        elif table in CUSTOMER_TABLES:
            result[table] = f"musteri_id IN ({customer_ids})"
        elif table == "cek_hareketleri":
            result[table] = f"cek_id IN (SELECT cek_id FROM kaynak.cekler WHERE musteri_id IN ({customer_ids}))"
        elif table in PUBLIC_TABLES:
            result[table] = "TRUE"
        else:
            result[table] = "FALSE"
    return result


def build_scope_warehouse(warehouse: Path, target: Path, scope: str) -> Path:
    """Builds a copy of the warehouse that holds only the rows in scope (rewritten from scratch if the file exists)."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    for stale_file in (target, target.with_name(target.name + ".wal")):
        stale_file.unlink(missing_ok=True)
    con = duckdb.connect(str(target))
    try:
        source = Path(warehouse).as_posix().replace("'", "''")
        con.execute(f"ATTACH '{source}' AS kaynak (READ_ONLY)")
        for table, condition in scope_filters(scope).items():
            con.execute(f"CREATE TABLE {table} AS SELECT * FROM kaynak.{table} WHERE {condition}")
        con.execute("CREATE TABLE ambar_bilgisi AS SELECT * FROM kaynak.ambar_bilgisi")
        con.execute("INSERT INTO ambar_bilgisi VALUES ('kapsam', ?)", [scope])
        for macro in MACROS:
            con.execute(macro)
        con.execute("DETACH kaynak")
    finally:
        con.close()
    return target


def connections(warehouse: Path, directory: Path, scopes) -> dict[str, duckdb.DuckDBPyConnection]:
    """A read-only connection for each scope. 'hepsi' is the warehouse itself; the others are built in the folder."""
    result = {}
    for scope in sorted(set(scopes)):
        if scope == "hepsi":
            result[scope] = open_warehouse(warehouse)
        else:
            result[scope] = open_warehouse(build_scope_warehouse(warehouse, Path(directory) / f"kapsam_{scope}.duckdb", scope))
    return result
