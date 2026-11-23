"""Faz 9 analizi: elle araçlar ↔ otomatik araçlar (ön kayıt README "Faz 9" ve reports/faz9_on_kayit.json).

The line above is written verbatim into the report as `aciklama`, so it stays in Turkish. It reads: "Phase 9 analysis:
hand-written tools ↔ generated tools (preregistration in the README "Faz 9" and reports/faz9_on_kayit.json)."

Inputs: reports/faz9_el_yeni.jsonl, reports/faz9_oto_yeni.jsonl (the third arm reports/faz9_duzeltme_yeni.jsonl if present),
the frozen classes reports/faz9_siniflar.json and the Phase 8b question set. Both arms go through the same scorer (the
scorer in scripts/phase07_evaluate.py). Because "kaynak" is empty in the generated envelope, in the generated rows the
identifiers are collected from the result itself before the scope check (the source-identifier collector in
sayman/experiment/generated_tools.py); one negative control row must be caught.

Primary measure: the correct shown answers on the questions of the direct classes (tek_cagri, zincir), paired; exact
McNemar (two-sided).

Output: reports/faz9_karsilastirma.json

    .venv\\Scripts\\python -I scripts\\phase09_analysis.py
"""

from __future__ import annotations

import copy
import json
import statistics
import sys
from collections import Counter
from math import comb
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import phase07_evaluate  # noqa: E402

from sayman.experiment.generated_tools import source_identifiers  # noqa: E402

QUESTION_SET = ROOT / "reports" / "faz8b_soru_seti.json"
CLASSES_FILE = ROOT / "reports" / "faz9_siniflar.json"
ARMS = {"el": "faz9_el_yeni.jsonl", "otomatik": "faz9_oto_yeni.jsonl", "duzeltme": "faz9_duzeltme_yeni.jsonl"}
DIRECT = ("tek_cagri", "zincir")
CEILING = {"el": "85/86", "otomatik": "37/86"}
# Read by hand after the run: the automatic arm found the correct invoice number but the answer went on as "…497351'dir
# (fatura ID: …)"; the verifier's number parser took the word "fatura" after the number for a unit and made the number
# "adet", so it did not match the source text number (kind "tutar"). Same verifier in both arms; the blind spot depends on the wording.
BLIND_SPOTS = ["Y-fat_son-01", "Y-fat_son-03"]


def exact_mcnemar(b: int, c: int) -> float:
    """Two-sided exact McNemar: the two-tail probability of b under binomial(n, 1/2) over the b + c discordant pairs."""
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(comb(n, k) for k in range(min(b, c) + 1)) / 2 ** n)


def add_auto_source(row: dict) -> dict:
    """Adds the identifiers in the result as "kaynak" to the successful calls of an automatic row (for scoring only)."""
    s = copy.deepcopy(row)
    for c in s.get("cagrilar", []):
        if not c.get("hata") and isinstance(c.get("yanit"), dict):
            c["yanit"]["kaynak"] = source_identifiers(c["yanit"].get("sonuc"))
    return s


def _median(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 1) if xs else None


def _p90(xs):
    xs = sorted(x for x in xs if x is not None)
    return round(xs[min(len(xs) - 1, int(0.9 * len(xs)))], 1) if xs else None


DEFAULT_PAGE_SIZE = 25  # default page size of the fake API


def summarize_question(p: dict, row: dict) -> dict:
    prompt_tokens = [t.get("istem") for t in row.get("tokenlar", []) if t.get("istem") is not None]
    page_sizes = [c.get("argumanlar", {}).get("sayfa_boyutu") for c in row.get("cagrilar", [])]
    return {"dogru": p["cevap_dogru"], "gosterilen_yanlis": p["yanlis_sayi_suphesi"], "durum": row["durum"],
            "hata_turu": p["hata_turu"], "sure_s": row.get("sure_s"), "istem_en_cok": max(prompt_tokens, default=None),
            "arac_cagrisi": len(row.get("cagrilar", [])), "kapsam_disi": p["kapsam_disi"],
            "durdurulan_dogru_taslak": p.get("taslak_tablosu", {}).get("reddedildi_dogru", 0),
            "kapsam_notu": row.get("kapsam_notu", False),
            "buyuk_sayfa": any(isinstance(b, int) and b > DEFAULT_PAGE_SIZE for b in page_sizes)}


