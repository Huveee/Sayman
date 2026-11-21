"""Faz 8b yeni test setini üretir, sızıntıyı denetler ve dondurur (hiçbir model çalıştırılmadan önce).

Set: tests/faz8b_sorular.py (S, K, T, F, G grupları). Müşteri ve faturalar Faz 7'nin iki setinde kullanılmamış
olanlardan seçilir. Beklenen değerler Faz 7'deki gibi araç fonksiyonları doğrudan çalıştırılarak bulunur.

Denetimler (sonuçları dosyaya yazılır):
- Sızıntı: Faz 8 eğitim verisi (data/egitim/sorular.json) ile bu set arasında Faz 8'in denetimi (kalıp, soru, kimlik,
  ad, kur). İhlal varsa dosya yazılmaz.
- Faz 7'ye benzerlik: yeni kalıpların ve soruların Faz 7 setine en yakın benzerliği (bilgi için; Faz 7 setinde
  eğitilmedi, ama Faz 8b kuralları o sete bakılarak yazıldı).
- Müşteri ipucu: doğrulayıcının "soru belirli bir müşteriyi soruyor" kararı (musteri_sorusu_mu) bütün sorularda;
  müşteriye özel sorularda yakalama ve ötekilerde yanlış alarm. Sonuç ne çıktıysa yazılır; kalıplar buna göre
  değiştirilmez.

Çıktı:  reports/faz8b_soru_seti.json (sorular, hedefler, ön kayıt, denetimler ve özet)

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\faz8b_soru_uret.py
"""

from __future__ import annotations

import csv
import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))
sys.path.insert(0, str(KOK / "tests"))

import faz7_sorular as f7  # noqa: E402
import faz8b_sorular as y  # noqa: E402

from sayman.asistan.dogrulayici import musteri_sorusu_mu  # noqa: E402
from sayman.belge import kutuphane  # noqa: E402
from sayman.egitim.sizinti import Degerlendirme, denetle, iskelet, jaccard, normal, soru_benzerligi  # noqa: E402
from sayman.veri.saat import saat_oku  # noqa: E402

CIKTI = KOK / "reports" / "faz8b_soru_seti.json"
FAZ7 = KOK / "reports" / "faz7_soru_seti.json"
EGITIM = KOK / "data" / "egitim" / "sorular.json"


def sizinti(sorular: list[dict], varliklar: list[str], egitim: list[dict]) -> dict:
    kaliplar = [k for sb, _, _ in y.SABLONLAR for k in sb.kaliplar] + [k for k, _ in y.TUM_SIRKET]
    kurlar = {(a["argumanlar"]["para_birimi"], str(a["anahtarlar"][0]["deger"]))
              for s in sorular for a in s.get("alternatifler", []) if a["arac"] == "doviz_kuru"}
    adlar = set()
    for dosya in ("musteriler.csv", "tedarikciler.csv"):
        with open(KOK / "data" / "uretilen" / dosya, encoding="utf-8") as f:
            adlar |= {normal(r["ad"]) for r in csv.DictReader(f)}
    metinler = {p.metin for p in kutuphane.parcalar(kutuphane.oku(KOK / "data" / "belge" / "belgeler.jsonl"))}
    d = Degerlendirme(kaliplar=kaliplar, sorular=[s["soru"] for s in sorular], kimlikler=set(varliklar),
                      adlar=adlar, madde_metinleri=metinler, kurlar=kurlar)
    r = denetle(egitim, d)
    return {"egitim_sorusu": len(egitim), "en_yakin_kalip_benzerligi": round(r.en_yakin_kalip, 3),
            "en_yakin_soru_benzerligi": round(r.en_yakin_soru, 3),
            "ihlal_sayisi": {k: len(v) for k, v in r.ihlaller.items()}, "ihlaller": r.ihlaller, "temiz": r.temiz}


def faz7_benzerligi(sorular: list[dict], faz7: dict) -> dict:
    f7_kaliplar = [k for sb in f7.SABLONLAR for k in sb.kaliplar]
    f7_kaliplar += [k for ks in f7.YASAK_KALIPLARI.values() for k in ks] + list(f7.BASKA_MUSTERI_KALIPLARI)
    f7_kaliplar += [m for m, _, _ in f7.ROL_KALIPLARI + f7.YONLENDIRME_KALIPLARI]
    f7_iskelet = {iskelet(k): k for k in f7_kaliplar}
    kalip = []
    for k in [k for sb, _, _ in y.SABLONLAR for k in sb.kaliplar] + [k for k, _ in y.TUM_SIRKET]:
        i = iskelet(k)
        puan, en = max(((1.0 if i == di else jaccard(i, di)), dk) for di, dk in f7_iskelet.items())
        kalip.append({"kalip": k, "faz7": en, "benzerlik": round(puan, 3)})
    kalip.sort(key=lambda x: -x["benzerlik"])
    f7_sorular = [s["soru"] for s in faz7["sorular"]]
    soru = []
    for s in sorular:
        b, en = max((soru_benzerligi(s["soru"], f), f) for f in f7_sorular)
        soru.append({"soru_id": s["soru_id"], "soru": s["soru"], "faz7": en, "benzerlik": round(b, 3)})
    soru.sort(key=lambda x: -x["benzerlik"])
    return {"en_yakin_kaliplar": kalip[:8], "en_yakin_sorular": soru[:8],
            "kalip_0_6_ustu": sum(x["benzerlik"] >= 0.6 for x in kalip),
            "soru_0_8_ustu": sum(x["benzerlik"] >= 0.8 for x in soru)}


