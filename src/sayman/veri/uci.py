"""UCI Online Retail II verisinin okunması ve temizlenmesi.

Kaynak: Chen, D. (2012). Online Retail II [Dataset]. UCI Machine Learning Repository.
https://doi.org/10.24432/C5CG6D. Lisans: CC BY 4.0.

Her satır ya tutulur (bir kategoriyle) ya da adı konmuş bir kuralla atılır. Böylece
"girdi satırı = tutulan + atılan" mutabakatı test edilebilir.
"""

from __future__ import annotations

import csv
import datetime as dt
import gzip
import io
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Iterator

from sayman.veri.para import ondalik

HAM_SUTUNLAR = [
    "sayfa",
    "Invoice",
    "StockCode",
    "Description",
    "Quantity",
    "InvoiceDate",
    "Price",
    "Customer ID",
    "Country",
]

# Ürün kodu (5 rakamla başlayan) olmayan stok kodları için tek tek verilen karar.
# Gerçek dosyanın profiline göre (reports/faz1_uci_profil.json, 13 Kasım 2026) yazıldı.
# Burada adı geçmeyen kodlar (DCGS..., GIFT_0001_..., SP1002 gibi web mağazası ürünleri) satış olarak kalır.
STOK_KODU_KARARLARI = {
    "POST": ("tut", "Müşteriye faturalanan posta ücreti: satışın parçası"),
    "DOT": ("tut", "Web mağazası posta ücreti: perakende satışın parçası"),
    "C2": ("tut", "Müşteriye faturalanan taşıma ücreti"),
    "D": ("tut", "İskonto: C faturasında müşteriye verilen indirim, iade gibi işlenir (C'siz 5 satır, toplam ~£200, satış kalır)"),
    "PADS": ("tut", "Yastık dolgusu, birim fiyatı 0,001: önemsiz tutarlı gerçek ürün"),
    "M": ("at", "Elle düzeltme: büyükleri bir tutarın iptal edilip başka kayda yeniden girilmesi (ör. C512770/512771, "
                "£25.111). Tutulsaydı iade faturaları gerçek karşılıklarına bağlanamıyor, sahte geri ödemeler "
                "doğuyordu (2,36 milyon TL'ye kadar). Bilinen yan etki: yalnızca M ile iptal edilmiş 556444 "
                "(£38.970) satış olarak kalır, satışların yaklaşık %0,2'si"),
    "S": ("at", "Numune: müşterisiz C satırları, nakit iadesi değil"),
    "CRUK": ("at", "Hayır kurumuna ödenen komisyon: gider, müşteri iadesi değil"),
    "BANK CHARGES": ("at", "Banka masrafı: gider"),
    "AMAZONFEE": ("at", "Amazon komisyonu: gider"),
    "ADJUST": ("at", "Elle düzeltme"),
    "ADJUST2": ("at", "Elle düzeltme"),
    "B": ("at", "Şüpheli alacak düzeltmesi (A faturalarında)"),
    "TEST001": ("at", "Deneme ürünü"),
    "TEST002": ("at", "Deneme ürünü"),
}
SATIS_DISI_KODLAR = frozenset(k for k, (karar, _) in STOK_KODU_KARARLARI.items() if karar == "at")

ATILMA_KURALLARI = {
    "sayfa_tekrari": "İki Excel sayfasının çakışan tarihlerinde birebir aynı satır (ikinci sayfadaki kopya atılır)",
    "duzeltme_faturasi": "Fatura numarası A ile başlıyor: şüpheli alacak düzeltmesi, satış değil",
    "satis_disi_kod": "Stok kodu satış dışı bir gider ya da düzeltme (STOK_KODU_KARARLARI'nda 'at' olanlar)",
    "gecersiz_fiyat": "Birim fiyat sıfır ya da eksi (gerçek dosyada C'siz eksi miktarlı 3.457 depo düzeltmesi satırının "
                      "hepsinin fiyatı 0 olduğu için onlar da bu kuralla atılır)",
    "stok_duzeltmesi": "İptal faturası olmadığı hâlde miktar sıfır ya da eksi, fiyatı artı (depo düzeltmesi)",
    "tutarsiz_iade": "İptal faturası (C) ama miktar artı",
    "okunamayan_satir": "Tarih, miktar ya da fiyat okunamadı",
}

TUTULMA_KATEGORILERI = {
    "satis": "Müşterili satış satırı",
    "iade": "Müşterili iptal/iade satırı (C ile başlayan fatura, eksi miktar)",
    "perakende": "Müşteri numarası olmayan satış: perakende/POS satışı olarak işlenir",
    "perakende_iade": "Müşteri numarası olmayan iade: POS iadesi olarak işlenir",
}


@dataclass(frozen=True, slots=True)
class Kalem:
    fatura_no: str
    stok_kodu: str
    miktar: int
    tarih: dt.datetime
    fiyat: Decimal
    musteri_no: str | None
    ulke: str
    kategori: str

    @property
    def tutar(self) -> Decimal:
        return self.fiyat * self.miktar


@dataclass
class TemizlemeSonucu:
    kalemler: list[Kalem]
    girdi: int
    tutulan: Counter = field(default_factory=Counter)
    atilan: Counter = field(default_factory=Counter)

    def mutabakat_tutuyor_mu(self) -> bool:
        return self.girdi == sum(self.tutulan.values()) + sum(self.atilan.values())


