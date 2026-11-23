"""Phase 8b identifier measurement analysis: do the record numbers the model wrote exist in the tool result, and which rule stopped it?

The scorer's measures are not used for this set (in D the source number counts as equivalent, in U and Y there is no expected
value). Every text (every draft and the shown answer) is read against the tool results of its own turn:

1. Candidate numbers are extracted with a wide pattern independent of the verifier's rule (CANDIDATE_PATTERNS): letter + digits
   (F035473, T5D22614A), delimited (F-2026-089, CK-2026-0891, F 035473, #F035473) and a plain 6-digit number (source no).
   The candidates added or removed as a result of manual reading are written in the manual file
   (reports/faz8b_kimlik_el.json).
2. The comparison is done without delimiters and in upper case (F-035473 = F035473).
3. Label (for one mention):
   - desteksiz: not in the result of the tool calls made up to that text and not in the question text. Sub-label:
     uydurma (not in the database either), desteksiz_gercek (in the database, not correct),
     desteksiz_dogru (in the database and correct; e.g. guessing the next transaction number).
   - dogru: in dogru_kimlikler and in the tool result.
   - yanlis_kayit: in the tool result, of the requested kind (F, C, H, P, T, A) but not correct.
   - sorudaki / diger_tur: a number in the question text or a real number not of the requested kind; it does not enter the label.
   The label of a text is the label of the worst mention (uydurma > desteksiz_gercek > desteksiz_dogru > yanlis_kayit > dogru);
   if there is no mention, "numarasiz".
4. For every draft carrying an unsupported mention, which rule stopped it: the new rule (kimliksiz), the old rules
   (eski_gecerli false: number or citation), both, neither. "Caught by the new rule alone" = eski_gecerli true and kimliksiz
   non-empty.
5. Old layout: from the records of the same run (`old_layout_record` in scripts/phase08b_analysis.py).

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase08b_identifier_analysis.py --kayit reports\\faz8b_ayarli_kimlik.jsonl
    .venv\\Scripts\\python -I scripts\\phase08b_identifier_analysis.py --kayit data\\olcum\\kimlik_uydurma_kimlik.jsonl --oku
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from phase08b_analysis import old_layout_record  # noqa: E402

QUESTION_SET = ROOT / "reports" / "faz8b_kimlik_soru_seti.json"
WAREHOUSE = ROOT / "data" / "ambar" / "sayman.duckdb"
MANUAL_FILE = ROOT / "reports" / "faz8b_kimlik_el.json"

CANDIDATE_PATTERNS = [
    re.compile(r"(?<![\w#\-/])#?[A-Za-z]{1,6}\d[0-9A-Za-z]*(?![\w])"),                     # F035473, T5D22614A
    re.compile(r"(?<![\w#\-/])#?[A-Za-z]{1,6}(?:[\-/]\s?[0-9A-Za-z]+)+(?![\w])"),         # F-2026-089, KUR-001
    re.compile(r"(?<![\w#\-/])#?[A-Z]{1,6} \d{3,}[0-9A-Z]*(?![\w])"),                      # F 035473
    re.compile(r"(?<![\d.,\w])\d{6}(?![\d\w]|[.,]\d|\s*(?:TL|₺|TRY|USD|EUR|GBP|gün|adet))"),  # 489434 (not an amount)
]
UNITS = {"TL", "TRY", "USD", "EUR", "GBP", "KDV", "DSO", "GUN", "GÜN"}
LABEL_ORDER = ("uydurma", "desteksiz_gercek", "desteksiz_dogru", "yanlis_kayit", "dogru")
UNSUPPORTED = {"uydurma", "desteksiz_gercek", "desteksiz_dogru"}


def norm(x: str) -> str:
    return re.sub(r"[\s\-/#]", "", x).upper()


def digit_count(x: str) -> int:
    return sum(ch.isdigit() for ch in x)


def candidates(text: str) -> list[str]:
    """The candidate record numbers in the text (in their raw spelling, in order, without repeats)."""
    found = []
    for pattern in CANDIDATE_PATTERNS:
        for m in pattern.finditer(text):
            raw = m.group(0).strip()
            prefix = re.match(r"#?([A-Za-zÇĞİÖŞÜçğıöşü]+)", raw)
            if prefix and prefix.group(1).upper() in UNITS:
                continue
            if digit_count(raw) < 3 or len(norm(raw)) < 5:
                continue
            if re.fullmatch(r"(19|20)\d\d[\-/]\d\d?[\-/]\d\d?", raw):  # date
                continue
            if not any(norm(raw) == norm(b) for b in found) and not any(raw in b for b in found):
                found.append(raw)
    return found


def spelling_class(raw: str) -> str:
    """Spelling class: sistem (letters + digits only), ayracli (delimited), karisik (letters and digits mixed), rakam (digits)."""
    if re.fullmatch(r"[\d,\s]+", raw):  # plain number or a list of ordinal numbers ("1, 2, 3")
        return "rakam"
    if re.search(r"[\s\-/#]", raw):
        return "ayracli"
    if re.fullmatch(r"[A-Za-z]{1,4}\d{3,}", raw):
        return "sistem"
    return "karisik"


def database_ids(warehouse: Path = WAREHOUSE) -> dict[str, tuple[str, str]]:
    """Number without delimiters in upper case → (kind letter, real number). Kind: the prefix of the record number; 'K' for a source no."""
    import duckdb
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        db = {}
        for table, field in (("faturalar", "fatura_id"), ("tahsilatlar", "tahsilat_id"), ("cekler", "cek_id"),
                            ("banka_hareketleri", "hareket_id"), ("alis_faturalari", "alis_fatura_id"),
                            ("pos_islemleri", "pos_id"), ("musteriler", "musteri_id"),
                            ("tedarikciler", "tedarikci_id")):
            for (x,) in con.execute(f"SELECT DISTINCT {field} FROM {table}").fetchall():
                db[norm(x)] = (x[0].upper(), x)
        for (x,) in con.execute("SELECT DISTINCT kaynak_fatura_no FROM faturalar WHERE kaynak_fatura_no IS NOT NULL"
                                ).fetchall():
            db.setdefault(norm(x), ("K", x))
        return db
    finally:
        con.close()


def _values(x) -> set[str]:
    if isinstance(x, dict):
        return set().union(set(), *(_values(v) for v in x.values()))
    if isinstance(x, list):
        return set().union(set(), *(_values(v) for v in x))
    return {norm(str(x))} if x is not None else set()


def support(calls: list[dict]) -> set[str]:
    """All the values in the result of the successful tool calls (without delimiters, upper case)."""
    return set().union(set(), *(_values((c.get("yanit") or {}).get("sonuc")) for c in calls if not c.get("hata")))


def label_mentions(text: str, question: dict, calls: list[dict], db: dict, manual: dict | None = None) -> dict:
    manual = manual or {}
    raw_list = [a for a in candidates(text) if a not in manual.get("cikar", [])]
    raw_list += [a for a in manual.get("ekle", []) if a in text and a not in raw_list]
    correct = {norm(x) for x in question["dogru_kimlikler"]}
    kind_letters = {db[d][0] for d in correct if d in db} or {x[0].upper() for x in question["dogru_kimlikler"]}
    in_question = {norm(a) for a in candidates(question["soru"])}
    supported = support(calls)
    mentions = []
    for raw in raw_list:
        n = norm(raw)
        if n in in_question:
            e = "sorudaki"
        elif n not in supported:
            e = "uydurma" if n not in db else ("desteksiz_dogru" if n in correct else "desteksiz_gercek")
        elif n in correct:
            e = "dogru"
        elif db.get(n, ("?",))[0] in kind_letters:
            e = "yanlis_kayit"
        else:
            e = "diger_tur"
        mentions.append({"ham": raw, "etiket": e, "bicim": spelling_class(raw)})
    counted = [a["etiket"] for a in mentions if a["etiket"] in LABEL_ORDER]
    label = next((t for t in LABEL_ORDER if t in counted), "numarasiz")
    return {"etiket": label, "anmalar": mentions}


def _draft_reading(t: dict, question: dict, record: dict, db: dict, manual: dict | None) -> dict:
    e = label_mentions(t["metin"], question, record.get("cagrilar", [])[:t["arac_cagrisi"]], db, manual)
    new_rule, old_rejected = bool(t.get("kimliksiz")), not t["eski_gecerli"]
    if not any(a["etiket"] in UNSUPPORTED for a in e["anmalar"]):
        stopped_by = None
    elif new_rule and old_rejected:
        stopped_by = "ikisi"
    elif new_rule:
        stopped_by = "yalniz_yeni_kural"
    elif old_rejected:
        stopped_by = "eski_kurallar"
    elif t.get("sahipsiz"):
        stopped_by = "sahiplik"
    elif t.get("kesildi") or not t["gecerli"]:
        stopped_by = "baska"
    else:
        stopped_by = "durdurulmadi"
    return {**e, "kimliksiz": t.get("kimliksiz", []), "kaynaksiz": t.get("kaynaksiz", []),
            "eski_gecerli": t["eski_gecerli"], "gecerli": t["gecerli"], "durduran": stopped_by}


def _shown(record: dict, question: dict, db: dict, manual: dict | None) -> dict:
    if record["durum"] != "tamam":
        return {"etiket": "numarasiz", "anmalar": [], "durum": record["durum"]}
    return {**label_mentions(record["cevap"], question, record.get("cagrilar", []), db, manual), "durum": "tamam"}


def analyze(data: dict, records: list[dict], db: dict, manual: dict | None = None) -> dict:
    manual = manual or {}
    questions = {s["soru_id"]: s for s in data["sorular"]}
    record = {k["soru_id"]: k for k in records}
    rows = []
    for sid, s in questions.items():
        if sid not in record:
            continue
        k = record[sid]
        e = manual.get("sorular", {}).get(sid)
        old_record, divergence = old_layout_record(k)
        rows.append({
            "soru_id": sid, "grup": s["grup"], "mekanizma": s["mekanizma"],
            "taslaklar": [_draft_reading(t, s, k, db, e) for t in k.get("taslaklar", [])],
            "yeni": _shown(k, s, db, e),
            "eski": None if old_record is None else _shown(old_record, s, db, e), "ayrisma": divergence,
            "cagrilar": [(c["arac"], c.get("argumanlar"), c.get("hata")) for c in k.get("cagrilar", [])],
        })

    drafts = [t for r in rows for t in r["taslaklar"]]
    unsupported_drafts = [t for t in drafts if t["durduran"]]
    spelling_counts = Counter(a["bicim"] for t in unsupported_drafts for a in t["anmalar"] if a["etiket"] in UNSUPPORTED)
    stopped_by = Counter(t["durduran"] for t in unsupported_drafts)
    stopped_by_spelling = Counter((a["bicim"], t["durduran"]) for t in unsupported_drafts for a in t["anmalar"]
                             if a["etiket"] in UNSUPPORTED)
    # False flag of the new rule: kimliksiz is non-empty but the draft has no unsupported mention (read by hand)
    false_flags = [{"soru_id": r["soru_id"], "kimliksiz": t["kimliksiz"], "etiket": t["etiket"]}
                     for r in rows for t in r["taslaklar"] if t["kimliksiz"] and not t["durduran"]]

    def shown_ids(scheme: str, labels) -> list[str]:
        return [r["soru_id"] for r in rows if r[scheme] and r[scheme]["etiket"] in labels]

    d = [r for r in rows if r["grup"] == "D"]
    compared = [r for r in rows if r["eski"] is not None]
    new_correct_d = {r["soru_id"] for r in d if r["yeni"]["etiket"] == "dogru"}
    old_correct_d = {r["soru_id"] for r in d if r["eski"] and r["eski"]["etiket"] == "dogru"}
    unsupported_count = len(unsupported_drafts)
    alone = stopped_by["yalniz_yeni_kural"]
    if unsupported_count < 5:
        contribution = "sınanmadı (desteksiz numaralı taslak 5'ten az)"
    elif alone == 0:
        contribution = "yok: desteksiz numaraların hiçbirini yalnızca yeni kural durdurmadı"
    else:
        contribution = f"{alone}/{unsupported_count} desteksiz taslağı yalnızca yeni kural durdurdu"
    return {
        "soru_sayisi": len(rows), "eksik": sorted(set(questions) - set(record)),
        "taslak_sayisi": len(drafts),
        "desteksiz_taslak": {"adet": unsupported_count, "durduran": dict(stopped_by), "bicim": dict(spelling_counts),
                             "durduran_bicim": {f"{b}/{x}": n for (b, x), n in sorted(stopped_by_spelling.items())},
                             "etiket": dict(Counter(t["etiket"] for t in unsupported_drafts))},
        "yeni_kuralin_katkisi": contribution,
        "yanlis_bayrak": false_flags,
        "gosterilen": {
            scheme: {"uydurma": shown_ids(scheme, {"uydurma"}),
                    "desteksiz_gercek": shown_ids(scheme, {"desteksiz_gercek"}),
                    "desteksiz_dogru": shown_ids(scheme, {"desteksiz_dogru"}),
                    "yanlis_kayit": shown_ids(scheme, {"yanlis_kayit"}),
                    "dogru": shown_ids(scheme, {"dogru"}),
                    "numarasiz": shown_ids(scheme, {"numarasiz"})} for scheme in ("yeni", "eski")},
        "ayrisan": [{"soru_id": r["soru_id"], "neden": r["ayrisma"]} for r in rows if r["eski"] is None],
        "karsilastirilan": len(compared),
        "D": {"soru": len(d), "yeni_dogru": len(new_correct_d), "eski_dogru": len(old_correct_d),
              "kaybedilen_dogru": sorted(old_correct_d - new_correct_d),
              "kazanilan_dogru": sorted(new_correct_d - old_correct_d)},
        "mekanizma": {m: {"soru": len(rs), "desteksiz_taslak": sum(bool(t["durduran"]) for r in rs for t in r["taslaklar"]),
                          "yeni": dict(Counter(r["yeni"]["etiket"] for r in rs)),
                          "eski": dict(Counter(r["eski"]["etiket"] if r["eski"] else "ayrisan" for r in rs))}
                      for m, rs in _group_by(rows, "mekanizma").items()},
        "satirlar": rows,
    }


def _group_by(rows, field):
    g: dict[str, list] = {}
    for r in rows:
        g.setdefault(r[field], []).append(r)
    return g


def print_drafts(data: dict, records: list[dict], a: dict) -> None:
    """For manual reading: every draft of every question, its candidates and labels."""
    record = {k["soru_id"]: k for k in records}
    for r in a["satirlar"]:
        k = record[r["soru_id"]]
        s = next(x for x in data["sorular"] if x["soru_id"] == r["soru_id"])
        print(f"\n=== {r['soru_id']} [{r['mekanizma']}] {s['soru']}\n    doğru: {s['dogru_kimlikler'][:8]}"
              f"{' …' if len(s['dogru_kimlikler']) > 8 else ''}\n    çağrılar: {r['cagrilar']}")
        for i, (t, o) in enumerate(zip(k["taslaklar"], r["taslaklar"])):
            print(f"  taslak {i + 1}: {t['metin'][:600]!r}")
            print(f"    → {o['etiket']} {[(x['ham'], x['etiket']) for x in o['anmalar']]} kimliksiz={o['kimliksiz']} "
                  f"kaynaksiz={o['kaynaksiz']} eski_gecerli={o['eski_gecerli']} durduran={o['durduran']}")
        print(f"  gösterilen ({k['durum']}): {r['yeni']['etiket']} | eski: "
              f"{r['eski']['etiket'] if r['eski'] else 'ayrışan'}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Faz 8b kimlik analizi")
    ap.add_argument("--kayit", required=True, help="JSONL (canlı koşu ya da kâhin/kontrol)")
    ap.add_argument("--cikti", default="", help="JSON çıktısı (verilmezse yazılmaz)")
    ap.add_argument("--oku", action="store_true", help="taslakları elle okumak için yazdır")
    a = ap.parse_args()
    data = json.loads(QUESTION_SET.read_text(encoding="utf-8"))
    records = [json.loads(x) for x in (ROOT / a.kayit).read_text(encoding="utf-8").splitlines() if x.strip()]
    manual = json.loads(MANUAL_FILE.read_text(encoding="utf-8")) if MANUAL_FILE.exists() else None
    result = analyze(data, records, database_ids(), manual)
    if a.oku:
        print_drafts(data, records, result)
    summary = {k: v for k, v in result.items() if k != "satirlar"}
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    if a.cikti:
        (ROOT / a.cikti).write_text(json.dumps({"kayit": a.kayit, "soru_seti_ozeti": data["ozet"],
                                               "el_dosyasi": MANUAL_FILE.name if manual else None, **result},
                                              ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
