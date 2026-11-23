"""Phase 5: measures document search with the question set.

It can be run as many times as wanted on the tuning set (half of the documents, one side of the template pairs); the
result of each trial is appended to reports/faz5_ayar_denemeleri.json with --kaydet. The held-out set is run once with
--ayrilmis after the settings are frozen; the results of the four methods are written side by side to
reports/faz5_arama.json. No setting is changed after looking at it.

All questions are asked with the CFO role (the widest search scope: all documents, laws included).

Input:  data/belge/belgeler.jsonl, data/belge/sorular.json (scripts/phase05_generate_documents.py)
        data/belge/onbellek/gomme_bge-m3.npz (scripts/phase05_build_index.py)
        data/modeller/bge-reranker-v2-m3 (only for ranking scores that are not in the cache)

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase05_evaluate_search.py --yontem karma_sirala
    .venv\\Scripts\\python -I scripts\\phase05_evaluate_search.py --yontem karma --rrf-k 30 --kaydet "k=30 denendi"
    .venv\\Scripts\\python -I scripts\\phase05_evaluate_search.py --ayrilmis
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))

import support.phase05_scoring as puan  # noqa: E402

from sayman.documents import library  # noqa: E402
from sayman.documents.search import METHODS, Searcher, top_k  # noqa: E402

DOCUMENT_DIR = ROOT / "data" / "belge"
EMBEDDING_CACHE = DOCUMENT_DIR / "onbellek" / "gomme_bge-m3.npz"
RERANK_CACHE = DOCUMENT_DIR / "onbellek" / "sirala_bge-reranker-v2-m3.json"
MODEL = ROOT / "data" / "modeller" / "bge-reranker-v2-m3"
TUNING_TRIALS = ROOT / "reports" / "faz5_ayar_denemeleri.json"
SEARCH_REPORT = ROOT / "reports" / "faz5_arama.json"
ROLE = "cfo"
K = 10

# Settings chosen on the tuning set and frozen before the held-out set is run.
FROZEN_SETTINGS = {"rrf_k": 60, "derinlik": 50, "aday": 20}


def build_searcher(chunks, settings: dict, method: str) -> Searcher:
    semantic = reranker = None
    if method != "bm25":
        from sayman.documents.semantic import SemanticIndex, EmbeddingCache
        semantic = SemanticIndex(chunks, EmbeddingCache(EMBEDDING_CACHE))
    if method == "karma_sirala":
        from sayman.documents.reranker import Reranker
        reranker = Reranker(MODEL, RERANK_CACHE)
    return Searcher(chunks, semantic, reranker, **settings)


def evaluate(searcher: Searcher, method: str, questions: list[dict]) -> tuple[dict, dict]:
    start = time.perf_counter()
    results = {s["soru_id"]: top_k(searcher.scores(s["soru"], ROLE, method), K) for s in questions}
    duration = time.perf_counter() - start
    if searcher.reranker:
        searcher.reranker.kaydet()
    return puan.score(questions, results), {"soru_basina_ms": round(1000 * duration / len(questions), 1)}


def brief(result: dict) -> dict:
    return {k: v for k, v in result.items() if k != "kacanlar"} | {"kacan": len(result["kacanlar"])}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--yontem", choices=METHODS)
    parser.add_argument("--rrf-k", type=int, default=FROZEN_SETTINGS["rrf_k"])
    parser.add_argument("--derinlik", type=int, default=FROZEN_SETTINGS["derinlik"])
    parser.add_argument("--aday", type=int, default=FROZEN_SETTINGS["aday"])
    parser.add_argument("--kaydet", metavar="DEGISIKLIK", help="sonucu ayar denemeleri günlüğüne ekle")
    parser.add_argument("--goster", type=int, default=0, help="kaçan sorulardan ilk N tanesini göster")
    parser.add_argument("--ayrilmis", action="store_true", help="dondurulmuş ayarlarla ayrılmış seti bir kez çalıştır")
    a = parser.parse_args()

    chunks = library.to_chunks(library.read_documents(DOCUMENT_DIR / "belgeler.jsonl"))
    questions = json.loads((DOCUMENT_DIR / "sorular.json").read_text(encoding="utf-8"))

    if a.ayrilmis:
        if SEARCH_REPORT.exists():
            raise SystemExit(f"{SEARCH_REPORT} zaten var: ayrılmış set bir kez çalıştırılır.")
        searcher = build_searcher(chunks, FROZEN_SETTINGS, "karma_sirala")
        report = {"aciklama": "Ayrılmış set; ayarlar ayar setinde dondurulduktan sonra bir kez çalıştırıldı. Bütün "
                             "sorular CFO rolüyle (bütün belgeler, kanunlar dahil) soruldu. Kabul ölçütü: "
                             "karma_sirala yönteminin isabet@5 değeri ≥ 0,85.",
                 "ayarlar": FROZEN_SETTINGS, "parca": len(chunks), "yontemler": {}}
        for set_name in ("ayar", "ayrilmis"):
            for method in METHODS:
                result, _ = evaluate(searcher, method, questions[set_name])  # the duration is meaningless here (cache); see phase05_evaluate_sources
                report["yontemler"].setdefault(method, {})[set_name] = brief(result)
                if set_name == "ayrilmis":
                    report["yontemler"][method]["ayrilmis_kacanlar"] = result["kacanlar"]
        report["kabul"] = report["yontemler"]["karma_sirala"]["ayrilmis"]["isabet@5"] >= 0.85
        SEARCH_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({y: {s: r[s]["isabet@5"] for s in ("ayar", "ayrilmis")}
                          for y, r in report["yontemler"].items()}, ensure_ascii=False, indent=1))
        print("kabul:", report["kabul"])
        return

    if not a.yontem:
        raise SystemExit("--yontem ya da --ayrilmis gerekli")
    settings = {"rrf_k": a.rrf_k, "derinlik": a.derinlik, "aday": a.aday}
    searcher = build_searcher(chunks, settings, a.yontem)
    result, duration = evaluate(searcher, a.yontem, questions["ayar"])
    print(json.dumps({"yontem": a.yontem, "ayarlar": settings, **brief(result), **duration}, ensure_ascii=False, indent=1))

    if a.goster:
        for sid in result["kacanlar"][: a.goster]:
            s = next(x for x in questions["ayar"] if x["soru_id"] == sid)
            print(f"\n- {sid} [{s['stil']}] {s['soru']}\n  doğru: {searcher.chunk_by_id[s['dogru_parca']].madde_baslik}")
            for p, score in searcher.search(s["soru"], ROLE, 5, a.yontem):
                print(f"  {score:7.3f} {p.parca_id} {p.belge_baslik[:30]} / {p.madde_baslik}")

    if a.kaydet:
        trial_log = (json.loads(TUNING_TRIALS.read_text(encoding="utf-8")) if TUNING_TRIALS.exists() else
                  {"aciklama": "Faz 5 belge aramasının ayar denemeleri. Hepsi yalnızca ayar setinde (CFO rolüyle) "
                               "yapıldı. Ayrılmış set ayarlar dondurulduktan sonra bir kez çalıştırılır. Soru seti "
                               "ve puanlama (tests/phase05_questions.py, tests/phase05_scoring.py) aramadan önce yazıldı.",
                   "denemeler": []})
        trial_log["denemeler"].append({"no": len(trial_log["denemeler"]) + 1, "yontem": a.yontem,
                                    "ayarlar": settings if a.yontem != "bm25" else None,
                                    "degisiklik": a.kaydet, "ayar": brief(result)})
        TUNING_TRIALS.write_text(json.dumps(trial_log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
