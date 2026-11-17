"""Türk kanunlarını (mevzuat.gov.tr PDF'leri) maddelere böler; kütüphaneye karıştırıcı belge olarak eklenir.

Soru setindeki hiçbir soru kanunlardan sorulmaz. Kanunlar aramayı zorlaştırmak için oradadır: Çek Kanunu, Borçlar
Kanunu ve Ticaret Kanunu'nda çek, temerrüt faizi, ayıplı mal ve fesih maddeleri vardır; bunlar şirketin politika ve
sözleşme maddeleriyle aynı konulardan söz eder.

- Her "MADDE n –" başlığı bir parçadır; parça kimliği "KANUN-6098#m5" gibidir (n kanundaki madde numarasıdır).
- Geçici maddeler, ek maddeler, sonradan araya eklenen maddeler ("MADDE 5/A") ve yürürlükten kaldırılmış (mülga)
  maddeler alınmaz.
- Maddenin hemen üstündeki kısa satır (ör. "b. Hazır olmayanlar arasında") madde başlığı olarak alınır.
- PDF'den çıkan metinde bazı kelimeler bölünmüş gelir ("tari hi"); düzeltilmez. Kanun metinleri telif kapsamı dışındadır
  (5846 sayılı Kanun md. 31).
"""

from __future__ import annotations

import re
from pathlib import Path

from sayman.belge.sentetik import TUM_ROLLER, Belge, Madde

KANUNLAR = {"5941": ("Çek Kanunu", "2009-12-14"), "6098": ("Türk Borçlar Kanunu", "2011-01-11"),
            "6102": ("Türk Ticaret Kanunu", "2011-01-13")}
_MADDE = re.compile(r"^[ \t]*(GEÇİCİ\s+|EK\s+)?MADDE\s*(\d+)(\s*/\s*[A-Z])?\s*[-–—]\s*", re.M)
EN_UZUN_BASLIK = 120


def pdf_metni(yol: Path) -> str:
    from pypdf import PdfReader
    return "\n".join(s.extract_text() for s in PdfReader(yol).pages)


def _baslik_basi(metin: str, madde_basi: int) -> tuple[int, str]:
    """Madde başlığının başladığı konum ve başlık. Başlık yoksa (madde_basi, "")."""
    once = metin[:madde_basi].rstrip()
    satir_basi = once.rfind("\n") + 1
    satir = once[satir_basi:].strip()
    if satir and len(satir) <= EN_UZUN_BASLIK and not satir.endswith((".", ":", ";", ",")) and "MADDE" not in satir:
        return satir_basi, satir
    return madde_basi, ""


def maddelere_bol(metin: str, kanun_no: str) -> tuple[Madde, ...]:
    belge_id = f"KANUN-{kanun_no}"
    eslesmeler = list(_MADDE.finditer(metin))
    baslar = [_baslik_basi(metin, e.start()) for e in eslesmeler]
    maddeler, gorulen = [], set()
    for i, e in enumerate(eslesmeler):
        if e.group(1) or e.group(3):
            continue
        no = int(e.group(2))
        bit = baslar[i + 1][0] if i + 1 < len(eslesmeler) else len(metin)
        govde = " ".join(metin[e.end():bit].split())
        if no in gorulen or len(govde) < 20 or govde.startswith("(Mülga"):
            continue
        gorulen.add(no)
        maddeler.append(Madde(f"{belge_id}#m{no}", belge_id, no, baslar[i][1] or f"Madde {no}", govde, None, None))
    return tuple(maddeler)


def kanunlar(klasor: Path) -> list[Belge]:
    """klasor/<kanun_no>.pdf dosyalarından kanun belgeleri (bulunanlar, kanun numarasına göre sıralı)."""
    belgeler = []
    for no, (ad, tarih) in sorted(KANUNLAR.items()):
        yol = klasor / f"{no}.pdf"
        if yol.exists():
            belgeler.append(Belge(f"KANUN-{no}", "mevzuat", ad, None, None, None, TUM_ROLLER, tarih,
                                  maddelere_bol(pdf_metni(yol), no)))
    return belgeler
