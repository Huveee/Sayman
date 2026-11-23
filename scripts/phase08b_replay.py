"""Phase 8b: passes the recorded Phase 7/8 answers through the verifier again (without running the model).

First the setup itself is tested: for every recorded draft, today's verifier must give exactly the decision given that day
(gecerli, the unsourced numbers). Then, for each new rule, we count: how many of the shown answers get caught by the new
rule, how many of these are correct according to the scorer (stopped by mistake) and how many are wrong (caught).

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase08b_replay.py
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from sayman.assistant.verifier import verify  # noqa: E402

REPORTS = ROOT / "reports"
MEASUREMENTS = ROOT / "data" / "olcum"

# (label, the record of every question, score report or None). The oracle answers every question correctly: for the false-reject measure.
RUNS = [
    ("faz7_gelistirme", REPORTS / "faz7_gelistirme.jsonl", REPORTS / "faz7_olcum_gelistirme.json"),
    ("faz7_saklanan", REPORTS / "faz7_saklanan.jsonl", REPORTS / "faz7_olcum_saklanan.json"),
    ("faz8_temel_kendi_gelistirme", REPORTS / "faz8_temel_kendi_gelistirme.jsonl",
     REPORTS / "faz8_temel_kendi_olcum_gelistirme.json"),
    ("faz8_temel_kendi_saklanan", REPORTS / "faz8_temel_kendi_saklanan.jsonl",
     REPORTS / "faz8_temel_kendi_olcum_saklanan.json"),
    ("faz8_ayarli_gelistirme", REPORTS / "faz8_ayarli_gelistirme.jsonl", REPORTS / "faz8_ayarli_olcum_gelistirme.json"),
    ("faz8_ayarli_saklanan", REPORTS / "faz8_ayarli_saklanan.jsonl", REPORTS / "faz8_ayarli_olcum_saklanan.json"),
    ("kahin_gelistirme", MEASUREMENTS / "gelistirme_kahin.jsonl", None),
    ("kahin_saklanan", MEASUREMENTS / "saklanan_kahin.jsonl", None),
]
# Negative control: writes another customer's value in the customer questions; the ownership rule must catch it.
CONTROLS = [
    ("baska_musteri_gelistirme", MEASUREMENTS / "gelistirme_baska_musteri.jsonl"),
    ("baska_musteri_saklanan", MEASUREMENTS / "saklanan_baska_musteri.jsonl"),
]


def read_questions() -> tuple[dict, dt.date]:
    v = json.loads((REPORTS / "faz7_soru_seti.json").read_text(encoding="utf-8"))
    return {s["soru_id"]: s for s in v["sorular"]}, dt.date.fromisoformat(v["simulasyon_gunu"])


def verifier_calls(record: dict) -> list[dict]:
    """The calls in the record in the form the verifier expects (`hata` in the record, `hata_mi` in the verifier)."""
    return [{"arac": c["arac"], "argumanlar": c.get("argumanlar") or {}, "hata_mi": c.get("hata") is not None,
             "yanit": c["yanit"]} for c in record.get("cagrilar", [])]


def read_records(path: Path) -> list[dict]:
    return [json.loads(s) for s in path.read_text(encoding="utf-8").splitlines() if s.strip()]


def read_scores(path: Path | None) -> dict:
    if path is None or not path.exists():
        return {}
    return {p["soru_id"]: p for p in json.loads(path.read_text(encoding="utf-8"))["sorular"]}


def check_setup() -> dict:
    """Does today's verifier give the recorded decisions exactly?"""
    questions_by_id, today = read_questions()
    result = {}
    for label, path, _ in RUNS:
        counts = Counter()
        differences = []
        for k in read_records(path):
            c = verifier_calls(k)
            drafts = k.get("taslaklar", [])
            for i, t in enumerate(drafts):
                d = verify(t["metin"], questions_by_id[k["soru_id"]]["soru"], c, today)
                same = d.legacy_valid == t["gecerli"] and [x.metin for x in d.unsourced] == t["kaynaksiz"]
                # The last draft was written after all the calls; the earlier ones may not have seen the calls made later.
                position = "son" if i == len(drafts) - 1 else "ara"
                counts[f"{position}_{'ayni' if same else 'farkli'}"] += 1
                if not same and len(differences) < 5:
                    differences.append({"soru_id": k["soru_id"], "kayitli": [t["gecerli"], t["kaynaksiz"]],
                                    "simdi": [d.gecerli, [x.metin for x in d.unsourced]]})
        result[label] = {**counts, "ornek_farklar": differences}
    return result


def _is_correct(p: dict | None) -> bool | None:
    """Is the shown answer correct according to the scorer (tool and document questions); None for unauthorized or when there is no score."""
    if p is None or p.get("bolum") == "yetkisiz":
        return None
    return bool(p.get("cevap_dogru"))


def apply_new_rules(runs=RUNS, controls=CONTROLS) -> dict:
    """Passes every shown answer (the last draft of a turn whose status is "tamam") through the new rules."""
    questions_by_id, today = read_questions()
    result = {}
    for label, path, score_path in [*runs, *[(e, y, None) for e, y in controls]]:
        oracle = label.startswith("kahin")
        score = read_scores(score_path)
        counts = Counter()
        examples = []
        for k in read_records(path):
            if k["durum"] != "tamam" or not k.get("taslaklar"):
                continue
            s = questions_by_id[k["soru_id"]]
            d = verify(k["taslaklar"][-1]["metin"], s["soru"], verifier_calls(k), today)
            correct = True if oracle else _is_correct(score.get(k["soru_id"]))
            outcome = {True: "dogru", False: "yanlis", None: "puansiz"}[correct]
            counts["gosterilen"] += 1
            for rule, flagged in (("sahipsiz", d.ownerless), ("kimliksiz", bool(d.unknown_identifiers)),
                                   ("kapsam_eksik", d.missing_scope is not None)):
                if flagged:
                    counts[f"{rule}_{outcome}"] += 1
                    examples.append({"kural": rule, "soru_id": k["soru_id"], "sinif": outcome, "rol": s["rol"],
                                     "kimliksiz": d.unknown_identifiers, "kapsam": d.missing_scope,
                                     "cevap": k["taslaklar"][-1]["metin"][:160]})
            if s["rol"] != "cfo" and s["bolum"] != "belge":
                counts["satis_gosterilen"] += 1
        result[label] = {"sayilar": dict(sorted(counts.items())), "ornekler": examples}
    return result


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    check_result = check_setup()
    differing_last = {e: v.get("son_farkli", 0) for e, v in check_result.items() if v.get("son_farkli")}
    print("düzenek: son taslaklarda farklı karar:", differing_last or "yok")
    result = apply_new_rules()
    for label, v in result.items():
        print(label, v["sayilar"])
    if "--ornekler" in sys.argv:
        for label, v in result.items():
            for o in v["ornekler"]:
                print(label, json.dumps(o, ensure_ascii=False))


if __name__ == "__main__":
    main()
