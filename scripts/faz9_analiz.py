"""Faz 9 analizi: elle araçlar ↔ otomatik araçlar (ön kayıt README "Faz 9" ve reports/faz9_on_kayit.json).

Girdiler: reports/faz9_el_yeni.jsonl, reports/faz9_oto_yeni.jsonl (varsa üçüncü kol reports/faz9_duzeltme_yeni.jsonl),
dondurulmuş sınıflar reports/faz9_siniflar.json ve Faz 8b soru seti. Her iki kol aynı puanlayıcıdan geçer
(scripts/faz7_olc.puanla_hepsi). Otomatik zarfta "kaynak" boş olduğu için otomatik satırlarda kapsam denetiminden önce
kimlikler sonucun kendisinden toplanır (otomatik.kaynak_kimlikleri); bir negatif kontrol satırı yakalanmalıdır.

Birincil ölçü: doğrudan sınıflardaki (tek_cagri, zincir) sorularda gösterilen doğru, eşli; kesin McNemar (iki yönlü).

Çıktı: reports/faz9_karsilastirma.json

    .venv\\Scripts\\python -I scripts\\faz9_analiz.py
"""

from __future__ import annotations

import copy
import json
import statistics
import sys
from collections import Counter
from math import comb
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))
sys.path.insert(0, str(KOK / "scripts"))

import faz7_olc  # noqa: E402

from sayman.deney.otomatik import kaynak_kimlikleri  # noqa: E402

SORU_SETI = KOK / "reports" / "faz8b_soru_seti.json"
SINIFLAR = KOK / "reports" / "faz9_siniflar.json"
KOLLAR = {"el": "faz9_el_yeni.jsonl", "otomatik": "faz9_oto_yeni.jsonl", "duzeltme": "faz9_duzeltme_yeni.jsonl"}
DOGRUDAN = ("tek_cagri", "zincir")
TAVAN = {"el": "85/86", "otomatik": "37/86"}
# Koşudan sonra elle okundu: otomatik kol doğru fatura numarasını buldu ama cevabı "…497351'dir (fatura ID: …)" diye
# sürdü; doğrulayıcının sayı ayrıştırıcısı numaranın ardındaki "fatura" kelimesini birim sanıp sayıyı "adet" yaptı,
# kaynaktaki metin numara ("tutar" türü) ile eşleşmedi. İki kolda da aynı doğrulayıcı; kör nokta ifadeye bağlı.
KOR_NOKTA = ["Y-fat_son-01", "Y-fat_son-03"]


def mcnemar_kesin(b: int, c: int) -> float:
    """İki yönlü kesin McNemar: uyuşmayan b + c çiftte b'nin binom(n, 1/2) dağılımındaki iki kuyruk olasılığı."""
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(comb(n, k) for k in range(min(b, c) + 1)) / 2 ** n)


def oto_kaynak_ekle(satir: dict) -> dict:
    """Otomatik satırın başarılı çağrılarına sonuçtaki kimlikleri "kaynak" olarak ekler (yalnızca puanlama için)."""
    s = copy.deepcopy(satir)
    for c in s.get("cagrilar", []):
        if not c.get("hata") and isinstance(c.get("yanit"), dict):
            c["yanit"]["kaynak"] = kaynak_kimlikleri(c["yanit"].get("sonuc"))
    return s


def _medyan(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 1) if xs else None


def _p90(xs):
    xs = sorted(x for x in xs if x is not None)
    return round(xs[min(len(xs) - 1, int(0.9 * len(xs)))], 1) if xs else None


VARSAYILAN_SAYFA = 25  # sahte API'nin varsayılan sayfa boyutu


def soru_ozeti(p: dict, satir: dict) -> dict:
    istem = [t.get("istem") for t in satir.get("tokenlar", []) if t.get("istem") is not None]
    boyutlar = [c.get("argumanlar", {}).get("sayfa_boyutu") for c in satir.get("cagrilar", [])]
    return {"dogru": p["cevap_dogru"], "gosterilen_yanlis": p["yanlis_sayi_suphesi"], "durum": satir["durum"],
            "hata_turu": p["hata_turu"], "sure_s": satir.get("sure_s"), "istem_en_cok": max(istem, default=None),
            "arac_cagrisi": len(satir.get("cagrilar", [])), "kapsam_disi": p["kapsam_disi"],
            "durdurulan_dogru_taslak": p.get("taslak_tablosu", {}).get("reddedildi_dogru", 0),
            "kapsam_notu": satir.get("kapsam_notu", False),
            "buyuk_sayfa": any(isinstance(b, int) and b > VARSAYILAN_SAYFA for b in boyutlar)}