def ipucu(sorular: list[dict]) -> dict:
    musteri = [s for s in sorular if s.get("diger_musteri")]
    oteki = [s for s in sorular if not s.get("diger_musteri")]
    kacan = [s["soru_id"] for s in musteri if not musteri_sorusu_mu(s["soru"])]
    alarm = [{"soru_id": s["soru_id"], "soru": s["soru"]} for s in oteki if musteri_sorusu_mu(s["soru"])]
    return {"musteri_sorusu": len(musteri), "yakalanan": len(musteri) - len(kacan), "kacan": kacan,
            "oteki_soru": len(oteki), "yanlis_alarm": alarm,
            "not": "Kalıpların çoğu doğrulayıcının ipucunu (\"müşterisi\", \"firması\", şirket türü) içerir; bu yüzden "
                   "yakalama oranı kuralın bağımsız bir sınaması değildir, yalnızca bu setteki kapsamını gösterir."}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    saat = saat_oku(KOK / "data" / "saat.json")
    faz7 = json.loads(FAZ7.read_text(encoding="utf-8"))
    egitim = json.loads(EGITIM.read_text(encoding="utf-8"))["sorular"]
    haric = set(faz7["varliklar"]["gelistirme"]) | set(faz7["varliklar"]["saklanan"])
    with tempfile.TemporaryDirectory() as gecici:
        o = y.YeniOrnekleyici(KOK / "data" / "ambar" / "sayman.duckdb", saat.simdi, Path(gecici), haric)
        try:
            sorular = y.soru_seti(o, {s["niyet"] for s in egitim})
            varliklar = sorted(o.varliklar[y.YENI])
            atlanan = o.tum_sirket_atlanan
        finally:
            o.kapat()
    kimlikler = [s["soru_id"] for s in sorular]
    assert len(kimlikler) == len(set(kimlikler)), "soru kimlikleri tekil olmalı"
    assert not set(varliklar) & haric, "Faz 7 varlıkları yeni sette olmamalı"
    sz = sizinti(sorular, varliklar, egitim)
    print("sızıntı:", json.dumps({k: v for k, v in sz.items() if k != "ihlaller"}, ensure_ascii=False))
    if not sz["temiz"]:
        print(json.dumps(sz["ihlaller"], ensure_ascii=False, indent=1))
        raise SystemExit("sızıntı var; set yazılmadı")
    veri = {
        "aciklama": "Faz 8b yeni test seti. Hiçbir model (kâhin dahil) çalıştırılmadan önce üretildi ve donduruldu; "
                    "hedefler ve ön kayıt aynı anda yazıldı. Üretim: scripts/faz8b_soru_uret.py, şablonlar: "
                    "tests/faz8b_sorular.py.",
        "yazilma_tarihi": y.YAZILMA_TARIHI, "simulasyon_gunu": saat.simdi.isoformat(),
        "ozet": f7.ozet(sorular),
        "sayilar": {"grup": dict(sorted(Counter(s["grup"] for s in sorular).items())),
                    "grup_rol": {f"{g}/{r}": n for (g, r), n in sorted(Counter((s["grup"], s["rol"])
                                                                              for s in sorular).items())},
                    "egitimde": dict(Counter((s["grup"] + "/" + s["egitimde"]) for s in sorular)),
                    "niyet": dict(Counter(s["niyet"] for s in sorular))},
        "sablonlar": {sb.ad: {"grup": g, "niyet": n, "adet": sb.adet, "kaliplar": list(sb.kaliplar)}
                      for sb, g, n in y.SABLONLAR},
        "tum_sirket_kaliplari": [k for k, _ in y.TUM_SIRKET], "tum_sirket_atlanan": atlanan,
        "hedefler": y.HEDEFLER, "hata_turleri": f7.HATA_TURLERI, "on_kayit": y.ON_KAYIT,
        "denetimler": {"sizinti": sz, "faz7_benzerligi": faz7_benzerligi(sorular, faz7), "musteri_ipucu": ipucu(sorular)},
        "varliklar": {y.YENI: varliklar},
        "sorular": sorular,
    }
    CIKTI.write_text(json.dumps(veri, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(veri["sayilar"], ensure_ascii=False))
    d = veri["denetimler"]
    print("Faz 7 benzerliği:", {k: v for k, v in d["faz7_benzerligi"].items() if not k.startswith("en_yakin")},
          d["faz7_benzerligi"]["en_yakin_kaliplar"][:3], d["faz7_benzerligi"]["en_yakin_sorular"][:3])
    print("ipucu:", {k: v for k, v in d["musteri_ipucu"].items() if k != "not"})
    print("özet", veri["ozet"])


if __name__ == "__main__":
    main()
