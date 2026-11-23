"""Phase 5: generates the document library and the question set, and writes their digests to the report.

The synthetic documents are derived from the company data (data/uretilen): information such as due date, credit limit
and cheque in the supplier and customer contracts is consistent with the data. The questions are generated from the
templates in tests/support/phase05_questions.py. The three laws are also added to the library as distractor documents
(they receive no questions).

The digests in the report (`belge_ozeti`: synthetic documents, `soru_ozeti`, `kutuphane_ozeti`: laws included) were
recorded when the question set was frozen; the tests compare the digest of the regenerated set with them. If the
document generator or the question templates change, this report changes too and the difference shows up in git.

Input:  data/uretilen/*.csv (scripts/phase01_generate_data.py)
        data/ham/mevzuat/5941.pdf, 6098.pdf, 6102.pdf (scripts/phase05_download_legislation.py)
Output: data/belge/belgeler.jsonl (laws included), data/belge/sorular.json, reports/faz5_soru_seti.json

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase05_generate_documents.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import support.phase05_questions as fs  # noqa: E402

from sayman.documents import library  # noqa: E402
from sayman.documents.legislation import LAWS, laws  # noqa: E402
from sayman.documents.synthetic import generate  # noqa: E402
from sayman.data.table_io import read_csv  # noqa: E402

GENERATED = ROOT / "data" / "uretilen"
LEGISLATION = ROOT / "data" / "ham" / "mevzuat"
OUTPUT = ROOT / "data" / "belge"
REPORT = ROOT / "reports" / "faz5_soru_seti.json"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    documents = generate(read_csv(GENERATED))
    law_documents = laws(LEGISLATION)
    if len(law_documents) != len(LAWS):
        raise SystemExit(f"{LEGISLATION} içinde {len(LAWS)} kanun PDF'i bekleniyordu, {len(law_documents)} bulundu")
    library.write_documents(documents + law_documents, OUTPUT / "belgeler.jsonl")
    questions = fs.question_set(documents)
    (OUTPUT / "sorular.json").write_text(json.dumps(questions, ensure_ascii=False, indent=1), encoding="utf-8")

    def set_digest(s):
        return {"soru": len(s), "ture_gore": dict(sorted(Counter(x["tur"] for x in s).items())),
                "stile_gore": dict(sorted(Counter(x["stil"] for x in s).items())),
                "belge": len({x["belge_id"] for x in s}),
                "sans_tabani_isabet@5": fs.chance_baseline(s, documents)}

    report = {
        "aciklama": "Faz 5 belge kütüphanesi ve soru seti. Özetler soru seti dondurulduğunda kaydedildi.",
        "belge": len(documents), "madde": sum(len(b.maddeler) for b in documents),
        "ture_gore": dict(sorted(Counter(b.tur for b in documents).items())),
        "belge_ozeti": fs.documents_digest(documents), "soru_ozeti": fs.digest(questions),
        "karistirici_kanunlar": {b.belge_id: {"ad": b.baslik, "madde": len(b.maddeler), "pdf_sha256": hashlib.sha256(
            (LEGISLATION / f"{b.belge_id.split('-')[1]}.pdf").read_bytes()).hexdigest()} for b in law_documents},
        "kutuphane": {"belge": len(documents) + len(law_documents),
                      "madde": sum(len(b.maddeler) for b in documents + law_documents)},
        "kutuphane_ozeti": fs.documents_digest(documents + law_documents),
        "setler": {name: set_digest(s) for name, s in questions.items()},
        "sans_tabani_aciklama": "Sözleşme sorularında arama yalnızca doğru sözleşmeyi bulup maddelerini rastgele "
                                "sıralasa beklenen isabet@5. Politika soruları dahil değildir.",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