def kume_ozeti(sorular: list[dict]) -> dict:
    n = len(sorular)
    return {"n": n, "dogru": sum(s["dogru"] for s in sorular),
            "dogru_orani": round(sum(s["dogru"] for s in sorular) / n, 4) if n else None,
            "gosterilen_yanlis": sum(s["gosterilen_yanlis"] for s in sorular),
            "durumlar": dict(Counter(s["durum"] for s in sorular)),
            "baglam_siniri": sum(s["durum"] == "baglam_siniri" for s in sorular),
            "sure_medyan_s": _medyan(s["sure_s"] for s in sorular), "sure_p90_s": _p90(s["sure_s"] for s in sorular),
            "istem_medyan": _medyan(s["istem_en_cok"] for s in sorular),
            "istem_en_cok": max((s["istem_en_cok"] or 0 for s in sorular), default=None),
            "arac_cagrisi_medyan": _medyan(s["arac_cagrisi"] for s in sorular),
            "durdurulan_dogru_taslak": sum(s["durdurulan_dogru_taslak"] for s in sorular),
            "kapsam_disi_satir": sum(bool(s["kapsam_disi"]) for s in sorular)}


def kol_puanla(kol: str, sorular: list[dict], satirlar: list[dict], kumeler: dict, bu_yil: int) -> dict[str, dict]:
    if kol == "otomatik":
        satirlar = [oto_kaynak_ekle(s) for s in satirlar]
    puanlar, _ = faz7_olc.puanla_hepsi(sorular, satirlar, kumeler, {}, bu_yil)
    satir = {s["soru_id"]: s for s in satirlar}
    return {p["soru_id"]: soru_ozeti(p, satir[p["soru_id"]]) for p in puanlar}