def _musteri_no(deger: str) -> str | None:
    deger = (deger or "").strip()
    if not deger or deger.lower() == "nan":
        return None
    if deger.endswith(".0"):
        deger = deger[:-2]
    return deger


def _satir_anahtari(s: dict) -> tuple:
    return tuple(s.get(k, "") for k in HAM_SUTUNLAR[1:])


def _sayfa_tekrarlarini_bul(satirlar: list[dict]) -> set[int]:
    """İkinci sayfada, ilk sayfada birebir karşılığı olan satırların sırasını döner (çoklu küme)."""
    sayfalar = sorted({s["sayfa"] for s in satirlar})
    if len(sayfalar) < 2:
        return set()
    ilk = Counter(_satir_anahtari(s) for s in satirlar if s["sayfa"] == sayfalar[0])
    tekrarlar = set()
    for i, s in enumerate(satirlar):
        if s["sayfa"] == sayfalar[0]:
            continue
        anahtar = _satir_anahtari(s)
        if ilk[anahtar] > 0:
            ilk[anahtar] -= 1
            tekrarlar.add(i)
    return tekrarlar


def temizle(satirlar: Iterable[dict], satis_disi_kodlar: frozenset = SATIS_DISI_KODLAR) -> TemizlemeSonucu:
    satirlar = list(satirlar)
    tekrarlar = _sayfa_tekrarlarini_bul(satirlar)
    sonuc = TemizlemeSonucu(kalemler=[], girdi=len(satirlar))
    for i, s in enumerate(satirlar):
        if i in tekrarlar:
            sonuc.atilan["sayfa_tekrari"] += 1
            continue
        fatura_no = s["Invoice"].strip()
        stok_kodu = s["StockCode"].strip()
        iptal = fatura_no.upper().startswith("C")
        if fatura_no.upper().startswith("A"):
            sonuc.atilan["duzeltme_faturasi"] += 1
            continue
        if stok_kodu.upper() in satis_disi_kodlar:
            sonuc.atilan["satis_disi_kod"] += 1
            continue
        try:
            miktar = int(Decimal(s["Quantity"]))
            fiyat = ondalik(s["Price"])
            tarih = dt.datetime.fromisoformat(s["InvoiceDate"])
        except (InvalidOperation, ValueError):
            sonuc.atilan["okunamayan_satir"] += 1
            continue
        if fiyat <= 0:
            sonuc.atilan["gecersiz_fiyat"] += 1
            continue
        if not iptal and miktar <= 0:
            sonuc.atilan["stok_duzeltmesi"] += 1
            continue
        if iptal and miktar > 0:
            sonuc.atilan["tutarsiz_iade"] += 1
            continue
        musteri = _musteri_no(s.get("Customer ID", ""))
        if musteri:
            kategori = "iade" if iptal else "satis"
        else:
            kategori = "perakende_iade" if iptal else "perakende"
        sonuc.tutulan[kategori] += 1
        sonuc.kalemler.append(
            Kalem(fatura_no, stok_kodu, miktar, tarih, fiyat, musteri, s["Country"].strip(), kategori)
        )
    return sonuc


def ham_csv_oku(yol: Path) -> Iterator[dict]:
    """Önbelleğe alınmış ham satırları okur (.csv ya da .csv.gz, UTF-8)."""
    acici = gzip.open if str(yol).endswith(".gz") else open
    with acici(yol, "rt", encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f)


def ham_csv_yaz(satirlar: Iterable[dict], yol: Path) -> int:
    """.gz uzantılıysa sıkıştırarak yazar; gzip başlığına zaman ve dosya adı yazılmaz,
    böylece aynı içerik her seferinde aynı baytları (aynı SHA-256 özetini) verir."""
    yol.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(yol, "wb") as ham:
        gz = gzip.GzipFile(filename="", mode="wb", fileobj=ham, mtime=0) if str(yol).endswith(".gz") else None
        f = io.TextIOWrapper(gz or ham, encoding="utf-8", newline="")
        yazici = csv.DictWriter(f, fieldnames=HAM_SUTUNLAR, lineterminator="\n")
        yazici.writeheader()
        for s in satirlar:
            yazici.writerow(s)
            n += 1
        f.flush()
        f.detach()
        if gz:
            gz.close()
    return n


def _hucre_metni(deger) -> str:
    if deger is None:
        return ""
    if isinstance(deger, dt.datetime):
        return deger.isoformat(sep=" ", timespec="seconds")
    if isinstance(deger, float):
        if deger.is_integer():
            return str(int(deger))
        return repr(deger)
    return str(deger)


def xlsx_oku(yol: Path) -> Iterator[dict]:
    """Orijinal Excel dosyasını sayfa sayfa okur. openpyxl yalnızca burada gerekir."""
    import openpyxl

    kitap = openpyxl.load_workbook(yol, read_only=True, data_only=True)
    try:
        for sayfa in kitap.sheetnames:
            satirlar = kitap[sayfa].iter_rows(values_only=True)
            baslik = [str(h).strip() for h in next(satirlar)]
            for degerler in satirlar:
                if degerler is None or all(v is None for v in degerler):
                    continue
                s = {"sayfa": sayfa}
                for ad, deger in zip(baslik, degerler):
                    s[ad] = _hucre_metni(deger)
                yield {k: s.get(k, "") for k in HAM_SUTUNLAR}
    finally:
        kitap.close()
