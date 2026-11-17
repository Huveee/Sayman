"""Faz 5: belge aramasını soru setiyle ölçer.

Ayar seti (belgelerin yarısı, kalıp çiftlerinin bir yanı) üzerinde istenildiği kadar çalıştırılabilir; her denemenin
sonucu --kaydet ile reports/faz5_ayar_denemeleri.json'a eklenir. Ayrılmış set, ayarlar dondurulduktan sonra
--ayrilmis ile bir kez çalıştırılır; dört yöntemin sonucu yan yana reports/faz5_arama.json'a yazılır. Ona bakıp
ayar değiştirilmez.

Bütün sorular CFO rolüyle sorulur (en geniş arama alanı: bütün belgeler, kanunlar dahil).

Girdi:  data/belge/belgeler.jsonl, data/belge/sorular.json (scripts/faz5_belge_uret.py)
        data/belge/onbellek/gomme_bge-m3.npz (scripts/faz5_dizin_kur.py)
        data/modeller/bge-reranker-v2-m3 (yalnızca önbellekte olmayan sıralama skorları için)

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\faz5_arama_olc.py --yontem karma_sirala
    .venv\\Scripts\\python -I scripts\\faz5_arama_olc.py --yontem karma --rrf-k 30 --kaydet "k=30 denendi"
    .venv\\Scripts\\python -I scripts\\faz5_arama_olc.py --ayrilmis
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))
sys.path.insert(0, str(KOK / "tests"))

import faz5_puanlama as puan  # noqa: E402

from sayman.belge import kutuphane  # noqa: E402
from sayman.belge.arama import YONTEMLER, Arayici, ilk  # noqa: E402

BELGE = KOK / "data" / "belge"
GOMME = BELGE / "onbellek" / "gomme_bge-m3.npz"
SIRALAMA = BELGE / "onbellek" / "sirala_bge-reranker-v2-m3.json"
MODEL = KOK / "data" / "modeller" / "bge-reranker-v2-m3"
DENEMELER = KOK / "reports" / "faz5_ayar_denemeleri.json"
SONUC = KOK / "reports" / "faz5_arama.json"
ROL = "cfo"
K = 10

# Ayar setinde seçilen ve ayrılmış set çalıştırılmadan önce dondurulan ayarlar.
DONMUS = {"rrf_k": 60, "derinlik": 50, "aday": 20}


def arayici_kur(parcalar, ayar: dict, yontem: str) -> Arayici:
    anlam = siralayici = None
    if yontem != "bm25":
        from sayman.belge.anlam import AnlamDizini, GommeOnbellegi
        anlam = AnlamDizini(parcalar, GommeOnbellegi(GOMME))
    if yontem == "karma_sirala":
        from sayman.belge.siralayici import Siralayici
        siralayici = Siralayici(MODEL, SIRALAMA)
    return Arayici(parcalar, anlam, siralayici, **ayar)


def olc(arayici: Arayici, yontem: str, sorular: list[dict]) -> tuple[dict, dict]:
    bas = time.perf_counter()
    sonuclar = {s["soru_id"]: ilk(arayici.skorlar(s["soru"], ROL, yontem), K) for s in sorular}
    sure = time.perf_counter() - bas
    if arayici.siralayici:
        arayici.siralayici.kaydet()
    return puan.puanla(sorular, sonuclar), {"soru_basina_ms": round(1000 * sure / len(sorular), 1)}


def kisa(sonuc: dict) -> dict:
    return {k: v for k, v in sonuc.items() if k != "kacanlar"} | {"kacan": len(sonuc["kacanlar"])}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--yontem", choices=YONTEMLER)
    ap.add_argument("--rrf-k", type=int, default=DONMUS["rrf_k"])
    ap.add_argument("--derinlik", type=int, default=DONMUS["derinlik"])
    ap.add_argument("--aday", type=int, default=DONMUS["aday"])
    ap.add_argument("--kaydet", metavar="DEGISIKLIK", help="sonucu ayar denemeleri günlüğüne ekle")
    ap.add_argument("--goster", type=int, default=0, help="kaçan sorulardan ilk N tanesini göster")
    ap.add_argument("--ayrilmis", action="store_true", help="dondurulmuş ayarlarla ayrılmış seti bir kez çalıştır")
    a = ap.parse_args()

    parcalar = kutuphane.parcalar(kutuphane.oku(BELGE / "belgeler.jsonl"))
    sorular = json.loads((BELGE / "sorular.json").read_text(encoding="utf-8"))

    if a.ayrilmis:
        if SONUC.exists():
            raise SystemExit(f"{SONUC} zaten var: ayrılmış set bir kez çalıştırılır.")
        arayici = arayici_kur(parcalar, DONMUS, "karma_sirala")
        rapor = {"aciklama": "Ayrılmış set; ayarlar ayar setinde dondurulduktan sonra bir kez çalıştırıldı. Bütün "
                             "sorular CFO rolüyle (bütün belgeler, kanunlar dahil) soruldu. Kabul ölçütü: "
                             "karma_sirala yönteminin isabet@5 değeri ≥ 0,85.",
                 "ayarlar": DONMUS, "parca": len(parcalar), "yontemler": {}}
        for set_adi in ("ayar", "ayrilmis"):
            for yontem in YONTEMLER:
                sonuc, _ = olc(arayici, yontem, sorular[set_adi])  # süre burada anlamsız (önbellek); bkz. faz5_kaynak
                rapor["yontemler"].setdefault(yontem, {})[set_adi] = kisa(sonuc)
                if set_adi == "ayrilmis":
                    rapor["yontemler"][yontem]["ayrilmis_kacanlar"] = sonuc["kacanlar"]
        rapor["kabul"] = rapor["yontemler"]["karma_sirala"]["ayrilmis"]["isabet@5"] >= 0.85
        SONUC.write_text(json.dumps(rapor, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({y: {s: r[s]["isabet@5"] for s in ("ayar", "ayrilmis")}
                          for y, r in rapor["yontemler"].items()}, ensure_ascii=False, indent=1))
        print("kabul:", rapor["kabul"])
        return

    if not a.yontem:
        raise SystemExit("--yontem ya da --ayrilmis gerekli")
    ayar = {"rrf_k": a.rrf_k, "derinlik": a.derinlik, "aday": a.aday}
    arayici = arayici_kur(parcalar, ayar, a.yontem)
    sonuc, sure = olc(arayici, a.yontem, sorular["ayar"])
    print(json.dumps({"yontem": a.yontem, "ayarlar": ayar, **kisa(sonuc), **sure}, ensure_ascii=False, indent=1))

    if a.goster:
        for sid in sonuc["kacanlar"][: a.goster]:
            s = next(x for x in sorular["ayar"] if x["soru_id"] == sid)
            print(f"\n- {sid} [{s['stil']}] {s['soru']}\n  doğru: {arayici.parca[s['dogru_parca']].madde_baslik}")
            for p, skor in arayici.ara(s["soru"], ROL, 5, a.yontem):
                print(f"  {skor:7.3f} {p.parca_id} {p.belge_baslik[:30]} / {p.madde_baslik}")

    if a.kaydet:
        gunluk = (json.loads(DENEMELER.read_text(encoding="utf-8")) if DENEMELER.exists() else
                  {"aciklama": "Faz 5 belge aramasının ayar denemeleri. Hepsi yalnızca ayar setinde (CFO rolüyle) "
                               "yapıldı. Ayrılmış set ayarlar dondurulduktan sonra bir kez çalıştırılır. Soru seti "
                               "ve puanlama (tests/faz5_sorular.py, tests/faz5_puanlama.py) aramadan önce yazıldı.",
                   "denemeler": []})
        gunluk["denemeler"].append({"no": len(gunluk["denemeler"]) + 1, "yontem": a.yontem,
                                    "ayarlar": ayar if a.yontem != "bm25" else None,
                                    "degisiklik": a.kaydet, "ayar": kisa(sonuc)})
        DENEMELER.write_text(json.dumps(gunluk, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
