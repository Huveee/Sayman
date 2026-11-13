"""Veri kapsamı: her rol, yalnızca görebileceği satırların bulunduğu salt okunur bir veritabanında çalışır.

Sunucu açılırken satış rolleri için ambardan ayrı birer kopya kurulur: yalnızca o müdürün müşterileri
(yurtiçi ya da yurt dışı, musteriler.yurtici sütunundan), onların faturaları, tahsilatları ve çekleri; bütün
döviz kurları (kamuya açık). Banka, POS, tedarikçi ve ödeme tabloları boş kopyalanır. Kopya kurulduktan sonra
salt okunur açılır. CFO ambarın kendisini salt okunur açar.

Böylece satış müdürünün bağlantısında başka bir müşterinin satırı fiziksel olarak yoktur: araçta bir hata
olsa bile kapsam dışı veri sonuca giremez.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from sayman.ambar.kurulum import MAKROLAR, baglan
from sayman.veri.cikti import SEMA

MUSTERI_TABLOLARI = ("faturalar", "tahsilatlar", "cekler")
ACIK_TABLOLAR = ("doviz_kurlari",)


def filtreler(kapsam: str) -> dict[str, str]:
    """Her tablo için WHERE koşulu (kaynak ambarda)."""
    if kapsam == "hepsi":
        return {ad: "TRUE" for ad in SEMA}
    if kapsam not in ("yurtici", "ihracat"):
        raise ValueError(f"bilinmeyen kapsam: {kapsam}")
    musteri = "yurtici" if kapsam == "yurtici" else "NOT yurtici"
    kume = f"SELECT musteri_id FROM kaynak.musteriler WHERE {musteri}"
    sonuc = {}
    for ad in SEMA:
        if ad == "musteriler":
            sonuc[ad] = musteri
        elif ad in MUSTERI_TABLOLARI:
            sonuc[ad] = f"musteri_id IN ({kume})"
        elif ad == "cek_hareketleri":
            sonuc[ad] = f"cek_id IN (SELECT cek_id FROM kaynak.cekler WHERE musteri_id IN ({kume}))"
        elif ad in ACIK_TABLOLAR:
            sonuc[ad] = "TRUE"
        else:
            sonuc[ad] = "FALSE"
    return sonuc


def kapsam_ambari_kur(ambar: Path, hedef: Path, kapsam: str) -> Path:
    """Ambarın yalnızca kapsamdaki satırlarını içeren kopyasını kurar (dosya varsa baştan yazılır)."""
    hedef = Path(hedef)
    hedef.parent.mkdir(parents=True, exist_ok=True)
    for eski in (hedef, hedef.with_name(hedef.name + ".wal")):
        eski.unlink(missing_ok=True)
    con = duckdb.connect(str(hedef))
    try:
        kaynak = Path(ambar).as_posix().replace("'", "''")
        con.execute(f"ATTACH '{kaynak}' AS kaynak (READ_ONLY)")
        for ad, kosul in filtreler(kapsam).items():
            con.execute(f"CREATE TABLE {ad} AS SELECT * FROM kaynak.{ad} WHERE {kosul}")
        con.execute("CREATE TABLE ambar_bilgisi AS SELECT * FROM kaynak.ambar_bilgisi")
        con.execute("INSERT INTO ambar_bilgisi VALUES ('kapsam', ?)", [kapsam])
        for makro in MAKROLAR:
            con.execute(makro)
        con.execute("DETACH kaynak")
    finally:
        con.close()
    return hedef


def baglantilar(ambar: Path, klasor: Path, kapsamlar) -> dict[str, duckdb.DuckDBPyConnection]:
    """Her kapsam için salt okunur bağlantı. 'hepsi' ambarın kendisidir; diğerleri klasöre kurulur."""
    sonuc = {}
    for kapsam in sorted(set(kapsamlar)):
        if kapsam == "hepsi":
            sonuc[kapsam] = baglan(ambar)
        else:
            sonuc[kapsam] = baglan(kapsam_ambari_kur(ambar, Path(klasor) / f"kapsam_{kapsam}.duckdb", kapsam))
    return sonuc