def summarize_set(questions: list[dict]) -> dict:
    n = len(questions)
    return {"n": n, "dogru": sum(s["dogru"] for s in questions),
            "dogru_orani": round(sum(s["dogru"] for s in questions) / n, 4) if n else None,
            "gosterilen_yanlis": sum(s["gosterilen_yanlis"] for s in questions),
            "durumlar": dict(Counter(s["durum"] for s in questions)),
            "baglam_siniri": sum(s["durum"] == "baglam_siniri" for s in questions),
            "sure_medyan_s": _median(s["sure_s"] for s in questions), "sure_p90_s": _p90(s["sure_s"] for s in questions),
            "istem_medyan": _median(s["istem_en_cok"] for s in questions),
            "istem_en_cok": max((s["istem_en_cok"] or 0 for s in questions), default=None),
            "arac_cagrisi_medyan": _median(s["arac_cagrisi"] for s in questions),
            "durdurulan_dogru_taslak": sum(s["durdurulan_dogru_taslak"] for s in questions),
            "kapsam_disi_satir": sum(bool(s["kapsam_disi"]) for s in questions)}


def score_arm(arm: str, questions: list[dict], rows: list[dict], scopes: dict, this_year: int) -> dict[str, dict]:
    if arm == "otomatik":
        rows = [add_auto_source(s) for s in rows]
    scores, _ = phase07_evaluate.score_all(questions, rows, scopes, {}, this_year)
    row = {s["soru_id"]: s for s in rows}
    return {p["soru_id"]: summarize_question(p, row[p["soru_id"]]) for p in scores}


