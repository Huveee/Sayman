"""Faz 8b kimlik ölçümü, biçim tablosu: doğrulayıcı hangi kimlik yazılışlarını yakalıyor? (model çalıştırılmaz)

Her satır, tek cümlelik yapay bir taslağı bugünkü doğrulayıcıdan geçirir. Araç sonucunda yalnızca gerçek kayıtlar
vardır: fatura F035473 (kaynak numarası 489434), çek C025774, tahsilat T00039E4A, banka hareketi H0001234.
- Uydurma kimlikler (sonuçta yok): yeni kural (kimliksiz) mı, eski sayı kuralı (kaynaksiz) mı yakalıyor, yoksa ikisi
  de kaçırıyor mu?
- Gerçek kimliğin başka yazılışları (sonuçta var): yanlışlıkla durduruluyor mu?

Kural ölçüm boyunca değiştirilmez; bu tablo kuralın bugünkü kapsamını gösterir.

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\faz8b_kimlik_bicim.py
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))

from sayman.assistant.verifier import dogrula  # noqa: E402

CIKTI = KOK / "reports" / "faz8b_kimlik_bicim.json"
BUGUN = dt.date(2026, 9, 1)
SORU = "Bu kaydın numarası ne?"


def _cagri(arac, sonuc, cid):
    return {"arac": arac, "argumanlar": {}, "hata_mi": False,
            "yanit": {"cagri_id": cid, "arac": arac, "tarih": "2026-09-01", "kapsam": "tüm şirket", "sonuc": sonuc}}


CAGRILAR = [
    _cagri("fatura_ara", {"bulunan": 1, "faturalar": [{"fatura_id": "F035473", "kaynak_fatura_no": "489434",
                                                       "fatura_tarihi": "2026-08-20", "tutar": "1200.00"}]}, "C1"),
    _cagri("cek_portfoyu", {"cekler": [{"cek_id": "C025774", "vade_tarihi": "2026-09-02", "tutar_tl": "9521.42"}]}, "C2"),
    _cagri("musteri_ozeti", {"son_tahsilatlar": [{"tahsilat_id": "T00039E4A", "tarih": "2026-08-15",
                                                  "tutar": "500.00", "fatura_id": "F035473"}]}, "C3"),
    _cagri("banka_hareketleri", {"hareketler": [{"hareket_id": "H0001234", "tarih": "2026-08-31",
                                                 "tutar": "-750.00"}]}, "C4"),
]

# (grup, yazılış, açıklama)
SATIRLAR = [
    ("uydurma", "F035999", "sistem biçimi, fatura"),
    ("uydurma", "C560878", "kaynak biçimi (Faz 8'de görülen)"),
    ("uydurma", "A000999", "sistem biçimi, tedarikçi faturası"),
    ("uydurma", "H0009999", "sistem biçimi, banka hareketi"),
    ("uydurma", "f035999", "küçük harf"),
    ("uydurma", "FTR2026001", "uzun önek"),
    ("uydurma", "F-2025-089", "tireli (Faz 8b'de görülen)"),
    ("uydurma", "KUR-001", "tireli, kısa (Faz 8'de görülen)"),
    ("uydurma", "C-025999", "tireli çek"),
    ("uydurma", "F 035999", "boşluklu"),
    ("uydurma", "#F035999", "diyez önekli"),
    ("uydurma", "F035999/2026", "eğik çizgi sonekli"),
    ("uydurma", "T0003A9E4", "tahsilat biçimi (harf-rakam karışık)"),
    ("uydurma", "489999", "yalnızca rakam (kaynak numarası gibi)"),
    ("gercek", "F035473", "sonuçtaki gibi"),
    ("gercek", "f035473", "küçük harf"),
    ("gercek", "F 035473", "boşluklu"),
    ("gercek", "#F035473", "diyez önekli"),
    ("gercek", "F-035473", "tireli"),
    ("gercek", "C025774", "çek, sonuçtaki gibi"),
    ("gercek", "T00039E4A", "tahsilat, sonuçtaki gibi"),
    ("gercek", "H0001234", "banka hareketi, sonuçtaki gibi"),
    ("gercek", "489434", "kaynak numarası, sonuçtaki gibi"),
]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sonuc = []
    for grup, yazilis, aciklama in SATIRLAR:
        d = dogrula(f"Kaydın numarası {yazilis}.", SORU, CAGRILAR, BUGUN)
        yeni, eski = bool(d.kimliksiz), bool(d.kaynaksiz)
        if grup == "uydurma":
            karar = "yeni kural" if yeni else ("eski sayı kuralı" if eski else "kaçtı")
        else:
            karar = "yanlış durdurma" if (yeni or eski) else "geçti"
        sonuc.append({"grup": grup, "yazilis": yazilis, "aciklama": aciklama, "kimliksiz": d.kimliksiz,
                      "kaynaksiz": [x.metin for x in d.kaynaksiz], "karar": karar})
        print(f"{grup:8s} {yazilis:14s} {karar:18s} {aciklama}")
    CIKTI.write_text(json.dumps({"aciklama": __doc__.split("\n\n")[1].replace("\n", " "), "satirlar": sonuc},
                                ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
