"""Phase 8b identifier measurement, spelling table: which identifier spellings does the verifier catch? (no model is run)

Her satır, tek cümlelik yapay bir taslağı bugünkü doğrulayıcıdan geçirir. Araç sonucunda yalnızca gerçek kayıtlar
vardır: fatura F035473 (kaynak numarası 489434), çek C025774, tahsilat T00039E4A, banka hareketi H0001234.
- Uydurma kimlikler (sonuçta yok): yeni kural (kimliksiz) mı, eski sayı kuralı (kaynaksiz) mı yakalıyor, yoksa ikisi
  de kaçırıyor mu?
- Gerçek kimliğin başka yazılışları (sonuçta var): yanlışlıkla durduruluyor mu?

The paragraph above is written verbatim into the report as `aciklama`, so it stays in Turkish. In English: each row passes
a one-sentence artificial draft through today's verifier; the tool result holds only real records: invoice F035473 (source
number 489434), cheque C025774, collection T00039E4A, bank transaction H0001234. Fabricated identifiers (not in the result):
does the new rule (kimliksiz) or the old number rule (kaynaksiz) catch them, or do both miss them? Other spellings of a real
identifier (present in the result): is it stopped by mistake?

The rule is not changed during the measurement; this table shows the present coverage of the rule.

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase08b_identifier_formats.py
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sayman.assistant.verifier import verify  # noqa: E402

OUTPUT = ROOT / "reports" / "faz8b_kimlik_bicim.json"
TODAY = dt.date(2026, 9, 1)
QUESTION = "Bu kaydın numarası ne?"


def _call(tool, result, call_id):
    return {"arac": tool, "argumanlar": {}, "hata_mi": False,
            "yanit": {"cagri_id": call_id, "arac": tool, "tarih": "2026-09-01", "kapsam": "tüm şirket", "sonuc": result}}


CALLS = [
    _call("fatura_ara", {"bulunan": 1, "faturalar": [{"fatura_id": "F035473", "kaynak_fatura_no": "489434",
                                                       "fatura_tarihi": "2026-08-20", "tutar": "1200.00"}]}, "C1"),
    _call("cek_portfoyu", {"cekler": [{"cek_id": "C025774", "vade_tarihi": "2026-09-02", "tutar_tl": "9521.42"}]}, "C2"),
    _call("musteri_ozeti", {"son_tahsilatlar": [{"tahsilat_id": "T00039E4A", "tarih": "2026-08-15",
                                                  "tutar": "500.00", "fatura_id": "F035473"}]}, "C3"),
    _call("banka_hareketleri", {"hareketler": [{"hareket_id": "H0001234", "tarih": "2026-08-31",
                                                 "tutar": "-750.00"}]}, "C4"),
]

# (group, spelling, description)
ROWS = [
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
    result = []
    for group, spelling, description in ROWS:
        d = verify(f"Kaydın numarası {spelling}.", QUESTION, CALLS, TODAY)
        new_rule, old_rule = bool(d.unknown_identifiers), bool(d.unsourced)
        if group == "uydurma":
            decision = "yeni kural" if new_rule else ("eski sayı kuralı" if old_rule else "kaçtı")
        else:
            decision = "yanlış durdurma" if (new_rule or old_rule) else "geçti"
        result.append({"grup": group, "yazilis": spelling, "aciklama": description, "kimliksiz": d.unknown_identifiers,
                      "kaynaksiz": [x.metin for x in d.unsourced], "karar": decision})
        print(f"{group:8s} {spelling:14s} {decision:18s} {description}")
    OUTPUT.write_text(json.dumps({"aciklama": __doc__.split("\n\n")[1].replace("\n", " "), "satirlar": result},
                                ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
