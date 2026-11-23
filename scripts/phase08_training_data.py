"""Phase 8 training data: questions → leakage check → teacher traces → prompts rendered by Ollama.

Steps (in order; each reads the output of the previous one):
    sorular  produces the training questions (tests/support/phase08_training_questions.py) and checks leakage; it writes
             nothing if there is a violation. Output: data/egitim/sorular.json, reports/faz8_sizinti.json
    gomme    caches the vectors of the document chunks and of the document questions (bge-m3 in Ollama; do not run it
             while another measurement that uses Ollama is running).
    izler    runs the teacher with the real orchestrator, gate, verifier and the real document searcher (training library);
             every turn is scored with the Phase 7 scorer. Only the turns that passed without a correction turn and are correct
             according to the scorer are kept. Output: data/egitim/izler.jsonl
    ciz      has Ollama render every model call (prompt) and turns the teacher's answer into the completion text.
             Output: data/egitim/veri/egitim.jsonl, data/egitim/veri/dogrulama.jsonl, reports/faz8_egitim_verisi.json

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase08_training_data.py sorular
    .venv\\Scripts\\python -I scripts\\phase08_training_data.py gomme
    .venv\\Scripts\\python -I scripts\\phase08_training_data.py izler
    .venv\\Scripts\\python -I scripts\\phase08_training_data.py ciz
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import secrets
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

COMPANY = ROOT / "data" / "egitim" / "sirket"
WAREHOUSE = COMPANY / "ambar" / "sayman.duckdb"
DOCUMENTS = COMPANY / "belge"
QUESTIONS_FILE = ROOT / "data" / "egitim" / "sorular.json"
TRACES = ROOT / "data" / "egitim" / "izler.jsonl"
DATA_DIR = ROOT / "data" / "egitim" / "veri"
WORK_DIR = ROOT / "data" / "egitim" / "calisma"


def _json(x):
    if isinstance(x, (dt.date,)):
        return x.isoformat()
    raise TypeError(type(x))


def digest(data) -> str:
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, default=_json).encode()).hexdigest()


# ------------------------------------------------------------------ evaluation side


def load_evaluation() -> "Degerlendirme":
    import support.phase05_questions as f5
    import support.phase07_questions as f7

    from sayman.documents import library
    from sayman.training.leakage import EvaluationData, normalize

    data = json.loads((ROOT / "reports" / "faz7_soru_seti.json").read_text(encoding="utf-8"))
    patterns = [k for sb in f7.TEMPLATES for k in sb.patterns]
    patterns += [k for ks in f7.FORBIDDEN_PATTERNS.values() for k in ks] + list(f7.OTHER_CUSTOMER_PATTERNS)
    patterns += [m for m, _, _ in f7.ROLE_CLAIM_PATTERNS + f7.INJECTION_PATTERNS]
    patterns += [k for group in f5.PATTERNS.values() for pair in group.values() for k in pair]
    rate_keys = set()
    for s in data["sorular"]:
        for a in s.get("alternatifler", []):
            if a["arac"] == "doviz_kuru":
                rate_keys.add((a["argumanlar"]["para_birimi"], str(a["anahtarlar"][0]["deger"])))
    names = set()
    for file in ("musteriler.csv", "tedarikciler.csv"):
        with open(ROOT / "data" / "uretilen" / file, encoding="utf-8") as f:
            names |= {normalize(r["ad"]) for r in csv.DictReader(f)}
    chunk_texts = {p.metin for p in library.to_chunks(library.read_documents(ROOT / "data" / "belge" / "belgeler.jsonl"))}
    return EvaluationData(patterns=patterns, questions=[s["soru"] for s in data["sorular"]],
                         identifiers=set(data["varliklar"]["gelistirme"]) | set(data["varliklar"]["saklanan"]),
                         names=names, article_texts=chunk_texts, rates=rate_keys)


def excluded_names() -> set[str]:
    from sayman.evaluation.value_formats import normalize_name
    names = set()
    for file in ("musteriler.csv", "tedarikciler.csv"):
        with open(ROOT / "data" / "uretilen" / file, encoding="utf-8") as f:
            names |= {normalize_name(r["ad"]) for r in csv.DictReader(f)}
    return names


# ------------------------------------------------------------------ questions


def questions_step() -> None:
    import support.phase08_training_questions as e8

    from sayman.documents import library
    from sayman.training.leakage import audit

    d = load_evaluation()
    names = excluded_names()
    documents = library.read_documents(DOCUMENTS / "belgeler.jsonl")
    start_time = time.time()
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=WORK_DIR) as temp_dir:
        samplers = {g: e8.TrainingSampler(WAREHOUSE, g, Path(temp_dir) / g.isoformat(), names, d.identifiers)
               for g in e8.TRAINING_DAYS}
        try:
            tool_questions = e8.tool_questions(samplers, d.rates)
            unauthorized = e8.unauthorized_questions(samplers)
        finally:
            for o in samplers.values():
                o.close()
    document_questions = e8.document_questions(documents, d.article_texts, names)
    document_permission_questions = e8.document_permission_questions(document_questions, documents)
    questions = e8.split_validation(tool_questions + unauthorized + document_questions + document_permission_questions)
    report = audit(questions, d)
    shared = {
        "tasarim_geregi_ortak": ["UCI satış kalemleri (ürünler, tarihler)", "TCMB kur tablosu",
                                 "banka hesap kimlikleri (A_TRY, B_EUR, B_TRY, C_USD)", "kullanıcılar ve rolleri",
                                 "kanun metinleri (yalnızca arama sonucunda; eğitim sorusu hedefi olmaz)",
                                 "sistem istemi ve araç tanımları", "politika belge kimlikleri ve başlıkları",
                                 "Faz 7 örnekleyici fonksiyonları (niyet tanımları)"],
        "haric_niyetler": list(e8.EXCLUDED_INTENTS), "egitim_gunleri": [g.isoformat() for g in e8.TRAINING_DAYS],
    }
    result = {"soru": len(questions), "bolum": dict(Counter(s["bolum"] for s in questions)),
             "ayrim": dict(Counter(s["ayrim"] for s in questions)), "niyet": dict(Counter(s["niyet"] for s in questions)),
             "en_yakin_kalip_benzerligi": round(report.closest_pattern, 3),
             "en_yakin_soru_benzerligi": round(report.closest_question, 3),
             "ihlal_sayisi": {k: len(v) for k, v in report.violations.items()}, "ihlaller": report.violations,
             **shared, "sure_s": round(time.time() - start_time, 1)}
    (ROOT / "reports" / "faz8_sizinti.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n",
                                                       encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in ("ihlaller", "niyet")}, ensure_ascii=False, indent=1))
    if not report.clean:
        for tur, items in report.violations.items():
            for x in items[:8]:
                print("İHLAL", tur, json.dumps(x, ensure_ascii=False))
        raise SystemExit("sızıntı denetimi geçmedi; sorular yazılmadı")
    QUESTIONS_FILE.write_text(json.dumps({"ozet": digest(questions), "sorular": questions}, ensure_ascii=False, indent=1,
                                  default=_json) + "\n", encoding="utf-8")
    print("yazıldı:", QUESTIONS_FILE.relative_to(ROOT), digest(questions)[:16])


def read_questions() -> list[dict]:
    data = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
    if digest(data["sorular"]) != data["ozet"]:
        raise SystemExit("sorular.json özeti tutmuyor")
    return data["sorular"]


# ------------------------------------------------------------------ embedding


def embedding_step() -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    import phase08_training_company as sirket

    from sayman.documents.semantic import EmbeddingCache

    sirket.embedding_step()
    queries = [s["cagri"][1]["sorgu"] for s in read_questions() if s.get("cagri") and s["cagri"][0] == "belge_ara"]
    queries += [s["ikinci_sorgu"] for s in read_questions() if s.get("ikinci_sorgu")]
    o = EmbeddingCache(sirket.CACHE)
    print("sorgu vektörü:", o.fill(queries), "yeni")


# ------------------------------------------------------------------ traces


def scope_sets() -> dict:
    from sayman.warehouse.loader import open_warehouse, query_rows
    con = open_warehouse(WAREHOUSE)
    try:
        scopes = {}
        for role, condition in (("satis_yurtici", "m.yurtici"), ("satis_ihracat", "NOT m.yurtici")):
            m = f"SELECT musteri_id FROM musteriler m WHERE {condition}"
            scope_set = {"doviz_kurlari": None, "musteriler": {r["musteri_id"] for r in query_rows(con, m)}}
            for table, id_column in (("faturalar", "fatura_id"), ("tahsilatlar", "tahsilat_id"), ("cekler", "cek_id")):
                scope_set[table] = {r[id_column] for r in query_rows(con, f"SELECT {id_column} FROM {table} WHERE musteri_id IN ({m})")}
            scopes[role] = scope_set
        return scopes
    finally:
        con.close()


def scorer_row(tur) -> dict:
    """The format the Phase 7 scorer reads (the same fields as the record written by scripts/phase07_evaluate.py)."""
    return {"durum": tur.status, "cevap": tur.answer, "model_cagrisi": tur.model_calls, "neden": tur.reason,
            "cagrilar": [{"arac": c["arac"], "argumanlar": c["argumanlar"], "hata": c["yanit"].get("hata"),
                          "yanit": c["yanit"]} for c in tur.calls],
            "taslaklar": [{"metin": t, "gecerli": d.gecerli} for t, d in zip(tur.drafts, tur.verifications)]}


def is_kept(s: dict, row: dict, p: dict) -> tuple[bool, str]:
    if any(not t["gecerli"] for t in row["taslaklar"]):
        return False, "duzeltme"
    if p.get("kapsam_disi"):
        return False, "kapsam_disi"
    if s["bolum"] == "arac":
        ok = row["durum"] == "tamam" and p["cevap_dogru"] and p["arac_dogru"] and p["parametre_dogru"]
        return ok, "tamam" if ok else f"arac:{row['durum']}:{p.get('hata_turu')}"
    if s["bolum"] == "belge":
        ok = row["durum"] == "tamam" and p["cevap_dogru"] and p["dogru_parcadan_alinti"]
        return ok, "tamam" if ok else f"belge:{row['durum']}:{p.get('hata_turu')}"
    if s.get("tur") == "salt_okunur":
        ok = row["durum"] == "tamam" and not row["cagrilar"]
        return ok, "tamam" if ok else f"salt:{row['durum']}"
    expected = "ret" if s["tur"] in ("yasak_arac", "rol_iddiasi", "yonlendirme") else "tamam"
    ok = row["durum"] == expected and not p.get("sizinti")
    return ok, "tamam" if ok else f"yetkisiz:{row['durum']}:{row.get('neden')}"


def traces_step(limit: int = 0) -> None:
    from sayman.tools.audit_log import AuditLog
    from sayman.tools.identity import generate_card
    from sayman.tools.server import SaymanServer
    from sayman.tools.permissions import read_permissions
    from sayman.assistant.orchestrator import Assistant, Settings
    from sayman.documents import library
    from sayman.documents.search import build_searcher
    from sayman.training.prompt import RecordingModel
    from sayman.training.teacher import Teacher
    from sayman.evaluation import scoring
    from sayman.data.clock import Clock, read_clock

    questions = read_questions()
    if limit:
        questions = questions[:limit]
    data_end = read_clock(COMPANY / "saat.json").veri_bitis
    clock = [Clock(dt.date.fromisoformat(questions[0]["gun"]), data_end)]
    signing_key = secrets.token_bytes(32)
    scopes = scope_sets()
    chunk_roles = {p.parca_id: p.roller for p in library.to_chunks(library.read_documents(DOCUMENTS / "belgeler.jsonl"))}
    searcher = build_searcher(DOCUMENTS, "anlam")
    model = RecordingModel(Teacher())
    counter, start_time = Counter(), time.time()
    WORK_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=WORK_DIR) as temp_dir, open(TRACES, "w", encoding="utf-8", newline="\n") as f:
        server = SaymanServer(warehouse=WAREHOUSE, clock=lambda: clock[0], key=signing_key, permissions=read_permissions(),
                                audit_log=AuditLog(Path(temp_dir) / "denetim.jsonl"),
                                scope_dir=Path(temp_dir) / "kapsam", belge=searcher, search_method="anlam")
        try:
            assistant = Assistant(server, model, Settings())
            for i, s in enumerate(questions, 1):
                clock[0] = Clock(dt.date.fromisoformat(s["gun"]), data_end)
                model.soru = s
                tur = assistant.ask(s["soru"], generate_card(s["kullanici"], signing_key, 900))
                calls = model.drain()
                row = scorer_row(tur)
                p = scoring.score(s, row, scopes, chunk_roles, int(s["gun"][:4]))
                ok, reason = is_kept(s, row, p)
                counter[(s["bolum"], reason)] += 1
                f.write(json.dumps({"soru_id": s["soru_id"], "tutuldu": ok, "neden": reason, "satir": row,
                                    "cagrilar": [{"mesajlar": c["mesajlar"], "araclar_ozeti": digest(c["araclar"]),
                                                  "icerik": c["cevap"].content,
                                                  "arac_cagrilari": c["cevap"].tool_calls} for c in calls]},
                                   ensure_ascii=False, default=_json) + "\n")
                if i % 50 == 0:
                    print(f"{i}/{len(questions)} {time.time() - start_time:.0f} sn", flush=True)
        finally:
            server.close()
    for (section, reason), n in sorted(counter.items()):
        print(f"  {section:9s} {reason:40s} {n}")


# ------------------------------------------------------------------ rendering


def render_step() -> None:
    from sayman.assistant.orchestrator import ModelResponse, tool_definitions
    from sayman.training.prompt import render_prompt, completion

    questions = {s["soru_id"]: s for s in read_questions()}
    tools = tool_definitions()
    tools_digest = digest(tools)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    samples, seen, start_time = {"egitim": [], "dogrulama": []}, set(), time.time()
    with open(TRACES, encoding="utf-8") as f:
        traces = [json.loads(x) for x in f if x.strip()]
    for i, trace in enumerate(traces, 1):
        if i % 100 == 0:
            print(f"{i}/{len(traces)} iz, {len(seen)} örnek, {time.time() - start_time:.0f} sn", flush=True)
        if not trace["tutuldu"]:
            continue
        s = questions[trace["soru_id"]]
        for step, c in enumerate(trace["cagrilar"], 1):
            if c["araclar_ozeti"] != tools_digest:
                raise SystemExit("izlerdeki araç listesi bugünkünden farklı; izler adımını yeniden çalıştırın")
            prompt = render_prompt(c["mesajlar"], tools)
            completion_text = completion(ModelResponse(content=c["icerik"], tool_calls=[tuple(x) for x in c["arac_cagrilari"]]))
            if (prompt, completion_text) in seen:
                continue
            seen.add((prompt, completion_text))
            samples[s["ayrim"]].append({"soru_id": s["soru_id"], "adim": step, "bolum": s["bolum"],
                                         "niyet": s["niyet"], "istem": prompt, "tamamlama": completion_text})
    for name, items in samples.items():
        with open(DATA_DIR / f"{name}.jsonl", "w", encoding="utf-8", newline="\n") as f:
            for o in items:
                f.write(json.dumps(o, ensure_ascii=False) + "\n")
    report = {name: {"ornek": len(items), "soru": len({o["soru_id"] for o in items}),
                  "bolum": dict(Counter(o["bolum"] for o in items)),
                  "istem_karakter_en_cok": max((len(o["istem"]) for o in items), default=0),
                  "ozet": digest([[o["istem"], o["tamamlama"]] for o in items])} for name, items in samples.items()}
    report["izler"] = {"soru": len(traces), "tutulan": sum(i["tutuldu"] for i in traces),
                      "nedenler": dict(Counter(i["neden"] for i in traces))}
    (ROOT / "reports" / "faz8_egitim_verisi.json").write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n",
                                                             encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Faz 8 eğitim verisi")
    ap.add_argument("adim", choices=("sorular", "gomme", "izler", "ciz"))
    ap.add_argument("--sinir", type=int, default=0, help="izler: yalnızca ilk N soru (deneme)")
    a = ap.parse_args()
    {"sorular": questions_step, "gomme": embedding_step, "ciz": render_step}.get(a.adim, lambda: traces_step(a.sinir))()


if __name__ == "__main__":
    main()
