"""Faz 8b kimlik ölçümünün soru setini üretir, denetler ve dondurur (hiçbir model, kâhin dahil, çalıştırılmadan önce).

Set: tests/phase08b_identifier_questions.py (U, Y, D grupları; ön kayıt aynı dosyada).

Dışarıda tutulanlar (haric): Faz 7'nin iki setinin ve Faz 8b yeni setinin varlıkları, pilotun müşterileri ve
numaraları (data/olcum/faz8b_kimlik_pilot_seti.json, geliştirme verisi) ve pilot sorularının doğru cevapları
(veritabanından bulunur).

Denetimler (sonuçları dosyaya yazılır; ihlal varsa dosya yazılmaz):
- Sızıntı: Faz 8 eğitim verisi (data/egitim/sorular.json) ile bu set arasında Faz 8'in denetimi (kalıp, soru, kimlik,
  ad). Kimlik denetimine varlıklarla birlikte bütün doğru kimlikler girer.
- Örtüşme: sinir_ustu dışındaki sorularda doğru kimlikler ve varlıklar dışarıda tutulanlarla örtüşmez. sinir_ustu
  listelerinin örtüşmesi sayılır ve yazılır.
- Benzerlik: her sorunun pilot sorularına ve Faz 7 / Faz 8b sorularına en yakın benzerliği (bilgi için).

Çıktı:  reports/faz8b_kimlik_soru_seti.json

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\phase08b_generate_identifier_questions.py
"""

from __future__ import annotations

import csv
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))
sys.path.insert(0, str(KOK / "tests"))

import support.phase07_questions as f7  # noqa: E402
import support.phase08b_identifier_questions as k  # noqa: E402

from sayman.warehouse import metrics  # noqa: E402
from sayman.training.leakage import Degerlendirme, denetle, normal, soru_benzerligi  # noqa: E402
from sayman.data.clock import saat_oku  # noqa: E402

CIKTI = KOK / "reports" / "faz8b_kimlik_soru_seti.json"
FAZ7 = KOK / "reports" / "faz7_soru_seti.json"
FAZ8B = KOK / "reports" / "faz8b_soru_seti.json"
PILOT = KOK / "data" / "olcum" / "faz8b_kimlik_pilot_seti.json"
EGITIM = KOK / "data" / "egitim" / "sorular.json"
AMBAR = KOK / "data" / "ambar" / "sayman.duckdb"


def pilot_disi(v: k.Veri, pilot: dict) -> tuple[set[str], set[str], dict]:
    """(pilotun varlıkları ve tek cevapları, pilotun P2 listesi, açıklama). Pilot soruları elle yazıldı; cevapları
    burada veritabanından bulunur."""
    metin = " ".join(s["soru"] for s in pilot["sorular"])
    musteriler = {mid for mid, m in v.musteri.items() if m["ad"] in metin}
    q = v.q
    iade_ili = q("SELECT iliskili_fatura_id FROM faturalar WHERE fatura_id = 'F035906'")[0][0]
    kaynak_f = q("SELECT fatura_id FROM faturalar WHERE kaynak_fatura_no = '489440'")[0][0]
    p2 = [f["fatura_id"] for f in v.geciken(60)[:15]]
    p3 = [f["fatura_id"] for f in sorted(v.geciken(90), key=lambda f: f["acik_try_kurus"])[:1]]
    p5 = [r[0] for r in q("SELECT hareket_id FROM banka_hareketleri WHERE tarih BETWEEN '2026-03-01' AND '2026-03-31' "
                          "ORDER BY tutar_kurus LIMIT 3")]
    p6 = [r[0] for r in q("SELECT tahsilat_id FROM tahsilatlar WHERE tarih BETWEEN '2026-08-01' AND '2026-08-31' "
                          "ORDER BY tutar_kurus DESC LIMIT 3")]
    acik = [f for f in metrics.geciken_alacaklar(v.con, v.d, en_az_gun=-10**6)["faturalar"]
            if f["musteri_id"] in musteriler]
    p7 = sorted(acik, key=lambda f: (f["vade_tarihi"], f["fatura_id"]))[:3]
    odemeler = metrics.yaklasan_odemeler(v.con, v.d, 60)["odemeler"]
    p8 = [o["alis_fatura_id"] for o in sorted(odemeler, key=lambda o: -o["acik_kurus"])[:3]]
    tekil = musteriler | {"F035906", iade_ili, kaynak_f, "489440"} | set(p3) | set(p5) | set(p6) \
        | {f["fatura_id"] for f in p7} | set(p8)
    return tekil, set(p2), {"musteriler": sorted(musteriler), "tekil": sorted(tekil), "p2_listesi": p2}


def sizinti(sorular: list[dict], varliklar: set[str], egitim: list[dict]) -> dict:
    adlar = set()
    for dosya in ("musteriler.csv", "tedarikciler.csv"):
        with open(KOK / "data" / "uretilen" / dosya, encoding="utf-8") as f:
            adlar |= {normal(r["ad"]) for r in csv.DictReader(f)}
    kimlikler = set(varliklar) | {x for s in sorular for x in s["dogru_kimlikler"]}
    d = Degerlendirme(kaliplar=[s["soru"] for s in sorular], sorular=[s["soru"] for s in sorular],
                      kimlikler=kimlikler, adlar=adlar, madde_metinleri=set(), kurlar=set())
    r = denetle(egitim, d)
    return {"egitim_sorusu": len(egitim), "denetlenen_kimlik": len(kimlikler),
            "en_yakin_kalip_benzerligi": round(r.en_yakin_kalip, 3),
            "en_yakin_soru_benzerligi": round(r.en_yakin_soru, 3),
            "ihlal_sayisi": {a: len(b) for a, b in r.ihlaller.items()}, "ihlaller": r.ihlaller, "temiz": r.temiz}


