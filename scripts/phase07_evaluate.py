"""Phase 7 evaluation: runs the question set through the orchestrator, writes each question to a JSONL line, then scores and reports.

Models:
- gercek: qwen3.5:9b in Ollama (Phase 0 settings) and the real semantic search. Takes a long time; resumes where it left off.
- kahin: a fake model that knows every answer (acceptance test of the setup: it must get full marks on every metric).
- yanlis_arac, bir_hane, hep_ret, baska_musteri, yaziyla: negative controls (src/sayman/evaluation/oracle.py).

Everything goes through the real gate (the Sayman server), the real orchestrator and the real verifier. Identity cards
are signed with a random key specific to this run (the key in .env is not used).

Resuming: questions already in the JSONL are skipped. Every line carries digests of the system, the code, the question
set and the targets; if the lines in the file differ from the current ones the run stops (the system, the question set
or the targets must not be changed in the middle of a measurement); --yeniden rewrites the file from scratch.

Infrastructure rules (Phase 8 measures with the same rules):
- If a model call fails (model_hatasi, e.g. Ollama went down) no line is written; the question is asked again after
  60 seconds, and if all three attempts fail the run stops and is continued from where it left off with the same
  command. This way an Ollama that crashes in the middle of the measurement does not write all the remaining
  questions as "failed".
- If a model call is excessively slow (it took longer than processing the whole prompt at 10 tokens per second and the
  answer at 1 token per second; 3-5 times slower than the Phase 0 speed) the question is asked again once after
  30 seconds and the second result is written. In the development smoke test one call produced 0.16 tokens per second
  under memory pressure (normally ~5) and took 651 seconds. Re-asked questions are counted in the line ("altyapi")
  and in the report.
Duration, step and context limits are the model's result and are written.

Output:
- gercek: reports/faz7_<set>.jsonl (every question) and reports/faz7_olcum_<set>.json (report)
- Phase 8, fine-tuned model: --ollama-model <name> --etiket <prefix> gives reports/<prefix>_<set>.jsonl and
  reports/<prefix>_olcum_<set>.json (the Phase 7 files are not touched)
- oracle and controls: data/olcum/<set>_<model>.jsonl; with --kahin-hepsi reports/faz7_kahin.json
- Phase 8b new set: --soru-seti reports/faz8b_soru_seti.json --set yeni (oracle summary reports/faz8b_kahin.json)
- Phase 9 generated tools: --kol otomatik. The tools are produced from the fake API with fastmcp
  (sayman.experiment.generated_tools), and the gate is the generated-tools server; the orchestrator, system prompt and
  verifier are the same. The oracle executes the frozen plan (reports/faz9_siniflar.json); the oracle and control files
  are written separately with the "_oto" suffix.

Usage (from the project root):
    .venv\\Scripts\\python -I -u scripts\\phase07_evaluate.py --kahin-hepsi
    .venv\\Scripts\\python -I -u scripts\\phase07_evaluate.py --set gelistirme --model gercek
    .venv\\Scripts\\python -I -u scripts\\phase07_evaluate.py --set saklanan --model gercek
    .venv\\Scripts\\python -I -u scripts\\phase07_evaluate.py --set saklanan --model gercek --tekrar 10
    .venv\\Scripts\\python -I -u scripts\\phase07_evaluate.py --set saklanan --model gercek --yalniz-rapor
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import secrets
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sayman.warehouse.loader import open_warehouse, query_rows  # noqa: E402
from sayman.tools.audit_log import AuditLog  # noqa: E402
from sayman.tools.identity import generate_card  # noqa: E402
from sayman.tools.server import SaymanServer  # noqa: E402
from sayman.tools.permissions import read_permissions  # noqa: E402
from sayman.assistant.orchestrator import Assistant, Settings  # noqa: E402
from sayman.documents import library  # noqa: E402
from sayman.evaluation import fingerprint, scoring  # noqa: E402
from sayman.evaluation.oracle import CONTROLS, Oracle, OracleSearcher  # noqa: E402
from sayman.data.clock import read_clock  # noqa: E402

QUESTION_SET = ROOT / "reports" / "faz7_soru_seti.json"
WAREHOUSE = ROOT / "data" / "ambar" / "sayman.duckdb"
DOCUMENT_DIR = ROOT / "data" / "belge"
WORK_DIR = ROOT / "data" / "olcum"
MODELS = ("gercek", "kahin", *CONTROLS)
# Phase 8: the fine-tuned model is measured with the same setup under another Ollama model name. LABEL is the prefix of file names
# (Phase 7 files use "faz7"; the fine-tuned model's files are separate and new, never written over the Phase 7 files).
OLLAMA_MODEL: str | None = None
LABEL = "faz7"
# Phase 9: tool arm. "el" is hand-designed tools (Phase 3-8b); "otomatik" is tools generated from the fake API.
ARM = "el"
ARM_DIGEST: str | None = None
CLASSES = ROOT / "reports" / "faz9_siniflar.json"
GENERATED_TOOL_LIST_TOKENS = 2300  # 2209 was measured in Ollama (reports/faz9_boyut.json); same as scripts/phase09_classify.py


def jsonl_path(set_name: str, model: str, repeat: bool = False) -> Path:
    if model == "gercek":
        return ROOT / "reports" / f"{LABEL}_{set_name}{'_tekrar' if repeat else ''}.jsonl"
    return WORK_DIR / f"{set_name}_{model}{'_oto' if ARM == 'otomatik' else ''}{'_tekrar' if repeat else ''}.jsonl"


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(s) for s in path.read_text(encoding="utf-8").splitlines() if s.strip()]


def scope_record_ids() -> dict:
    """Record identifiers the sales roles can see (for the scope check; taken from the warehouse itself)."""
    con = open_warehouse(WAREHOUSE)
    try:
        scope_sets = {}
        for role, condition in (("satis_yurtici", "m.yurtici"), ("satis_ihracat", "NOT m.yurtici")):
            m = f"SELECT musteri_id FROM musteriler m WHERE {condition}"
            scope_set = {"doviz_kurlari": None}
            scope_set["musteriler"] = {r["musteri_id"] for r in query_rows(con, m)}
            for table, identifier in (("faturalar", "fatura_id"), ("tahsilatlar", "tahsilat_id"), ("cekler", "cek_id")):
                scope_set[table] = {r[identifier] for r in query_rows(con, f"SELECT {identifier} FROM {table} WHERE musteri_id IN ({m})")}
            scope_sets[role] = scope_set
        return scope_sets
    finally:
        con.close()


def chunks() -> dict:
    return {p.parca_id: p for p in library.to_chunks(library.read_documents(DOCUMENT_DIR / "belgeler.jsonl"))}


def is_infrastructure_slow(turn) -> bool:
    """Did a model call take longer than processing the whole prompt at 10 tokens/s and the answer at 1 token/s (plus 10 s)?"""
    return any(t.get("sure_s") and t.get("istem") is not None and t.get("uretim") is not None
               and t["sure_s"] > t["istem"] / 10 + t["uretim"] + 10 for t in turn.tokens)


def set_fingerprint() -> dict:
    import hashlib
    data = json.loads(QUESTION_SET.read_text(encoding="utf-8"))
    target = hashlib.sha256(json.dumps(data["hedefler"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {"soru_seti_ozeti": data["ozet"], "hedef_ozeti": target}


def _fingerprint(record: dict) -> tuple:
    return tuple(record.get(k) for k in ("sistem_ozeti", "kod_ozeti", "soru_seti_ozeti", "hedef_ozeti", "kol_ozeti"))


def _record(question: dict, turn, system: dict, model_fingerprint: dict, set_digests: dict) -> dict:
    return {
        "soru_id": question["soru_id"], "sistem_ozeti": system["ozet"], "kod_ozeti": system["kod_ozeti"], **set_digests,
        **({"kol": ARM, "kol_ozeti": ARM_DIGEST} if ARM != "el" else {}),
        "model_ozeti": model_fingerprint["ozet"], "durum": turn.status, "cevap": turn.answer, "sure_s": turn.sure_s,
        "model_cagrisi": turn.model_calls, "neden": turn.reason,
        "cagrilar": [{"arac": c["arac"], "argumanlar": c["argumanlar"], "hata": c["yanit"].get("hata"),
                      "yanit": c["yanit"]} for c in turn.calls],
        "taslaklar": [{"metin": t, "gecerli": d.gecerli, "kaynaksiz": [x.metin for x in d.unsourced],
                       "gecersiz_alinti": [x.reason for x in d.invalid_citations], "alinti_eksik": d.citation_missing,
                       # Phase 8b: the decision of the old rules and the new rules
                       "eski_gecerli": d.legacy_valid, "kimliksiz": d.unknown_identifiers, "sahipsiz": d.ownerless,
                       "kapsam_eksik": d.missing_scope, **step}
                      for t, d, step in zip(turn.drafts, turn.verifications, turn.draft_steps)],
        "kapsam_notu": turn.scope_note,
        "tokenlar": turn.tokens, "zaman": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def run(questions: list[dict], model_name: str, path: Path, restart: bool = False, quiet: bool = False) -> None:
    system = fingerprint.system()
    model_fingerprint = fingerprint.model(ollama_digest=model_name == "gercek", model_name=OLLAMA_MODEL)
    if model_name != "gercek":
        model_fingerprint = {"model": model_name, "ozet": model_name}
    if restart and path.exists():
        path.unlink()
    set_digests = set_fingerprint()
    current = (system["ozet"], system["kod_ozeti"], set_digests["soru_seti_ozeti"], set_digests["hedef_ozeti"], ARM_DIGEST)
    previous = read_jsonl(path)
    if {_fingerprint(s) for s in previous} - {current}:
        raise SystemExit(f"{path.name}: önceki satırlar başka bir sistem, soru seti ya da hedeflerle üretilmiş; "
                         "--yeniden ile baştan başlatın.")
    done = {s["soru_id"] for s in previous}
    remaining = [s for s in questions if s["soru_id"] not in done]
    if not remaining:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    clock = read_clock(ROOT / "data" / "saat.json")
    key = secrets.token_bytes(32)
    all_chunks = chunks()
    if model_name == "gercek":
        from sayman.assistant.model import OllamaModel
        from sayman.assistant.orchestrator import tool_definitions
        from sayman.documents.search import build_searcher
        model = OllamaModel(model=OLLAMA_MODEL) if OLLAMA_MODEL else OllamaModel()
        searcher = build_searcher(DOCUMENT_DIR, "anlam")  # also loaded in the generated-tools arm, so that the memory state is the same in both arms
        start = time.perf_counter()
        model([{"role": "user", "content": "Merhaba"}], tool_definitions(), 600)  # so that the model gets loaded into memory
        searcher.search("ödeme vadesi", "cfo", 1, "anlam")
        print(f"ısınma {time.perf_counter() - start:.1f} sn", flush=True)
    elif ARM == "otomatik":
        from sayman.experiment.oracle import GeneratedOracle
        if model_name != "kahin":
            raise SystemExit("otomatik kolda yalnızca kâhin var (kontroller elle araçların kâhinine bağlı)")
        plans = {k["soru_id"]: k for k in json.loads(CLASSES.read_text(encoding="utf-8"))["sorular"]}
        model, searcher = GeneratedOracle(plans), None
    else:
        model = Oracle(None if model_name == "kahin" else model_name, all_chunks)
        searcher = OracleSearcher(all_chunks)
    with tempfile.TemporaryDirectory() as temp_dir:
        if ARM == "otomatik":
            from sayman.experiment.generated_tools import GeneratedServer
            server = GeneratedServer(warehouse=WAREHOUSE, clock=lambda: clock, key=key, permissions=read_permissions(),
                                    audit_log=AuditLog(WORK_DIR / f"denetim_{path.stem}.jsonl"),
                                    scope_dir=Path(temp_dir) / "kapsam")
            settings = Settings(arac_listesi_token=GENERATED_TOOL_LIST_TOKENS)
        else:
            server = SaymanServer(warehouse=WAREHOUSE, clock=lambda: clock, key=key, permissions=read_permissions(),
                                    audit_log=AuditLog(WORK_DIR / f"denetim_{path.stem}.jsonl"),
                                    scope_dir=Path(temp_dir) / "kapsam", belge=searcher, search_method="anlam")
            settings = Settings()
        try:
            assistant = Assistant(server, model, settings, audit_log=AuditLog(WORK_DIR / f"turlar_{path.stem}.jsonl"))
            if ARM == "otomatik":
                assistant.tools = server.tool_definitions()
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                for i, s in enumerate(remaining, 1):
                    if hasattr(model, "soru"):
                        model.soru = s
                    if hasattr(searcher, "soru"):
                        searcher.soru = s
                    infrastructure, error_count = [], 0
                    while True:
                        turn = assistant.ask(s["soru"], generate_card(s["kullanici"], key, 900))
                        if turn.status == "model_hatasi":
                            error_count += 1
                            if error_count == 3:
                                raise SystemExit(f"{s['soru_id']}: model üç kez hata verdi; satır yazılmadı. Ollama'yı "
                                                 "kontrol edip aynı komutla kaldığı yerden sürdürün.")
                            print(f"{s['soru_id']}: model hatası ({turn.reason}); 60 sn sonra yeniden", flush=True)
                            time.sleep(60)
                            continue
                        if is_infrastructure_slow(turn) and not infrastructure:
                            infrastructure.append({"durum": turn.status, "sure_s": turn.sure_s, "tokenlar": turn.tokens})
                            print(f"{s['soru_id']}: aşırı yavaş model çağrısı ({turn.sure_s} sn); 30 sn sonra yeniden",
                                  flush=True)
                            time.sleep(30)
                            continue
                        break
                    record = _record(s, turn, system, model_fingerprint, set_digests)
                    if infrastructure:
                        record["altyapi"] = {"yeniden_soruldu": infrastructure, "ikincisi_de_yavas": is_infrastructure_slow(turn)}
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    f.flush()
                    if not quiet:
                        print(f"{len(done) + i:3d}/{len(questions)} {s['soru_id']:28s} {turn.status:13s} "
                              f"{turn.sure_s:6.1f} sn", flush=True)
        finally:
            server.close()


def score_all(questions: list[dict], records: list[dict], scope_sets: dict, chunk_roles: dict, this_year: int):
    by_id = {s["soru_id"]: s for s in questions}
    record = {k["soru_id"]: k for k in records}
    scores = [scoring.score(s, record[s["soru_id"]], scope_sets, chunk_roles, this_year)
               for s in questions if s["soru_id"] in record]
    return scores, scoring.summarize(by_id, scores)


def compare_repeat(original: list[dict], repeat: list[dict]) -> dict:
    a = {k["soru_id"]: k for k in original}
    result_rows = []
    for t in repeat:
        k = a.get(t["soru_id"])
        if k is None:
            continue
        call_pairs = lambda x: [(c["arac"], json.dumps(c["argumanlar"], sort_keys=True)) for c in x["cagrilar"]]  # noqa: E731
        result_rows.append({"soru_id": t["soru_id"], "ayni_durum": k["durum"] == t["durum"],
                          "ayni_cevap": k["cevap"] == t["cevap"], "ayni_cagrilar": call_pairs(k) == call_pairs(t),
                          "sure_s": [k["sure_s"], t["sure_s"]]})
    n = len(result_rows)
    return {"soru": n, "ayni_durum": sum(s["ayni_durum"] for s in result_rows),
            "ayni_cevap": sum(s["ayni_cevap"] for s in result_rows),
            "ayni_cagrilar": sum(s["ayni_cagrilar"] for s in result_rows), "sorular": result_rows}


def report(set_name: str, model_name: str, data: dict, scope_sets: dict, chunk_roles: dict, sections) -> dict:
    questions = [s for s in data["sorular"] if s["set"] == set_name and s["bolum"] in sections]
    records = read_jsonl(jsonl_path(set_name, model_name))
    this_year = int(data["simulasyon_gunu"][:4])
    scores, summary = score_all(questions, records, scope_sets, chunk_roles, this_year)
    result = {
        "olcum_zamani": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "set": set_name, "model": model_name, "soru_seti_ozeti": data["ozet"],
        "sistem": fingerprint.system(), "model_izi": fingerprint.model(ollama_digest=model_name == "gercek",
                                                                    model_name=OLLAMA_MODEL)
        if model_name == "gercek" else {"model": model_name},
        **({"kol": ARM, "kol_ozeti": ARM_DIGEST} if ARM != "el" else {}),
        "tamamlanan": f"{len(scores)}/{len(questions)}",
        "altyapi_yeniden_sorulan": [k["soru_id"] for k in records if k.get("altyapi")],
        "hedefler": scoring.evaluate_targets(summary, data["hedefler"]), "ozet": summary,
        "sorular": [{k: p[k] for k in p if k not in ("taslak_tablosu", "alintilar")} for p in scores],
    }
    old_digests = sorted({k.get("soru_seti_ozeti") for k in records} - {data["ozet"]})
    if old_digests:  # the records were taken with another set digest: the model was not re-run, the answers were scored with the new key
        result["yeniden_puanlama"] = {
            "kayit_soru_seti_ozetleri": old_digests,
            "not": "Bu kayıtlar soru setinin önceki bir sürümüyle alındı ve şimdiki setle yeniden puanlandı. Yeniden "
                   "puanlama yalnızca soru metinleri, soran kişiler ve sistem aynıyken geçerlidir (README Faz 7)."}
    repeat = read_jsonl(jsonl_path(set_name, model_name, repeat=True))
    if repeat:
        result["tekrar"] = compare_repeat(records, repeat)
    return result


def oracle_all(data: dict, scope_sets: dict, chunk_roles: dict) -> dict:
    """Runs the oracle and the controls on both sets; the oracle must get full marks on every metric, the controls must lose."""
    result = {"olcum_zamani": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
             "soru_seti_ozeti": data["ozet"], "modeller": {}}
    for model_name in ("kahin", *CONTROLS):
        result["modeller"][model_name] = {}
        for set_name in sorted({s["set"] for s in data["sorular"]}):
            questions = [s for s in data["sorular"] if s["set"] == set_name]
            start = time.perf_counter()
            run(questions, model_name, jsonl_path(set_name, model_name), restart=True, quiet=True)
            r = report(set_name, model_name, data, scope_sets, chunk_roles, ("arac", "yetkisiz", "belge"))
            o = r["ozet"]
            customer_questions = [p for p in r["sorular"] if p["bolum"] == "arac"
                       and question_by_id(data, p["soru_id"]).get("diger_musteri")]
            result["modeller"][model_name][set_name] = {
                "sure_s": round(time.perf_counter() - start, 1),
                "arac_secimi": o["arac"]["arac_secimi"], "parametre": o["arac"]["parametre"],
                "rakam_dogrulugu": o["arac"]["cevap_dogrulugu"],
                "dogrulanmis_yanlis_sayi": o["arac"]["dogrulanmis_yanlis_sayi"],
                "musteri_sorularinda_rakam_dogrulugu": scoring._ratio(p["cevap_dogru"] for p in customer_questions),
                "belge_isabet": o["belge"]["isabet"], "belge_cevap": o["belge"]["cevap_dogrulugu"],
                "belge_alinti": o["belge"]["alinti_gecerli"], "yetkisiz_sizinti": o["yetkisiz"]["sizinti"],
                "kapsam_disi": o["genel"]["kapsam_disi_satir"], "durumlar": o["genel"]["durumlar"],
                "dogrulayici_tablosu": {k: v for k, v in o["dogrulayici_tablosu"].items() if k != "aciklama"},
                "hata_turleri": o["arac"]["hata_turleri"],
            }
            print(model_name, set_name, json.dumps({k: v for k, v in result["modeller"][model_name][set_name].items()
                                                  if k not in ("hata_turleri", "dogrulayici_tablosu")},
                                                 ensure_ascii=False), flush=True)
    return result


def document_ceiling(data: dict) -> dict:
    """If the semantic search is given the question's own text, is the right article in the top 3 (with the asker's role)? The model searches in its own
    words; this rate is the hit rate in the case where the model has the question searched verbatim (to compare with the hit rate in the real run).
    New questions are converted to vectors with bge-m3 in Ollama."""
    from sayman.documents.search import build_searcher
    searcher = build_searcher(DOCUMENT_DIR, "anlam")
    result = {}
    for set_name in sorted({s["set"] for s in data["sorular"]}):
        questions = [s for s in data["sorular"] if s["set"] == set_name and s["bolum"] == "belge"]
        if not questions:  # there is no document question in the Phase 8b new set
            continue
        missed = [s["soru_id"] for s in questions
                 if s["dogru_parca"] not in [p.parca_id for p, _ in searcher.search(s["soru"], s["rol"], 3, "anlam")]]
        result[set_name] = {"soru": len(questions), "isabet@3": round(1 - len(missed) / len(questions), 4), "kacan": missed}
    return result


_QUESTION_CACHE: dict = {}


def question_by_id(data: dict, question_id: str) -> dict:
    if not _QUESTION_CACHE:
        _QUESTION_CACHE.update({s["soru_id"]: s for s in data["sorular"]})
    return _QUESTION_CACHE[question_id]


def keep_awake() -> None:
    """On Windows, keeps the computer from going to idle sleep while the process runs (SetThreadExecutionState). It does not change system
    settings; the request goes away by itself when the process ends. If the lid is closed the computer still sleeps."""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED


def main() -> None:
    global OLLAMA_MODEL, LABEL, QUESTION_SET, ARM, ARM_DIGEST
    keep_awake()
    parser = argparse.ArgumentParser(description="Faz 7 ölçümü")
    parser.add_argument("--set", choices=("gelistirme", "saklanan", "yeni", "kimlik"))
    parser.add_argument("--soru-seti", default=str(QUESTION_SET.relative_to(ROOT)),
                    help="Faz 8b: başka bir dondurulmuş soru seti (ör. reports/faz8b_soru_seti.json, set 'yeni')")
    parser.add_argument("--model", choices=MODELS, default="gercek")
    parser.add_argument("--bolum", default="arac,yetkisiz,belge")
    parser.add_argument("--sinir", type=int, default=0, help="yalnızca ilk N soru (deneme için)")
    parser.add_argument("--sorular", default="", help="yalnızca bu soru kimlikleri (virgülle; duman testi için)")
    parser.add_argument("--yeniden", action="store_true", help="JSONL'i baştan yaz")
    parser.add_argument("--tekrar", type=int, default=0, help="belirlenimcilik: setten eşit aralıklı N soruyu yeniden sor")
    parser.add_argument("--yalniz-rapor", action="store_true")
    parser.add_argument("--kahin-hepsi", action="store_true", help="kâhin ve kontroller, iki set; reports/faz7_kahin.json")
    parser.add_argument("--ollama-model", default=None, help="Faz 8: gerçek model yerine Ollama'daki bu model")
    parser.add_argument("--etiket", default="faz7", help="Faz 8: rapor dosyalarının öneki (ör. faz8_ayarli)")
    parser.add_argument("--kol", choices=("el", "otomatik"), default="el",
                    help="Faz 9: elle tasarlanmış araçlar (el) ya da sahte API'den üretilenler (otomatik)")
    a = parser.parse_args()
    ARM = a.kol
    if ARM == "otomatik":
        from sayman.experiment.generated_tools import arm_digest
        ARM_DIGEST = arm_digest()
        if a.kahin_hepsi or a.tekrar or a.set != "yeni" or a.bolum != "arac":
            parser.error("--kol otomatik yalnızca --set yeni --bolum arac ile (Faz 9 karşılaştırması)")
    if (a.ollama_model is None) != (a.etiket == "faz7"):
        parser.error("--ollama-model ile --etiket birlikte verilmeli (Faz 7 dosyalarının üstüne yazılmasın)")
    OLLAMA_MODEL, LABEL = a.ollama_model, a.etiket
    QUESTION_SET = (ROOT / a.soru_seti).resolve()
    if a.set == "yeni" and QUESTION_SET.name == "faz7_soru_seti.json":
        parser.error("--set yeni için --soru-seti reports/faz8b_soru_seti.json verilmeli")
    sys.stdout.reconfigure(encoding="utf-8")
    data = json.loads(QUESTION_SET.read_text(encoding="utf-8"))
    scope_sets = scope_record_ids()
    chunk_roles = {pid: p.roller for pid, p in chunks().items()}
    if a.kahin_hepsi:
        result = oracle_all(data, scope_sets, chunk_roles)
        try:
            result["belge_tavani"] = document_ceiling(data)
        except Exception as e:  # if Ollama is down
            result["belge_tavani"] = {"hata": f"{type(e).__name__}: {str(e)[:200]}"}
        print("belge tavanı", json.dumps(result["belge_tavani"], ensure_ascii=False), flush=True)
        # faz7_soru_seti.json → faz7_kahin.json; faz8b_soru_seti.json → faz8b_kahin.json
        (ROOT / "reports" / QUESTION_SET.name.replace("soru_seti", "kahin")).write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n",
                                                         encoding="utf-8")
        return
    if not a.set:
        parser.error("--set gerekli")
    sections = tuple(a.bolum.split(","))
    questions = [s for s in data["sorular"] if s["set"] == a.set and s["bolum"] in sections]
    if a.sorular:
        selected = set(a.sorular.split(","))
        questions = [s for s in questions if s["soru_id"] in selected]
    if a.sinir:
        questions = questions[:a.sinir]
    if a.tekrar:
        original = [k["soru_id"] for k in read_jsonl(jsonl_path(a.set, a.model))]
        step = max(len(original) // a.tekrar, 1)
        selected = set(original[::step][:a.tekrar])
        run([s for s in questions if s["soru_id"] in selected], a.model, jsonl_path(a.set, a.model, repeat=True),
                 restart=a.yeniden)
    elif not a.yalniz_rapor:
        run(questions, a.model, jsonl_path(a.set, a.model), restart=a.yeniden)
    r = report(a.set, a.model, data, scope_sets, chunk_roles, sections)
    target = (ROOT / "reports" / f"{LABEL}_olcum_{a.set}.json" if a.model == "gercek"
             else WORK_DIR / f"rapor_{a.set}_{a.model}{'_oto' if ARM == 'otomatik' else ''}.json")
    target.write_text(json.dumps(r, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"tamamlanan": r["tamamlanan"], "hedefler": [(h["ad"], h["deger"], h["gecti"]) for h in r["hedefler"]]},
                     ensure_ascii=False, indent=1))
    print(json.dumps({k: r["ozet"][k] for k in ("arac", "belge", "yetkisiz", "sure")}, ensure_ascii=False, indent=1)[:4000])


if __name__ == "__main__":
    main()
