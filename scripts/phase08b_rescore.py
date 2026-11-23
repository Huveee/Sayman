"""Phase 8b: re-scores the recorded Phase 7/8 answers with the corrected scorer (the model is not run).

Puanlayıcıda iki değişiklik var (README Faz 8b): fatura kimliği anahtarı faturanın kaynak sistem numarasını da kabul
eder; "alamam", "alamıyorum", "veremiyorum" ret sayılır. Sonuçlar görüldükten sonra yapılan bir değişiklik olduğu için
üç modelin (kütüphane, kendi temel, ince ayarlı) iki setteki bütün cevapları aynı puanlayıcıyla yeniden puanlanır ve
eski ile yeni sayılar yan yana yazılır. Faz 7 ve Faz 8 rapor dosyalarına dokunulmaz; çıktı ayrı bir dosyadır.

The paragraph above is written verbatim into the report as `aciklama`, so it stays in Turkish. In English: the scorer has
two changes (README Phase 8b): the invoice identifier key also accepts the source system number of the invoice, and
"alamam", "alamıyorum", "veremiyorum" count as a refusal. Because this change was made after the results were seen, all the
answers of the three models (library, own base, fine-tuned) on the two sets are re-scored with the same scorer and the old
and new counts are written side by side. The Phase 7 and Phase 8 report files are not touched; the output is a separate file.

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase08b_rescore.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import phase07_evaluate  # noqa: E402

REPORTS = ROOT / "reports"
OUTPUT = REPORTS / "faz8b_yeniden_puanlama.json"
LABELS = ("faz7", "faz8_temel_kendi", "faz8_ayarli")
SET_NAMES = ("gelistirme", "saklanan")
MEASURES = [("arac", "cevap_dogrulugu"), ("arac", "dogrulanmis_yanlis_sayi"), ("belge", "cevap_dogrulugu"),
           ("yetkisiz", "reddetti"), ("yetkisiz", "sizinti")]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    data = json.loads(phase07_evaluate.QUESTION_SET.read_text(encoding="utf-8"))
    scopes = phase07_evaluate.scope_record_ids()
    chunk_roles = {pid: p.roller for pid, p in phase07_evaluate.chunks().items()}
    this_year = int(data["simulasyon_gunu"][:4])
    result = {"aciklama": __doc__.split("\n\n")[1].replace("\n", " "), "kosular": {}}
    for label in LABELS:
        for set_name in SET_NAMES:
            old_report = json.loads((REPORTS / f"{label}_olcum_{set_name}.json").read_text(encoding="utf-8"))
            questions = [s for s in data["sorular"] if s["set"] == set_name]
            records = phase07_evaluate.read_jsonl(REPORTS / f"{label}_{set_name}.jsonl")
            scores, summary = phase07_evaluate.score_all(questions, records, scopes, chunk_roles, this_year)
            old_scores = {p["soru_id"]: p for p in old_report["sorular"]}
            changed = []
            for p in scores:
                e = old_scores[p["soru_id"]]
                for field in ("cevap_dogru", "yanlis_sayi_suphesi", "reddetti", "sizinti"):
                    if field in p and e.get(field) != p[field]:
                        changed.append({"soru_id": p["soru_id"], "alan": field, "eski": e.get(field), "yeni": p[field]})
            measures = {f"{b}.{m}": {"eski": old_report["ozet"][b][m], "yeni": summary[b][m]} for b, m in MEASURES}
            result["kosular"][f"{label}_{set_name}"] = {"olculer": measures, "degisen": changed}
            print(f"{label}_{set_name}:", {k: f"{v['eski']} → {v['yeni']}" for k, v in measures.items()
                                           if v["eski"] != v["yeni"]} or "değişmedi",
                  [(d["soru_id"], d["alan"]) for d in changed])
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("yazıldı:", OUTPUT.relative_to(ROOT))


if __name__ == "__main__":
    main()