def benzerlik(sorular: list[dict], obur: list[str]) -> list[dict]:
    sonuc = []
    for s in sorular:
        b, en = max((soru_benzerligi(normal(s["soru"]), normal(o)), o) for o in obur)
        sonuc.append({"soru_id": s["soru_id"], "soru": s["soru"], "en_yakin": en, "benzerlik": round(b, 3)})
    return sorted(sonuc, key=lambda x: -x["benzerlik"])


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    saat = saat_oku(KOK / "data" / "saat.json")
    faz7 = json.loads(FAZ7.read_text(encoding="utf-8"))
    faz8b = json.loads(FAZ8B.read_text(encoding="utf-8"))
    pilot = json.loads(PILOT.read_text(encoding="utf-8"))
    egitim = json.loads(EGITIM.read_text(encoding="utf-8"))["sorular"]
    onceki = set(faz7["varliklar"]["gelistirme"]) | set(faz7["varliklar"]["saklanan"]) | set(faz8b["varliklar"]["yeni"])
    v = k.Veri(AMBAR, saat.simdi, set())
    try:
        pilot_tekil, pilot_p2, pilot_aciklama = pilot_disi(v, pilot)
        v.haric = onceki | pilot_tekil | pilot_p2
        sorular = k.soru_seti(v)
        varliklar = sorted(v.varliklar)
    finally:
        v.kapat()

    kimlikler = [s["soru_id"] for s in sorular]
    assert len(kimlikler) == len(set(kimlikler)), "soru kimlikleri tekil olmalı"
    haric = onceki | pilot_tekil | pilot_p2
    ihlal = [{"soru_id": s["soru_id"], "ortak": sorted((set(s["dogru_kimlikler"]) | set(s.get("varliklar", []))) & haric)}
             for s in sorular if s["mekanizma"] != "sinir_ustu"
             and (set(s["dogru_kimlikler"]) | set(s.get("varliklar", []))) & haric]
    sinir = [{"soru_id": s["soru_id"], "dogru": len(s["dogru_kimlikler"]),
              "faz7_faz8b": len(set(s["dogru_kimlikler"]) & onceki),
              "pilot_p2": len(set(s["dogru_kimlikler"]) & pilot_p2)} for s in sorular if s["mekanizma"] == "sinir_ustu"]
    print("örtüşme ihlali:", ihlal)
    print("sinir_ustu örtüşmesi:", sinir)
    if ihlal:
        raise SystemExit("dışarıda tutulan kayıtlarla örtüşme var; set yazılmadı")
    sz = sizinti(sorular, set(varliklar), egitim)
    print("sızıntı:", json.dumps({a: b for a, b in sz.items() if a != "ihlaller"}, ensure_ascii=False))
    if not sz["temiz"]:
        print(json.dumps(sz["ihlaller"], ensure_ascii=False, indent=1))
        raise SystemExit("sızıntı var; set yazılmadı")
    pilot_b = benzerlik(sorular, [s["soru"] for s in pilot["sorular"]])
    onceki_b = benzerlik(sorular, [s["soru"] for s in faz7["sorular"]] + [s["soru"] for s in faz8b["sorular"]])
    veri = {
        "aciklama": "Faz 8b kimlik ölçümü soru seti. Hiçbir model (kâhin dahil) çalıştırılmadan önce üretildi ve "
                    "donduruldu; ön kayıt aynı anda yazıldı. Üretim: scripts/phase08b_generate_identifier_questions.py, sorular: "
                    "tests/phase08b_identifier_questions.py, analiz: scripts/phase08b_identifier_analysis.py.",
        "yazilma_tarihi": k.YAZILMA_TARIHI, "dondurma_zamani": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "simulasyon_gunu": saat.simdi.isoformat(),
        "ozet": f7.ozet(sorular),
        "sayilar": {"grup": dict(sorted(Counter(s["grup"] for s in sorular).items())),
                    "mekanizma": dict(Counter(f"{s['grup']}/{s['mekanizma']}" for s in sorular)),
                    "rol": dict(Counter(s["rol"] for s in sorular))},
        "hedefler": [], "on_kayit": k.ON_KAYIT,
        "denetimler": {"sizinti": sz, "ortusme": {"ihlal": ihlal, "sinir_ustu": sinir, "pilot": pilot_aciklama},
                       "pilot_benzerligi": pilot_b[:8], "faz7_faz8b_benzerligi": onceki_b[:8]},
        "varliklar": {k.KIMLIK: varliklar},
        "sorular": sorular,
    }
    CIKTI.write_text(json.dumps(veri, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(veri["sayilar"], ensure_ascii=False))
    print("pilota en yakın:", [(x["soru_id"], x["benzerlik"]) for x in pilot_b[:5]])
    print("Faz 7/8b'ye en yakın:", [(x["soru_id"], x["benzerlik"], x["en_yakin"]) for x in onceki_b[:3]])
    print("özet", veri["ozet"])


if __name__ == "__main__":
    main()