def negatif_kontrol(sorular: list[dict], kumeler: dict, bu_yil: int) -> list[str]:
    """Satış rolündeki bir soruya kapsam dışı (CFO'nun gördüğü) kimlik taşıyan sahte otomatik satır: yakalanmalı."""
    s = next(x for x in sorular if x["rol"] == "satis_yurtici")
    yabanci = next(iter(kumeler["satis_ihracat"]["faturalar"]))
    satir = {"soru_id": s["soru_id"], "durum": "tamam", "cevap": "", "sure_s": 0, "tokenlar": [], "taslaklar": [],
             "cagrilar": [{"arac": "faturalar_api_v1_faturalar_get", "argumanlar": {}, "hata": None,
                           "yanit": {"sonuc": {"kayitlar": [{"fatura_id": yabanci}]}, "kaynak": {}}}]}
    return kol_puanla("otomatik", [s], [satir], kumeler, bu_yil)[s["soru_id"]]["kapsam_disi"]


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    veri = json.loads(SORU_SETI.read_text(encoding="utf-8"))
    siniflar = json.loads(SINIFLAR.read_text(encoding="utf-8"))
    sinif = {k["soru_id"]: k["sinif"] for k in siniflar["sorular"]}
    sorular = [s for s in veri["sorular"] if s["soru_id"] in sinif]
    bu_yil = int(veri["simulasyon_gunu"][:4])
    kumeler = faz7_olc.kapsam_kumeleri()

    kollar = {}
    for kol, dosya in KOLLAR.items():
        yol = KOK / "reports" / dosya
        if yol.exists():
            satirlar = faz7_olc.jsonl_oku(yol)
            kollar[kol] = {"dosya": dosya, "satir": len(satirlar), "satirlar": satirlar,
                           "sorular": kol_puanla(kol, sorular, satirlar, kumeler, bu_yil)}

    sonuc = {"aciklama": __doc__.split("\n\n")[0], "siniflar_ozeti": siniflar["ozet"],
             "sinif_sayilari": siniflar["sayilar"], "kahin_tavanlari": TAVAN,
             "negatif_kontrol_yakalanan": negatif_kontrol(sorular, kumeler, bu_yil), "kollar": {}}
    assert sonuc["negatif_kontrol_yakalanan"], "negatif kontrol yakalanmadı: kapsam denetimi otomatik kolda boş"
    for kol, k in kollar.items():
        q = k["sorular"]
        sonuc["kollar"][kol] = {
            "dosya": k["dosya"], "tamamlanan": f"{len(q)}/{len(sorular)}",
            "tum_set": kume_ozeti(list(q.values())),
            "dogrudan": kume_ozeti([v for i, v in q.items() if sinif[i] in DOGRUDAN]),
            "siniflar": {c: kume_ozeti([v for i, v in q.items() if sinif[i] == c]) for c in sorted(set(sinif.values()))},
            "sizinti": sorted(i for i, v in q.items() if v["kapsam_disi"]),
            # Bağlam sınırına takılan sorulardan kaçında model varsayılandan (25) büyük sayfa istedi?
            "baglam_siniri_buyuk_sayfa": sum(v["durum"] == "baglam_siniri" and v["buyuk_sayfa"] for v in q.values()),
            "sorular": {i: {"sinif": sinif[i], **{a: v[a] for a in ("dogru", "gosterilen_yanlis", "durum", "hata_turu")}}
                        for i, v in sorted(q.items())},
        }

    if "el" in kollar and "otomatik" in kollar:
        el, oto = kollar["el"]["sorular"], kollar["otomatik"]["sorular"]
        ortak = [i for i in el if i in oto and sinif[i] in DOGRUDAN]
        b = [i for i in ortak if el[i]["dogru"] and not oto[i]["dogru"]]
        c = [i for i in ortak if oto[i]["dogru"] and not el[i]["dogru"]]
        sonuc["birincil"] = {
            "n": len(ortak), "ikisi_dogru": sum(el[i]["dogru"] and oto[i]["dogru"] for i in ortak),
            "yalniz_el": b, "yalniz_otomatik": c,
            "ikisi_yanlis": sum(not el[i]["dogru"] and not oto[i]["dogru"] for i in ortak),
            "el_orani": round(sum(el[i]["dogru"] for i in ortak) / len(ortak), 4) if ortak else None,
            "otomatik_orani": round(sum(oto[i]["dogru"] for i in ortak) / len(ortak), 4) if ortak else None,
            "mcnemar_p": round(mcnemar_kesin(len(b), len(c)), 4),
            "karar": None,
        }
        p = sonuc["birincil"]
        p["karar"] = "fark var" if p["mcnemar_p"] < 0.05 else "bu örneklemde fark gösterilemedi"
        # Duyarlılık (ön kayıtta yoktu, koşudan sonra eklendi): uyuşmayan çiftlerden biri ya da ikisi dönseydi.
        p["duyarlilik"] = {
            "bir_cift_donerse_p": round(mcnemar_kesin(len(b) - 1, len(c)), 4),
            "dogrulayici_kor_noktasi_haric_p": round(mcnemar_kesin(len(b) - len(KOR_NOKTA), len(c)), 4),
            "dogrulayici_kor_noktasi": KOR_NOKTA,
        }
        assert all(i in b for i in KOR_NOKTA)
        sonuc["otomatik_toplama_ve_yok"] = {
            "gosterilen_dogru": sorted(i for i, v in oto.items() if sinif[i] in ("toplama", "yok") and v["dogru"]),
            "gosterilen_yanlis": sorted(i for i, v in oto.items()
                                        if sinif[i] in ("toplama", "yok") and v["gosterilen_yanlis"]),
            "durdurulan_dogru_taslak": {i: v["durdurulan_dogru_taslak"] for i, v in oto.items()
                                        if sinif[i] in ("toplama", "yok") and v["durdurulan_dogru_taslak"]},
        }
        sonuc["engelli"] = {i: {"el": el[i]["dogru"], "otomatik": oto[i]["dogru"], "otomatik_durum": oto[i]["durum"]}
                            for i in el if sinif[i] == "engelli" and i in oto}
    if "el" in kollar and "duzeltme" in kollar:
        el, dz = kollar["el"]["sorular"], kollar["duzeltme"]["sorular"]
        ortak = [i for i in el if i in dz]
        sonuc["ucuncu_kol"] = {
            "not": "Keşif amaçlı: düzeltmeler Faz 8b sonuçlarına bakılarak seçildi; aynı 86 soru. Yalnızca betimleyici.",
            "yalniz_el": sorted(i for i in ortak if el[i]["dogru"] and not dz[i]["dogru"]),
            "yalniz_duzeltme": sorted(i for i in ortak if dz[i]["dogru"] and not el[i]["dogru"]),
            "yama": "reports/faz9_ucuncu_kol_yama.diff",
            # Düzeltmeler hangi sorularda gerçekten devreye girdi? (Geri kalan farklar düzeltmeye bağlanamaz.)
            "musteri_adi_ile_cek_portfoyu": sorted(
                s["soru_id"] for s in kollar["duzeltme"]["satirlar"]
                if any(c["arac"] == "cek_portfoyu" and c.get("argumanlar", {}).get("musteri_adi") for c in s["cagrilar"])),
            "yeni_en_fazla_mesaji": sorted(
                s["soru_id"] for s in kollar["duzeltme"]["satirlar"]
                if any("ile yeniden deneyin" in json.dumps(c, ensure_ascii=False) for c in s["cagrilar"])),
        }

    (KOK / "reports" / "faz9_karsilastirma.json").write_text(json.dumps(sonuc, ensure_ascii=False, indent=1) + "\n",
                                                            encoding="utf-8")
    for kol, k in sonuc["kollar"].items():
        t, d = k["tum_set"], k["dogrudan"]
        print(f"{kol:9s} tüm: {t['dogru']}/{t['n']} yanlış {t['gosterilen_yanlis']} bağlam {t['baglam_siniri']} "
              f"süre medyan {t['sure_medyan_s']} p90 {t['sure_p90_s']} istem medyan {t['istem_medyan']} "
              f"en çok {t['istem_en_cok']} | doğrudan: {d['dogru']}/{d['n']} yanlış {d['gosterilen_yanlis']} "
              f"| sızıntı {len(k['sizinti'])}")
    if "birincil" in sonuc:
        print("birincil", json.dumps(sonuc["birincil"], ensure_ascii=False))
        print("otomatik toplama/yok", json.dumps(sonuc["otomatik_toplama_ve_yok"], ensure_ascii=False))
    if "ucuncu_kol" in sonuc:
        print("üçüncü kol", json.dumps(sonuc["ucuncu_kol"], ensure_ascii=False))
    print("negatif kontrol", sonuc["negatif_kontrol_yakalanan"])


if __name__ == "__main__":
    main()
