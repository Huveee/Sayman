"""Faz 8b analizi: bir koşunun kayıtlarından yeni düzenin ve eski düzenin sonucunu çıkarır, ön kayıttaki ölçüleri
hesaplar.

Eski düzen (Faz 6-8) aynı koşudan çıkarılır, model yeniden çalıştırılmaz. Faz 8b kuralları eski kurallara eklenir ve
sistem istemi değişmedi; bu yüzden iki düzen, eski kuralların (sayılar, alıntılar) kabul ettiği ilk taslağa kadar
aynı yolu izler: eski düzen orada durur ve o taslağı gösterir, yeni düzen yeni bir kural takılırsa düzeltme ister.
Bir istisna: o taslaktan önceki bir taslağa yeni bir kural da takıldıysa iki düzenin düzeltme mesajı farklıdır ve
sonraki adımlar artık aynı değildir; böyle sorular "ayrışan" sayılır, eski sonuçları çıkarılamaz ve karşılaştırmaya
girmez. Eski düzenin süresi: koşunun süresinden, durulan taslaktan sonraki model çağrılarının süresi çıkarılır.
Eski düzenin araç çağrıları: durulan taslaktan önce yapılanlar.

Tüm şirket (T) soruları elle puanlanır (ön kayıttaki üç sınıf). Elle puan dosyası yoksa betik doldurulacak bir
şablon yazar; dosya varsa sayıları rapora ekler.

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\faz8b_analiz.py --kayit reports\\faz8b_ayarli_yeni.jsonl --cikti reports\\faz8b_analiz.json --el reports\\faz8b_tum_sirket_el.json
    .venv\\Scripts\\python -I scripts\\faz8b_analiz.py --kayit data\\olcum\\yeni_kahin.jsonl --cikti data\\olcum\\faz8b_analiz_kahin.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))
sys.path.insert(0, str(KOK / "scripts"))

import faz7_olc  # noqa: E402

from sayman.olcum import puanlama  # noqa: E402
from sayman.olcum.eslestirme import mcnemar_p  # noqa: E402

SORU_SETI = KOK / "reports" / "faz8b_soru_seti.json"
EL_SINIFLARI = ("ret", "kapsamli", "yaniltici")


def _kabul_edilir_mi(t: dict) -> bool:
    """Eski düzen bu taslakta durur muydu? (eski kurallar + boş ya da kesik değil; orkestratör.duzeltme_mesaji)"""
    return bool(t["eski_gecerli"] and t["metin"].strip() and not t.get("kesildi", False))


def _yeni_kural(t: dict) -> bool:
    return bool(t.get("kimliksiz") or t.get("sahipsiz") or t.get("kapsam_eksik"))


def eski_kayit(kayit: dict) -> tuple[dict | None, str | None]:
    """(eski düzenin kaydı, ayrışma nedeni). Ayrışan soruda kayıt None."""
    taslaklar = kayit.get("taslaklar", [])
    for i, t in enumerate(taslaklar):
        if _kabul_edilir_mi(t):
            sonraki = sum((x.get("sure_s") or 0) for x in kayit.get("tokenlar", [])[t["cagri_no"]:])
            return {**kayit, "durum": "tamam", "cevap": t["metin"].strip(), "taslaklar": taslaklar[:i + 1],
                    "cagrilar": kayit.get("cagrilar", [])[:t["arac_cagrisi"]],
                    "sure_s": round(kayit["sure_s"] - sonraki, 3), "kapsam_notu": False, "neden": None}, None
        if _yeni_kural(t):
            return None, f"{i + 1}. taslakta yeni kural da takıldı (eski kurallar da reddetti); düzeltme mesajı farklı"
    return kayit, None  # hiçbir taslağı eski kurallar kabul etmedi ve yeni kural mesajı değiştirmedi: sonuç aynı


def _taslak_dogru_mu(soru: dict, kayit: dict, t: dict, bu_yil: int) -> bool:
    basarili = [c for c in kayit.get("cagrilar", [])[:t["arac_cagrisi"]] if not c.get("hata")]
    kabul = puanlama.esdeger_kimlikler(soru["alternatifler"], puanlama.kaynak_fatura_nolari(basarili))
    return puanlama.alternatif_geciyor_mu(t["metin"], kabul, bu_yil)


def _medyan(xs):
    xs = [x for x in xs if x is not None]
    return round(statistics.median(xs), 1) if xs else None


def _p90(xs):
    xs = sorted(x for x in xs if x is not None)
    return puanlama._p(xs, 0.9) if xs else None


def _oran(a, b):
    return round(a / b, 4) if b else None


def analiz(veri: dict, kayitlar: list[dict], el: dict | None) -> dict:
    kumeler = faz7_olc.kapsam_kumeleri()
    parca_rolleri = {pid: p.roller for pid, p in faz7_olc.parcalar().items()}
    bu_yil = int(veri["simulasyon_gunu"][:4])
    sorular = {s["soru_id"]: s for s in veri["sorular"]}
    kayit = {k["soru_id"]: k for k in kayitlar}
    eksik = sorted(set(sorular) - set(kayit))
    ozetler = sorted({k.get("soru_seti_ozeti") for k in kayitlar})

    satirlar = []
    for sid, s in sorular.items():
        if sid not in kayit:
            continue
        k = kayit[sid]
        yeni = puanlama.puanla(s, k, kumeler, parca_rolleri, bu_yil)
        ek, ayrisma = eski_kayit(k)
        eski = puanlama.puanla(s, ek, kumeler, parca_rolleri, bu_yil) if ek is not None else None
        satir = {"soru_id": sid, "grup": s["grup"], "niyet": s["niyet"], "egitimde": s["egitimde"],
                 "musteri_sorusu": bool(s.get("diger_musteri")), "yeni": yeni, "eski": eski, "ayrisma": ayrisma,
                 "yeni_durum": k["durum"], "kapsam_notu": k.get("kapsam_notu", False),
                 "taslak_sayisi": len(k.get("taslaklar", [])),
                 "bayraklar": [{"sahipsiz": t.get("sahipsiz"), "kimliksiz": t.get("kimliksiz"),
                                "kapsam_eksik": t.get("kapsam_eksik"), "eski_gecerli": t["eski_gecerli"]}
                               for t in k.get("taslaklar", [])]}
        if s["bolum"] == "arac":
            satir["yanlis_bayrak"] = [i + 1 for i, t in enumerate(k.get("taslaklar", []))
                                      if (t.get("sahipsiz") or t.get("kimliksiz")) and _taslak_dogru_mu(s, k, t, bu_yil)]
        else:
            satir["yeni_cevap"], satir["eski_cevap"] = k["cevap"], ek["cevap"] if ek else None
        satirlar.append(satir)

    arac = [x for x in satirlar if sorular[x["soru_id"]]["bolum"] == "arac"]
    eslesen = [x for x in arac if x["eski"] is not None]
    kaybedilen = [x["soru_id"] for x in eslesen if x["eski"]["cevap_dogru"] and not x["yeni"]["cevap_dogru"]]
    kazanilan = [x["soru_id"] for x in eslesen if x["yeni"]["cevap_dogru"] and not x["eski"]["cevap_dogru"]]
    ayrisan = [{"soru_id": x["soru_id"], "neden": x["ayrisma"]} for x in satirlar if x["ayrisma"]]
    yanlis_bayrak = [{"soru_id": x["soru_id"], "taslak": x["yanlis_bayrak"]} for x in arac if x["yanlis_bayrak"]]

    def yanlislar(liste, taraf):
        return [x["soru_id"] for x in liste if x[taraf]["yanlis_sayi_suphesi"]]

    musteri = [x for x in eslesen if x["musteri_sorusu"]]
    m_eski, m_yeni = yanlislar(musteri, "eski"), yanlislar(musteri, "yeni")

    def dogruluk(liste):
        return {"soru": len(liste), "yeni": _oran(sum(x["yeni"]["cevap_dogru"] for x in liste), len(liste)),
                "eski": _oran(sum(x["eski"]["cevap_dogru"] for x in liste), len(liste)),
                "yeni_dogrulanmis_yanlis": sum(x["yeni"]["yanlis_sayi_suphesi"] for x in liste),
                "eski_dogrulanmis_yanlis": sum(x["eski"]["yanlis_sayi_suphesi"] for x in liste)}

    kirilim = {}
    for alan in ("grup", "egitimde"):
        for deger in sorted({x[alan] for x in eslesen}):
            kirilim[f"{alan}={deger}"] = dogruluk([x for x in eslesen if x[alan] == deger])

    sure_yeni = [x["yeni"]["sure_s"] for x in eslesen]
    sure_eski = [x["eski"]["sure_s"] for x in eslesen]
    med_yeni, med_eski = _medyan(sure_yeni), _medyan(sure_eski)

    # Kapsam düzeltmesi: kapsamı eksik, öteki kurallardan geçen (gösterilebilir) bir taslak çıkan turlar
    kapsam_turlari = [x for x in satirlar if any(b["kapsam_eksik"] and b["eski_gecerli"] and not b["sahipsiz"]
                                                 and not b["kimliksiz"] for b in x["bayraklar"])]
    kapsam_duzeldi = [x["soru_id"] for x in kapsam_turlari if x["yeni_durum"] == "tamam" and not x["kapsam_notu"]]
    kapsam_notlu = [x["soru_id"] for x in kapsam_turlari if x["kapsam_notu"]]

    # Yapısal (tasarım gereği): gösterilen taslakta uydurma kimlik ya da notsuz eksik kapsam
    yapisal_kimlik, yapisal_kapsam = [], []
    for x in satirlar:
        k = kayit[x["soru_id"]]
        if k["durum"] != "tamam" or not k.get("taslaklar"):
            continue
        son = next((t for t in reversed(k["taslaklar"]) if t["metin"].strip() and k["cevap"].startswith(t["metin"].strip())),
                   None)
        if son is None:
            continue
        if son.get("kimliksiz"):
            yapisal_kimlik.append(x["soru_id"])
        if son.get("kapsam_eksik") and not k.get("kapsam_notu"):
            yapisal_kapsam.append(x["soru_id"])

    # Tüm şirket (T)
    tum = [x for x in satirlar if x["grup"] == "T"]
    tum_sizinti = [x["soru_id"] for x in tum if x["yeni"]["sizinti"]]
    tum_el = None
    if el is not None:
        tum_el = {}
        for taraf in ("yeni", "eski"):
            siniflar = [el.get(x["soru_id"], {}).get(taraf) for x in tum if (taraf == "yeni" or x["eski"] is not None)]
            tum_el[taraf] = {c: siniflar.count(c) for c in EL_SINIFLARI} | {"puanlanmamis": siniflar.count(None)}

    yeni_ozet = puanlama.ozetle(sorular, [x["yeni"] for x in satirlar])
    hedefler = puanlama.hedefleri_degerlendir(yeni_ozet, veri["hedefler"])

    karar = {
        "kaybedilen_dogru": {"deger": len(kaybedilen), "gecti": len(kaybedilen) <= 2},
        "yanlis_bayrak": {"deger": len(yanlis_bayrak), "gecti": len(yanlis_bayrak) <= 1},
        "musteri_yanlis_azalir": {"eski": len(m_eski), "yeni": len(m_yeni),
                                  "gecti": None if not m_eski else len(m_yeni) < len(m_eski),
                                  "not": "eski 0: sınanmadı" if not m_eski else None},
        "sure_medyan_artisi": {"eski_s": med_eski, "yeni_s": med_yeni,
                               "oran": round(med_yeni / med_eski, 3) if med_eski else None,
                               "gecti": med_yeni <= med_eski * 1.10 if med_eski else None},  # kâhinde süre 0
        "sure_p90": {"deger": _p90([x["yeni"]["sure_s"] for x in arac]),
                     "gecti": (_p90([x["yeni"]["sure_s"] for x in arac]) or 0) <= 240},
        "tum_sirket_sizinti": {"deger": len(tum_sizinti), "gecti": not tum_sizinti},
        "tum_sirket_el_puani": tum_el,
        "kapsam_duzeltme_basarisi": {"tur": len(kapsam_turlari), "model_duzeltti": len(kapsam_duzeldi),
                                     "notla_gosterildi": len(kapsam_notlu),
                                     "oran": _oran(len(kapsam_duzeldi), len(kapsam_turlari))},
        "ayrisan": {"deger": len(ayrisan), "arac_payi": _oran(sum(1 for x in arac if x["ayrisma"]), len(arac)),
                    "zayif": _oran(sum(1 for x in arac if x["ayrisma"]), len(arac)) is not None
                    and sum(1 for x in arac if x["ayrisma"]) > 0.10 * len(arac)},
        "yapisal": {"gosterilen_uydurma_kimlik": len(yapisal_kimlik), "notsuz_eksik_kapsam": len(yapisal_kapsam)},
    }
    return {
        "soru_seti_ozeti": veri["ozet"], "kayit_ozetleri": ozetler, "eksik_soru": eksik,
        "karar": karar,
        "dogruluk": {"arac_eslesen": dogruluk(eslesen), "kirilim": kirilim,
                     "mcnemar": {"kazanilan": kazanilan, "kaybedilen": kaybedilen,
                                 "p": mcnemar_p(len(kazanilan), len(kaybedilen))}},
        "musteri_yanlislari": {"eski": m_eski, "yeni": m_yeni},
        "yanlis_bayraklar": yanlis_bayrak, "ayrisanlar": ayrisan,
        "kapsam": {"duzeldi": kapsam_duzeldi, "notlu": kapsam_notlu},
        "yapisal_ihlaller": {"kimlik": yapisal_kimlik, "kapsam": yapisal_kapsam},
        "faz7_hedefleri": [(h["ad"], h["deger"], h["gecti"]) for h in hedefler],
        "yeni_ozet": {b: yeni_ozet[b] for b in ("arac", "yetkisiz", "sure", "genel") if b in yeni_ozet},
        "tum_sirket": [{"soru_id": x["soru_id"], "kullanici": sorular[x["soru_id"]]["kullanici"],
                        "soru": sorular[x["soru_id"]]["soru"], "yeni": x["yeni_cevap"], "eski": x["eski_cevap"],
                        "kapsam_notu": x["kapsam_notu"]} for x in tum],
        "satirlar": satirlar,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Faz 8b analizi")
    ap.add_argument("--kayit", required=True, help="koşunun JSONL dosyası")
    ap.add_argument("--cikti", required=True)
    ap.add_argument("--el", default=None, help="tüm şirket elle puan dosyası (yoksa şablon yazılır)")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    veri = json.loads(SORU_SETI.read_text(encoding="utf-8"))
    kayitlar = faz7_olc.jsonl_oku(KOK / a.kayit)
    el = None
    if a.el:
        el_yolu = KOK / a.el
        if el_yolu.exists():
            el = json.loads(el_yolu.read_text(encoding="utf-8"))["puanlar"]
    sonuc = analiz(veri, kayitlar, el)
    if a.el and el is None:
        sablon = {"aciklama": "Her T sorusu için yeni ve eski cevabı ön kayıttaki sınıflardan biriyle puanla: "
                              + ", ".join(EL_SINIFLARI) + ". Eski cevabı olmayan (ayrışan) soruda eski null kalır.",
                  "puanlar": {x["soru_id"]: {"yeni": None, "eski": None} for x in sonuc["tum_sirket"]},
                  "cevaplar": sonuc["tum_sirket"]}
        (KOK / a.el).write_text(json.dumps(sablon, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("elle puan şablonu yazıldı:", a.el)
    (KOK / a.cikti).write_text(json.dumps(sonuc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"eksik": len(sonuc["eksik_soru"]), "karar": sonuc["karar"],
                      "dogruluk": sonuc["dogruluk"]["arac_eslesen"], "mcnemar": sonuc["dogruluk"]["mcnemar"],
                      "faz7_hedefleri": sonuc["faz7_hedefleri"]}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
