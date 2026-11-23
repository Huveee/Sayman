"""Generates and freezes the Phase 7 question set: tool questions, unauthorized attempts, document questions, targets.

The expected values are found by running the tool functions directly within the asker's scope (the gate, the model and
the verifier are not used). With the same warehouse and the same Phase 5 question set, every run produces the same file.

Input:  data/ambar/sayman.duckdb, data/saat.json, data/belge/sorular.json
Output: reports/faz7_soru_seti.json

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase07_generate_questions.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from sayman.data.clock import read_clock  # noqa: E402
import support.phase07_questions as fs  # noqa: E402

OUTPUT = ROOT / "reports" / "faz7_soru_seti.json"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    clock = read_clock(ROOT / "data" / "saat.json")
    phase5_questions = json.loads((ROOT / "data" / "belge" / "sorular.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as temp_dir:
        o = fs.Sampler(ROOT / "data" / "ambar" / "sayman.duckdb", clock.simdi, Path(temp_dir))
        try:
            questions = fs.question_set(o, phase5_questions)
            entities = {k: sorted(v) for k, v in o.used_entities.items()}
        finally:
            o.close()
    question_ids = [s["soru_id"] for s in questions]
    assert len(question_ids) == len(set(question_ids)), "soru kimlikleri tekil olmalı"
    counts = Counter((s["set"], s["bolum"]) for s in questions)
    data = {
        "aciklama": "Faz 7 soru seti. Gerçek modelle hiçbir çalıştırmadan önce üretildi ve donduruldu; hedefler ve "
                    "hata türleri aynı anda yazıldı. Üretim: scripts/phase07_generate_questions.py, şablonlar: tests/phase07_questions.py.",
        "yazilma_tarihi": fs.WRITTEN_ON, "simulasyon_gunu": clock.simdi.isoformat(),
        "ozet": fs.digest(questions),
        "sayilar": {f"{s}/{b}": n for (s, b), n in sorted(counts.items())},
        "sablonlar": {sb.name: {"set": sb.set, "adet": sb.question_count, "kaliplar": list(sb.patterns),
                              "araclar": sorted({a["arac"] for s in questions if s.get("sablon") == sb.name
                                                 for a in s["alternatifler"]})} for sb in fs.TEMPLATES},
        "hedefler": fs.TARGETS, "hata_turleri": fs.ERROR_KINDS,
        "varliklar": entities,
        "sorular": questions,
    }
    OUTPUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(data["sayilar"], ensure_ascii=False, indent=1))
    print("özet", data["ozet"])
    print("araç dağılımı (saklanan):", dict(Counter(s["alternatifler"][0]["arac"] for s in questions
                                                     if s["bolum"] == "arac" and s["set"] == "saklanan")))
    print("rol dağılımı:", dict(Counter((s["set"], s["bolum"], s["rol"]) for s in questions)))


if __name__ == "__main__":
    main()