def negative_control(questions: list[dict], scopes: dict, this_year: int) -> list[str]:
    """A fake automatic row with an out-of-scope identifier (one the CFO sees) for a question in the sales role: it must be caught."""
    s = next(x for x in questions if x["rol"] == "satis_yurtici")
    foreign_id = next(iter(scopes["satis_ihracat"]["faturalar"]))
    row = {"soru_id": s["soru_id"], "durum": "tamam", "cevap": "", "sure_s": 0, "tokenlar": [], "taslaklar": [],
             "cagrilar": [{"arac": "faturalar_api_v1_faturalar_get", "argumanlar": {}, "hata": None,
                           "yanit": {"sonuc": {"kayitlar": [{"fatura_id": foreign_id}]}, "kaynak": {}}}]}
    return score_arm("otomatik", [s], [row], scopes, this_year)[s["soru_id"]]["kapsam_disi"]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    data = json.loads(QUESTION_SET.read_text(encoding="utf-8"))
    classes = json.loads(CLASSES_FILE.read_text(encoding="utf-8"))
    class_of = {k["soru_id"]: k["sinif"] for k in classes["sorular"]}
    questions = [s for s in data["sorular"] if s["soru_id"] in class_of]
    this_year = int(data["simulasyon_gunu"][:4])
    scopes = phase07_evaluate.scope_record_ids()

    arms = {}
    for arm, file in ARMS.items():
        path = ROOT / "reports" / file
        if path.exists():
            rows = phase07_evaluate.read_jsonl(path)
            arms[arm] = {"dosya": file, "satir": len(rows), "satirlar": rows,
                           "sorular": score_arm(arm, questions, rows, scopes, this_year)}

    result = {"aciklama": __doc__.split("\n\n")[0], "siniflar_ozeti": classes["ozet"],
             "sinif_sayilari": classes["sayilar"], "kahin_tavanlari": CEILING,
             "negatif_kontrol_yakalanan": negative_control(questions, scopes, this_year), "kollar": {}}
    assert result["negatif_kontrol_yakalanan"], "negatif kontrol yakalanmadı: kapsam denetimi otomatik kolda boş"
    for arm, k in arms.items():
        q = k["sorular"]
        result["kollar"][arm] = {
            "dosya": k["dosya"], "tamamlanan": f"{len(q)}/{len(questions)}",
            "tum_set": summarize_set(list(q.values())),
            "dogrudan": summarize_set([v for i, v in q.items() if class_of[i] in DIRECT]),
            "siniflar": {c: summarize_set([v for i, v in q.items() if class_of[i] == c]) for c in sorted(set(class_of.values()))},
            "sizinti": sorted(i for i, v in q.items() if v["kapsam_disi"]),
            # In how many of the questions that hit the context limit did the model ask for a page larger than the default (25)?
            "baglam_siniri_buyuk_sayfa": sum(v["durum"] == "baglam_siniri" and v["buyuk_sayfa"] for v in q.values()),
            "sorular": {i: {"sinif": class_of[i], **{a: v[a] for a in ("dogru", "gosterilen_yanlis", "durum", "hata_turu")}}
                        for i, v in sorted(q.items())},
        }

    if "el" in arms and "otomatik" in arms:
        manual, auto = arms["el"]["sorular"], arms["otomatik"]["sorular"]
        shared = [i for i in manual if i in auto and class_of[i] in DIRECT]
        b = [i for i in shared if manual[i]["dogru"] and not auto[i]["dogru"]]
        c = [i for i in shared if auto[i]["dogru"] and not manual[i]["dogru"]]
        result["birincil"] = {
            "n": len(shared), "ikisi_dogru": sum(manual[i]["dogru"] and auto[i]["dogru"] for i in shared),
            "yalniz_el": b, "yalniz_otomatik": c,
            "ikisi_yanlis": sum(not manual[i]["dogru"] and not auto[i]["dogru"] for i in shared),
            "el_orani": round(sum(manual[i]["dogru"] for i in shared) / len(shared), 4) if shared else None,
            "otomatik_orani": round(sum(auto[i]["dogru"] for i in shared) / len(shared), 4) if shared else None,
            "mcnemar_p": round(exact_mcnemar(len(b), len(c)), 4),
            "karar": None,
        }
        p = result["birincil"]
        p["karar"] = "fark var" if p["mcnemar_p"] < 0.05 else "bu örneklemde fark gösterilemedi"
        # Sensitivity (not in the preregistration, added after the run): if one or two of the discordant pairs had flipped.
        p["duyarlilik"] = {
            "bir_cift_donerse_p": round(exact_mcnemar(len(b) - 1, len(c)), 4),
            "dogrulayici_kor_noktasi_haric_p": round(exact_mcnemar(len(b) - len(BLIND_SPOTS), len(c)), 4),
            "dogrulayici_kor_noktasi": BLIND_SPOTS,
        }
        assert all(i in b for i in BLIND_SPOTS)
        result["otomatik_toplama_ve_yok"] = {
            "gosterilen_dogru": sorted(i for i, v in auto.items() if class_of[i] in ("toplama", "yok") and v["dogru"]),
            "gosterilen_yanlis": sorted(i for i, v in auto.items()
                                        if class_of[i] in ("toplama", "yok") and v["gosterilen_yanlis"]),
            "durdurulan_dogru_taslak": {i: v["durdurulan_dogru_taslak"] for i, v in auto.items()
                                        if class_of[i] in ("toplama", "yok") and v["durdurulan_dogru_taslak"]},
        }
        result["engelli"] = {i: {"el": manual[i]["dogru"], "otomatik": auto[i]["dogru"], "otomatik_durum": auto[i]["durum"]}
                            for i in manual if class_of[i] == "engelli" and i in auto}
    if "el" in arms and "duzeltme" in arms:
        manual, corrected = arms["el"]["sorular"], arms["duzeltme"]["sorular"]
        shared = [i for i in manual if i in corrected]
        result["ucuncu_kol"] = {
            "not": "Keşif amaçlı: düzeltmeler Faz 8b sonuçlarına bakılarak seçildi; aynı 86 soru. Yalnızca betimleyici.",
            "yalniz_el": sorted(i for i in shared if manual[i]["dogru"] and not corrected[i]["dogru"]),
            "yalniz_duzeltme": sorted(i for i in shared if corrected[i]["dogru"] and not manual[i]["dogru"]),
            "yama": "reports/faz9_ucuncu_kol_yama.diff",
            # In which questions did the corrections really take effect? (The remaining differences cannot be attributed to a correction.)
            "musteri_adi_ile_cek_portfoyu": sorted(
                s["soru_id"] for s in arms["duzeltme"]["satirlar"]
                if any(c["arac"] == "cek_portfoyu" and c.get("argumanlar", {}).get("musteri_adi") for c in s["cagrilar"])),
            "yeni_en_fazla_mesaji": sorted(
                s["soru_id"] for s in arms["duzeltme"]["satirlar"]
                if any("ile yeniden deneyin" in json.dumps(c, ensure_ascii=False) for c in s["cagrilar"])),
        }

    (ROOT / "reports" / "faz9_karsilastirma.json").write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n",
                                                            encoding="utf-8")
    for arm, k in result["kollar"].items():
        t, d = k["tum_set"], k["dogrudan"]
        print(f"{arm:9s} tüm: {t['dogru']}/{t['n']} yanlış {t['gosterilen_yanlis']} bağlam {t['baglam_siniri']} "
              f"süre medyan {t['sure_medyan_s']} p90 {t['sure_p90_s']} istem medyan {t['istem_medyan']} "
              f"en çok {t['istem_en_cok']} | doğrudan: {d['dogru']}/{d['n']} yanlış {d['gosterilen_yanlis']} "
              f"| sızıntı {len(k['sizinti'])}")
    if "birincil" in result:
        print("birincil", json.dumps(result["birincil"], ensure_ascii=False))
        print("otomatik toplama/yok", json.dumps(result["otomatik_toplama_ve_yok"], ensure_ascii=False))
    if "ucuncu_kol" in result:
        print("üçüncü kol", json.dumps(result["ucuncu_kol"], ensure_ascii=False))
    print("negatif kontrol", result["negatif_kontrol_yakalanan"])


if __name__ == "__main__":
    main()
