"""Faz 8 karşılaştırması: temel model (Faz 7 ölçümü) ile ince ayarlı model, aynı sorularda soru soru.

İki ölçüm raporunu (`reports/<etiket>_olcum_<set>.json`) okur. Önce ikisinin aynı soru setiyle ve aynı sistemle
(istem, kod, araçlar, belgeler) alındığını denetler; değilse durur. Sonra iki tablo yazar:

- hedef tablosu: Faz 7 hedefleri, iki modelin değeri ve geçip geçmediği;
- eşleştirme: her metrikte iyileşen / kötüleşen soru sayısı ve McNemar p değeri. Araç soruları ayrıca "eğitimde
  yok" (Faz 8 eğitiminin hiç görmediği 8 şablon) ve "eğitimde var" diye ayrılır; yetkisiz denemeler türlerine göre.

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\faz8_karsilastir.py --set gelistirme
    .venv\\Scripts\\python -I scripts\\faz8_karsilastir.py --set saklanan
    .venv\\Scripts\\python -I scripts\\faz8_karsilastir.py --set gelistirme --temel faz7 --ayarli faz7   # deneme
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))
sys.path.insert(0, str(KOK / "tests"))

from support.phase08_training_questions import HARIC_NIYETLER  # noqa: E402
from sayman.evaluation import pairing  # noqa: E402

SORU_SETI = KOK / "reports" / "faz7_soru_seti.json"


def rapor(etiket: str, kume: str) -> dict:
    p = KOK / "reports" / f"{etiket}_olcum_{kume}.json"
    if not p.exists():
        raise SystemExit(f"rapor yok: {p.relative_to(KOK)}")
    return json.loads(p.read_text(encoding="utf-8"))


def denetle(t: dict, a: dict) -> list[str]:
    """Karşılaştırmayı geçersiz kılacak farklar (hata) ve dikkat edilecekler (uyarı)."""
    hatalar = []
    for alan in ("set", "soru_seti_ozeti"):
        if t[alan] != a[alan]:
            hatalar.append(f"{alan} farklı: {t[alan]} ↔ {a[alan]}")
    if t["sistem"]["ozet"] != a["sistem"]["ozet"]:
        farkli = [k for k in t["sistem"] if t["sistem"][k] != a["sistem"].get(k)]
        hatalar.append(f"sistem özeti farklı ({', '.join(farkli)})")
    for ad, r in (("temel", t), ("ayarli", a)):
        bitti, toplam = r["tamamlanan"].split("/")
        if bitti != toplam:
            hatalar.append(f"{ad} ölçümü bitmemiş: {r['tamamlanan']}")
    if [h["ad"] for h in t["hedefler"]] != [h["ad"] for h in a["hedefler"]]:
        hatalar.append("hedef listeleri farklı")
    return hatalar


def ozetle_uyusuyor_mu(r: dict, sonuc_bolum: dict, taraf: str) -> list[str]:
    """Bu modülün oranları Faz 7 özetindekilerle aynı mı? Tanım kayarsa karşılaştırma yanlış olur."""
    farklar = []
    for bolum, kayit in sonuc_bolum.items():
        for ad, satir in kayit["metrikler"].items():
            beklenen = r["ozet"][bolum].get(ad)
            if beklenen is None:
                continue
            bulunan = satir[taraf]
            if isinstance(beklenen, int) and not isinstance(beklenen, bool):  # sızıntı özette sayıdır
                bulunan = round(bulunan * satir["n"])
            if beklenen != bulunan:
                farklar.append(f"{taraf} {bolum}.{ad}: özet {beklenen}, eşleştirme {bulunan}")
    return farklar


def gruplar(kume: str) -> dict[str, str]:
    seti = json.loads(SORU_SETI.read_text(encoding="utf-8"))
    g = {}
    for s in seti["sorular"]:
        if s["set"] != kume:
            continue
        if s["bolum"] == "arac":
            g[s["soru_id"]] = "egitimde_yok" if s["sablon"] in HARIC_NIYETLER else "egitimde_var"
        elif s["bolum"] == "yetkisiz":
            g[s["soru_id"]] = s["tur"]
    return g


def yaz_tablo(sonuc: dict) -> None:
    print(f"\nHedefler ({sonuc['set']}, {sonuc['temel']} ↔ {sonuc['ayarli']})")
    print(f"  {'hedef':26} {'eşik':>8} {'temel':>8}    {'ayarlı':>8}")
    for h in sonuc["hedefler"]:
        isaret = "≤" if h["yon"] == "en_cok" else "≥"
        print(f"  {h['ad']:26} {isaret}{h['esik']:>7} {h['temel']:>8} {'✓' if h['temel_gecti'] else '✗'}  "
              f"{h['ayarli']:>8} {'✓' if h['ayarli_gecti'] else '✗'}")
    print("\nSoru soru (iyileşen / kötüleşen, p)")
    for bolum, kayit in sonuc["eslestirme"]["bolum"].items():
        print(f"  {bolum} (n={kayit['n']})")
        for ad, m in kayit["metrikler"].items():
            print(f"    {ad:24} {m['temel']!s:>7} → {m['ayarli']!s:<7}  +{m['iyilesen']} / -{m['kotulesen']}  "
                  f"p={m['p']}")
        for g, gk in kayit["gruplar"].items():
            ana = "cevap_dogrulugu" if bolum == "arac" else "reddetti"
            m = gk["metrikler"].get(ana)
            if m:
                print(f"      · {g:22} n={gk['n']:<3} {ana}: {m['temel']} → {m['ayarli']}  "
                      f"+{m['iyilesen']} / -{m['kotulesen']}  p={m['p']}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Faz 8: temel ↔ ince ayarlı model eşleştirmesi")
    ap.add_argument("--set", required=True, choices=("gelistirme", "saklanan"))
    ap.add_argument("--temel", default="faz7")
    ap.add_argument("--ayarli", default="faz8_ayarli")
    ap.add_argument("--cikti", type=Path, default=None)
    arg = ap.parse_args()

    t, a = rapor(arg.temel, arg.set), rapor(arg.ayarli, arg.set)
    hatalar = denetle(t, a)
    if hatalar:
        raise SystemExit("karşılaştırılamaz:\n  " + "\n  ".join(hatalar))
    uyarilar = []
    if t["model_izi"]["ozet"] == a["model_izi"]["ozet"]:
        uyarilar.append("iki ölçümün model izi aynı (aynı model karşılaştırılıyor)")

    e = pairing.eslestir(t["sorular"], a["sorular"], gruplar(arg.set))
    farklar = ozetle_uyusuyor_mu(t, e["bolum"], "temel") + ozetle_uyusuyor_mu(a, e["bolum"], "ayarli")
    if farklar:
        raise SystemExit("eşleştirme oranları özetle uyuşmuyor:\n  " + "\n  ".join(farklar))

    sonuc = {
        "set": arg.set, "temel": arg.temel, "ayarli": arg.ayarli,
        "soru_seti_ozeti": t["soru_seti_ozeti"], "sistem_ozeti": t["sistem"]["ozet"],
        "model_izi": {"temel": t["model_izi"], "ayarli": a["model_izi"]},
        "egitimde_olmayan_sablonlar": list(HARIC_NIYETLER),
        "uyarilar": uyarilar,
        "hedefler": pairing.hedef_tablosu(t["hedefler"], a["hedefler"]),
        "durumlar": {"temel": t["ozet"]["genel"]["durumlar"], "ayarli": a["ozet"]["genel"]["durumlar"]},
        "sure": {"temel": t["ozet"]["sure"], "ayarli": a["ozet"]["sure"]},
        "eslestirme": e,
    }
    cikti = arg.cikti or KOK / "reports" / f"faz8_karsilastirma_{arg.set}.json"
    cikti.write_text(json.dumps(sonuc, ensure_ascii=False, indent=1), encoding="utf-8")
    yaz_tablo(sonuc)
    for u in uyarilar:
        print("uyarı:", u)
    print(f"\nyazıldı: {cikti.relative_to(KOK) if cikti.is_relative_to(KOK) else cikti}")


if __name__ == "__main__":
    main()
