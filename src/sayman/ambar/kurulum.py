"""Veri ambarı: data/uretilen/*.csv tablolarını tek bir DuckDB dosyasına yükler.

Ambarda yalnızca araçların okuyabileceği tablolar (cikti.SEMA) bulunur; cevap anahtarı
(data/anahtar) ve simülasyon saati (data/saat.json) ambara girmez. Saat her metriğe tarih
parametresi olarak verilir; böylece tek saat data/saat.json'dur ve ambar salt okunur açılabilir.

Sütun tipleri SEMA'daki adlardan açıkça kurulur, CSV'den tahmin edilmez: tutarlar tamsayı kuruş
(BIGINT), kurlar tam ondalık (DECIMAL), tarihler DATE. Kayan noktalı sayı kullanılmaz.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from sayman.veri.cikti import MANTIKSAL_SUTUNLAR, SEMA, TAM_SAYI_SUTUNLARI

KUR_SUTUNLARI = {"kur", "alis"}
KUR_TIPI = "DECIMAL(18,6)"

# Eşitlikte çift tarafa yuvarlama (sayman.veri.para.kurusa ile aynı kural). DuckDB'nin round_even'ı
# kayan noktaya çevirdiği için kullanılmaz; burada tam ondalık aritmetikle yapılır.
MAKROLAR = (
    "CREATE OR REPLACE MACRO yuvarla_cift(x) AS CAST(floor(x) + CASE WHEN x - floor(x) > 0.5 THEN 1 "
    "WHEN x - floor(x) < 0.5 THEN 0 WHEN floor(x) % 2 = 0 THEN 0 ELSE 1 END AS BIGINT)",
    # kuruş x kur -> kuruş (TL karşılığı)
    "CREATE OR REPLACE MACRO kur_carp(kurus, kur) AS "
    "yuvarla_cift(CAST(kurus AS DECIMAL(38,0)) * CAST(kur AS DECIMAL(18,6)))",
)


def sql_tipi(sutun: str) -> str:
    if sutun in TAM_SAYI_SUTUNLARI:
        return "BIGINT"
    if sutun in MANTIKSAL_SUTUNLAR:
        return "BOOLEAN"
    if sutun in KUR_SUTUNLARI:
        return KUR_TIPI
    if sutun.endswith("tarihi") or sutun == "tarih":
        return "DATE"
    return "VARCHAR"


def ambar_kur(klasor: Path, yol: Path | str = ":memory:", veri_hash: str | None = None) -> duckdb.DuckDBPyConnection:
    """CSV klasöründen ambarı kurar ve bağlantıyı döner. Dosya varsa baştan yazılır."""
    if str(yol) != ":memory:":
        yol = Path(yol)
        yol.parent.mkdir(parents=True, exist_ok=True)
        for eski in (yol, yol.with_name(yol.name + ".wal")):
            eski.unlink(missing_ok=True)
    con = duckdb.connect(str(yol))
    for ad, sutunlar in SEMA.items():
        tanim = ", ".join(f"{s} {sql_tipi(s)}" + (" PRIMARY KEY" if i == 0 else "") for i, s in enumerate(sutunlar))
        con.execute(f"CREATE TABLE {ad} ({tanim})")
        dosya = (Path(klasor) / f"{ad}.csv").as_posix().replace("'", "''")
        # Boş hücre NULL olur (cikti.csv_oku ile aynı); başlık satırı sütun sırasını doğrular.
        con.execute(f"COPY {ad} FROM '{dosya}' (HEADER, DELIMITER ',', QUOTE '\"')")
        baslik = (Path(klasor) / f"{ad}.csv").read_text(encoding="utf-8").split("\n", 1)[0].strip().split(",")
        if baslik != sutunlar:
            raise ValueError(f"{ad}.csv sütunları şemayla uyuşmuyor: {baslik}")
    for makro in MAKROLAR:
        con.execute(makro)
    con.execute("CREATE TABLE ambar_bilgisi (anahtar VARCHAR PRIMARY KEY, deger VARCHAR)")
    con.execute("INSERT INTO ambar_bilgisi VALUES ('veri_hash', ?)", [veri_hash])
    return con


def baglan(yol: Path | str, salt_okunur: bool = True) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(yol), read_only=salt_okunur)


def satirlar(con: duckdb.DuckDBPyConnection, sql: str, parametreler: dict | None = None) -> list[dict]:
    imlec = con.execute(sql, parametreler or {})
    adlar = [d[0] for d in imlec.description]
    return [dict(zip(adlar, s)) for s in imlec.fetchall()]
