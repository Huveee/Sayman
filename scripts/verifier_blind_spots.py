"""Verifier blind spot correction: the recorded drafts are passed through the old and the new verifier (the model is not run).

Düzeltme (README "Doğrulayıcının iki kör noktası"): kayıt numarası alanları (kaynak_fatura_no, cek_no) "numara"
türünde kaynaktır; belge maddelerindeki ve sorudaki yazıyla sayılar ("on iki ay") kaynaktır. Eski doğrulayıcı
düzeltmeden önceki commit'ten okunur, yenisi çalışma ağacından; ikisi aynı taslaklara aynı girdilerle uygulanır.

The paragraph above is written verbatim into the report as `aciklama`, so it stays in Turkish. In English: the correction
(README "Doğrulayıcının iki kör noktası"): the record number fields (kaynak_fatura_no, cek_no) are a source of kind "numara";
the numbers written in words in the document articles and in the question ("on iki ay") are a source. The old verifier is
read from the commit before the correction (OLD_COMMIT), the new one from the working tree; both are applied to the same
drafts with the same inputs.

What is measured:
- The drafts whose decision changes: reject → accept and accept → reject; each is classified by the scoring the Phase 7
  scorer uses (the scoring module of sayman.evaluation) as "carries the expected value" (dogru) or "does not" (eksik).
- The answer to show: the orchestrator shows the first draft that passes. If with the new verifier the first passing draft
  comes before the old one's and none of the drafts before it became accept → reject, the record can be replayed as it is;
  the result is counted.
- In the oracle runs (every answer correct) no draft may become accept → reject; in the fabrication controls (one digit, wrong
  unit, wrong tool, another customer, fabricated identifier) no draft may become reject → accept.

Output: reports/kor_nokta_olcum.json (the reports of the earlier phases are not touched).

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\verifier_blind_spots.py
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import types
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import phase07_evaluate  # noqa: E402
from sayman.assistant import verifier as yeni  # noqa: E402
from sayman.evaluation import scoring  # noqa: E402

OLD_COMMIT = "6f1a970"   # the last commit before the correction
REPORTS = ROOT / "reports"
MEASUREMENTS = ROOT / "data" / "olcum"
OUTPUT = REPORTS / "kor_nokta_olcum.json"
PHASE7_SET, PHASE8B_SET, IDENTIFIER_SET = (REPORTS / "faz7_soru_seti.json", REPORTS / "faz8b_soru_seti.json", REPORTS / "faz8b_kimlik_soru_seti.json")

MODEL_RUNS = [
    *[(f"{e}_{s}", REPORTS / f"{e}_{s}.jsonl", PHASE7_SET) for e in ("faz7", "faz8_temel_kendi", "faz8_ayarli")
      for s in ("gelistirme", "saklanan")],
    ("faz7_saklanan_tekrar", REPORTS / "faz7_saklanan_tekrar.jsonl", PHASE7_SET),
    ("faz8_temel_tekrar_gelistirme", REPORTS / "faz8_temel_tekrar_gelistirme.jsonl", PHASE7_SET),
    ("faz8b_ayarli_yeni", REPORTS / "faz8b_ayarli_yeni.jsonl", PHASE8B_SET),
    ("faz8b_ayarli_kimlik", REPORTS / "faz8b_ayarli_kimlik.jsonl", IDENTIFIER_SET),
    ("faz9_el_yeni", REPORTS / "faz9_el_yeni.jsonl", PHASE8B_SET),
    ("faz9_oto_yeni", REPORTS / "faz9_oto_yeni.jsonl", PHASE8B_SET),
    ("faz9_duzeltme_yeni", REPORTS / "faz9_duzeltme_yeni.jsonl", PHASE8B_SET),
]
TOTAL_KEYS = ("taslak", "ret_kabul_dogru", "ret_kabul_eksik", "kabul_ret_dogru", "kabul_ret_eksik",
                      "kazanilan_dogru_cevap", "kaybedilen_dogru_cevap", "gosterilen_yeni_eksik")
FABRICATIONS = ("bir_hane", "yanlis_birim", "yanlis_arac", "baska_musteri", "uydurma_kimlik")
CONTROL_RUNS = [
    (f"{s}_{k}", MEASUREMENTS / f"{s}_{k}.jsonl", set_path)
    for s, set_path in (("gelistirme", PHASE7_SET), ("saklanan", PHASE7_SET), ("yeni", PHASE8B_SET), ("kimlik", IDENTIFIER_SET))
    for k in ("kahin", *FABRICATIONS, "yaziyla")
] + [("yeni_kahin_oto", MEASUREMENTS / "yeni_kahin_oto.jsonl", PHASE8B_SET)]


def _git_module(name: str, path: str) -> types.ModuleType:
    """Runs one source file as it was at OLD_COMMIT and registers it under `name`."""
    source_code = subprocess.run(["git", "show", f"{OLD_COMMIT}:{path}"], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout
    m = types.ModuleType(name)
    sys.modules[name] = m   # dataclasses look their module up in sys.modules
    exec(compile(source_code, path.rsplit("/", 1)[-1], "exec"), m.__dict__)
    return m


def load_old_verifier() -> types.ModuleType:
    # The old verifier imports the old citation module by its old (Turkish) package path, which no longer exists.
    # Load that module from the same commit too and expose it under the old path only while the verifier loads.
    old_packages = ("sayman.belge", "sayman.belge.alinti")
    sys.modules["sayman.belge"] = types.ModuleType("sayman.belge")
    try:
        _git_module("sayman.belge.alinti", "src/sayman/belge/alinti.py")
        return _git_module("eski_dogrulayici", "src/sayman/asistan/dogrulayici.py")
    finally:
        for name in old_packages:
            sys.modules.pop(name, None)


def verifier_calls(record: dict) -> list[dict]:
    return [{"arac": c["arac"], "argumanlar": c.get("argumanlar") or {}, "hata_mi": c.get("hata") is not None,
             "yanit": c["yanit"]} for c in record.get("cagrilar", [])]


def draft_correct(question: dict, record: dict, text: str, scopes: dict, roles: dict, this_year: int) -> bool | None:
    """Would this draft have been correct according to the scorer if it had been shown? None for unauthorized questions."""
    if question["bolum"] == "yetkisiz":
        return None
    single_record = {**record, "durum": "tamam", "cevap": text}
    return bool(scoring.score(question, single_record, scopes, roles, this_year)["cevap_dogru"])


def measure_run(label: str, path: Path, set_path: Path, old_verifier, scopes: dict, roles: dict) -> dict:
    v = json.loads(set_path.read_text(encoding="utf-8"))
    questions = {s["soru_id"]: s for s in v["sorular"]}
    today = dt.date.fromisoformat(v["simulasyon_gunu"])
    counts = Counter()
    examples = []
    question_lists = {"kazanilan": [], "kaybedilen": [], "yeni_gosterilen_eksik": []}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        k = json.loads(line)
        s = questions[k["soru_id"]]
        c = verifier_calls(k)
        decisions = []
        for t in k.get("taslaklar", []):
            e = old_verifier.dogrula(t["metin"], s["soru"], c, today).gecerli
            n = yeni.verify(t["metin"], s["soru"], c, today).gecerli
            decisions.append((e, n))
            counts["taslak"] += 1
            if e != n:
                direction = "ret_kabul" if n else "kabul_ret"
                correct = draft_correct(s, k, t["metin"], scopes, roles, this_year=today.year)
                outcome = {True: "dogru", False: "eksik", None: "yetkisiz"}[correct]
                counts[f"{direction}_{outcome}"] += 1
                if len(examples) < 12:
                    examples.append({"soru_id": k["soru_id"], "yon": direction, "sinif": outcome,
                                     "taslak": t["metin"][:240]})
        # the answer to show: the first draft that passes
        first_old = next((i for i, (e, _) in enumerate(decisions) if e), None)
        first_new = next((i for i, (_, n) in enumerate(decisions) if n), None)
        if first_old == first_new:
            continue
        replayable = first_new is not None and all(not (e and not n) for e, n in decisions[:first_new]) and (
            first_old is None or first_new < first_old)
        if not replayable:
            counts["gosterilen_degisti_oynatilamaz"] += 1
            continue
        text = k["taslaklar"][first_new]["metin"]
        correct = draft_correct(s, k, text, scopes, roles, today.year)
        old_correct = (draft_correct(s, k, k["taslaklar"][first_old]["metin"], scopes, roles, today.year)
                      if first_old is not None else False)
        counts[f"gosterilen_{'once' if first_old is not None else 'yeni'}_{ {True: 'dogru', False: 'eksik', None: 'yetkisiz'}[correct]}"] += 1
        if correct and not old_correct:
            counts["kazanilan_dogru_cevap"] += 1
            question_lists["kazanilan"].append(k["soru_id"])
        if old_correct and correct is False:
            counts["kaybedilen_dogru_cevap"] += 1
            question_lists["kaybedilen"].append(k["soru_id"])
        if correct is False:
            question_lists["yeni_gosterilen_eksik"].append(k["soru_id"])
    return {"sayilar": dict(sorted(counts.items())), "sorular": question_lists, "ornekler": examples}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    old_verifier = load_old_verifier()
    scopes = phase07_evaluate.scope_record_ids()
    roles = {pid: p.roller for pid, p in phase07_evaluate.chunks().items()}
    result = {"aciklama": __doc__.split("\n\n")[1].replace("\n", " "), "eski_commit": OLD_COMMIT,
             "model_kosulari": {}, "kontroller": {}}
    for section, runs in (("model_kosulari", MODEL_RUNS), ("kontroller", CONTROL_RUNS)):
        for label, path, set_path in runs:
            if not path.exists():
                result[section][label] = {"yok": True}
                continue
            result[section][label] = r = measure_run(label, path, set_path, old_verifier, scopes, roles)
            print(label, r["sayilar"])
    totals = Counter()
    for r in result["model_kosulari"].values():
        totals.update(r.get("sayilar", {}))
    # zeros are written too (the Phase 11 report counts a missing key as an error)
    result["model_toplami"] = {a: totals.get(a, 0) for a in sorted({*totals, *TOTAL_KEYS})}
    oracle_accept_to_reject = sum(r.get("sayilar", {}).get(k, 0) for e, r in result["kontroller"].items() if "kahin" in e
                          for k in ("kabul_ret_dogru", "kabul_ret_eksik", "kabul_ret_yetkisiz"))
    fabrication_reject_to_accept = sum(r.get("sayilar", {}).get(k, 0) for e, r in result["kontroller"].items()
                            if any(u in e for u in FABRICATIONS)
                            for k in ("ret_kabul_dogru", "ret_kabul_eksik", "ret_kabul_yetkisiz"))
    result["kontrol_ozeti"] = {"kahin_kabul_ret": oracle_accept_to_reject, "uydurma_ret_kabul": fabrication_reject_to_accept}
    print("model toplamı:", result["model_toplami"])
    print("kontrol özeti:", result["kontrol_ozeti"])
    OUTPUT.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("yazıldı:", OUTPUT.relative_to(ROOT))


if __name__ == "__main__":
    main()
