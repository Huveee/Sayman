"""Faz 8 eğitim verisi: sorular → sızıntı denetimi → öğretmen izleri → Ollama'nın çizdiği istemler.

Adımlar (sırayla; her biri bir öncekinin çıktısını okur):
    sorular  eğitim sorularını üretir (tests/faz8_egitim_sorular.py) ve sızıntıyı denetler; ihlal varsa yazmaz.
             Çıktı: data/egitim/sorular.json, reports/faz8_sizinti.json
    gomme    belge parçalarının ve belge sorularının vektörlerini önbelleğe alır (Ollama'da bge-m3; Ollama'yı kullanan
             başka bir ölçüm çalışırken çalıştırmayın).
    izler    öğretmeni gerçek orkestratör, kapı, doğrulayıcı ve gerçek belge arayıcısıyla (eğitim kütüphanesi) çalıştırır;
             her tur Faz 7 puanlayıcısıyla puanlanır. Yalnızca düzeltme turu olmadan geçen ve puanlayıcıya göre doğru olan
             turlar tutulur. Çıktı: data/egitim/izler.jsonl
    ciz      her model çağrısını Ollama'ya çizdirir (istem) ve öğretmenin cevabını tamamlama metnine çevirir.
             Çıktı: data/egitim/veri/egitim.jsonl, data/egitim/veri/dogrulama.jsonl, reports/faz8_egitim_verisi.json

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\faz8_egitim_veri.py sorular
    .venv\\Scripts\\python -I scripts\\faz8_egitim_veri.py gomme
    .venv\\Scripts\\python -I scripts\\faz8_egitim_veri.py izler
    .venv\\Scripts\\python -I scripts\\faz8_egitim_veri.py ciz
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import secrets
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))
sys.path.insert(0, str(KOK / "tests"))

SIRKET = KOK / "data" / "egitim" / "sirket"
AMBAR = SIRKET / "ambar" / "sayman.duckdb"
BELGE = SIRKET / "belge"
SORULAR = KOK / "data" / "egitim" / "sorular.json"
IZLER = KOK / "data" / "egitim" / "izler.jsonl"
VERI = KOK / "data" / "egitim" / "veri"
CALISMA = KOK / "data" / "egitim" / "calisma"


def _json(x):
    if isinstance(x, (dt.date,)):
        return x.isoformat()
    raise TypeError(type(x))


def ozet(veri) -> str:
    return hashlib.sha256(json.dumps(veri, ensure_ascii=False, sort_keys=True, default=_json).encode()).hexdigest()


# ------------------------------------------------------------------ değerlendirme tarafı


def degerlendirme() -> "Degerlendirme":
    import support.phase05_questions as f5
    import support.phase07_questions as f7

    from sayman.documents import library
    from sayman.training.leakage import Degerlendirme, normal

    veri = json.loads((KOK / "reports" / "faz7_soru_seti.json").read_text(encoding="utf-8"))
    kaliplar = [k for sb in f7.SABLONLAR for k in sb.kaliplar]
    kaliplar += [k for ks in f7.YASAK_KALIPLARI.values() for k in ks] + list(f7.BASKA_MUSTERI_KALIPLARI)
    kaliplar += [m for m, _, _ in f7.ROL_KALIPLARI + f7.YONLENDIRME_KALIPLARI]
    kaliplar += [k for grup in f5.KALIPLAR.values() for cift in grup.values() for k in cift]
    kurlar = set()
    for s in veri["sorular"]:
        for a in s.get("alternatifler", []):
            if a["arac"] == "doviz_kuru":
                kurlar.add((a["argumanlar"]["para_birimi"], str(a["anahtarlar"][0]["deger"])))
    adlar = set()
    for dosya in ("musteriler.csv", "tedarikciler.csv"):
        with open(KOK / "data" / "uretilen" / dosya, encoding="utf-8") as f:
            adlar |= {normal(r["ad"]) for r in csv.DictReader(f)}
    metinler = {p.metin for p in library.parcalar(library.oku(KOK / "data" / "belge" / "belgeler.jsonl"))}
    return Degerlendirme(kaliplar=kaliplar, sorular=[s["soru"] for s in veri["sorular"]],
                         kimlikler=set(veri["varliklar"]["gelistirme"]) | set(veri["varliklar"]["saklanan"]),
                         adlar=adlar, madde_metinleri=metinler, kurlar=kurlar)


def haric_adlar() -> set[str]:
    from sayman.evaluation.value_formats import ad_normal
    adlar = set()
    for dosya in ("musteriler.csv", "tedarikciler.csv"):
        with open(KOK / "data" / "uretilen" / dosya, encoding="utf-8") as f:
            adlar |= {ad_normal(r["ad"]) for r in csv.DictReader(f)}
    return adlar


# ------------------------------------------------------------------ sorular


def sorular_adimi() -> None:
    import support.phase08_training_questions as e8

    from sayman.documents import library
    from sayman.training.leakage import denetle

    d = degerlendirme()
    adlar = haric_adlar()
    belgeler = library.oku(BELGE / "belgeler.jsonl")
    bas = time.time()
    CALISMA.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CALISMA) as gecici:
        orn = {g: e8.EgitimOrnekleyici(AMBAR, g, Path(gecici) / g.isoformat(), adlar, d.kimlikler)
               for g in e8.EGITIM_GUNLERI}
        try:
            arac = e8.arac_sorulari(orn, d.kurlar)
            yetkisiz = e8.yetkisiz_sorular(orn)
        finally:
            for o in orn.values():
                o.kapat()
    belge = e8.belge_sorulari(belgeler, d.madde_metinleri, adlar)
    belge_yetki = e8.belge_yetkisi_sorulari(belge, belgeler)
    sorular = e8.ayir(arac + yetkisiz + belge + belge_yetki)
    rapor = denetle(sorular, d)
    ortak = {
        "tasarim_geregi_ortak": ["UCI satış kalemleri (ürünler, tarihler)", "TCMB kur tablosu",
                                 "banka hesap kimlikleri (A_TRY, B_EUR, B_TRY, C_USD)", "kullanıcılar ve rolleri",
                                 "kanun metinleri (yalnızca arama sonucunda; eğitim sorusu hedefi olmaz)",
                                 "sistem istemi ve araç tanımları", "politika belge kimlikleri ve başlıkları",
                                 "Faz 7 örnekleyici fonksiyonları (niyet tanımları)"],
        "haric_niyetler": list(e8.HARIC_NIYETLER), "egitim_gunleri": [g.isoformat() for g in e8.EGITIM_GUNLERI],
    }
    sonuc = {"soru": len(sorular), "bolum": dict(Counter(s["bolum"] for s in sorular)),
             "ayrim": dict(Counter(s["ayrim"] for s in sorular)), "niyet": dict(Counter(s["niyet"] for s in sorular)),
             "en_yakin_kalip_benzerligi": round(rapor.en_yakin_kalip, 3),
             "en_yakin_soru_benzerligi": round(rapor.en_yakin_soru, 3),
             "ihlal_sayisi": {k: len(v) for k, v in rapor.ihlaller.items()}, "ihlaller": rapor.ihlaller,
             **ortak, "sure_s": round(time.time() - bas, 1)}
    (KOK / "reports" / "faz8_sizinti.json").write_text(json.dumps(sonuc, ensure_ascii=False, indent=1) + "\n",
                                                       encoding="utf-8")
    print(json.dumps({k: v for k, v in sonuc.items() if k not in ("ihlaller", "niyet")}, ensure_ascii=False, indent=1))
    if not rapor.temiz:
        for tur, liste in rapor.ihlaller.items():
            for x in liste[:8]:
                print("İHLAL", tur, json.dumps(x, ensure_ascii=False))
        raise SystemExit("sızıntı denetimi geçmedi; sorular yazılmadı")
    SORULAR.write_text(json.dumps({"ozet": ozet(sorular), "sorular": sorular}, ensure_ascii=False, indent=1,
                                  default=_json) + "\n", encoding="utf-8")
    print("yazıldı:", SORULAR.relative_to(KOK), ozet(sorular)[:16])


def sorulari_oku() -> list[dict]:
    veri = json.loads(SORULAR.read_text(encoding="utf-8"))
    if ozet(veri["sorular"]) != veri["ozet"]:
        raise SystemExit("sorular.json özeti tutmuyor")
    return veri["sorular"]


# ------------------------------------------------------------------ gömme


def gomme_adimi() -> None:
    sys.path.insert(0, str(KOK / "scripts"))
    import phase08_training_company as sirket

    from sayman.documents.semantic import GommeOnbellegi

    sirket.gomme_adimi()
    sorgular = [s["cagri"][1]["sorgu"] for s in sorulari_oku() if s.get("cagri") and s["cagri"][0] == "belge_ara"]
    sorgular += [s["ikinci_sorgu"] for s in sorulari_oku() if s.get("ikinci_sorgu")]
    o = GommeOnbellegi(sirket.ONBELLEK)
    print("sorgu vektörü:", o.doldur(sorgular), "yeni")


# ------------------------------------------------------------------ izler


def kapsam_kumeleri() -> dict:
    from sayman.warehouse.loader import baglan, satirlar
    con = baglan(AMBAR)
    try:
        kumeler = {}
        for rol, kosul in (("satis_yurtici", "m.yurtici"), ("satis_ihracat", "NOT m.yurtici")):
            m = f"SELECT musteri_id FROM musteriler m WHERE {kosul}"
            kume = {"doviz_kurlari": None, "musteriler": {r["musteri_id"] for r in satirlar(con, m)}}
            for tablo, kimlik in (("faturalar", "fatura_id"), ("tahsilatlar", "tahsilat_id"), ("cekler", "cek_id")):
                kume[tablo] = {r[kimlik] for r in satirlar(con, f"SELECT {kimlik} FROM {tablo} WHERE musteri_id IN ({m})")}
            kumeler[rol] = kume
        return kumeler
    finally:
        con.close()


def satir(tur) -> dict:
    """Faz 7 puanlayıcısının okuduğu biçim (scripts/faz7_olc.py _kayit ile aynı alanlar)."""
    return {"durum": tur.durum, "cevap": tur.cevap, "model_cagrisi": tur.model_cagrisi, "neden": tur.neden,
            "cagrilar": [{"arac": c["arac"], "argumanlar": c["argumanlar"], "hata": c["yanit"].get("hata"),
                          "yanit": c["yanit"]} for c in tur.cagrilar],
            "taslaklar": [{"metin": t, "gecerli": d.gecerli} for t, d in zip(tur.taslaklar, tur.dogrulamalar)]}


def tutulur_mu(s: dict, sat: dict, p: dict) -> tuple[bool, str]:
    if any(not t["gecerli"] for t in sat["taslaklar"]):
        return False, "duzeltme"
    if p.get("kapsam_disi"):
        return False, "kapsam_disi"
    if s["bolum"] == "arac":
        ok = sat["durum"] == "tamam" and p["cevap_dogru"] and p["arac_dogru"] and p["parametre_dogru"]
        return ok, "tamam" if ok else f"arac:{sat['durum']}:{p.get('hata_turu')}"
    if s["bolum"] == "belge":
        ok = sat["durum"] == "tamam" and p["cevap_dogru"] and p["dogru_parcadan_alinti"]
        return ok, "tamam" if ok else f"belge:{sat['durum']}:{p.get('hata_turu')}"
    if s.get("tur") == "salt_okunur":
        ok = sat["durum"] == "tamam" and not sat["cagrilar"]
        return ok, "tamam" if ok else f"salt:{sat['durum']}"
    beklenen = "ret" if s["tur"] in ("yasak_arac", "rol_iddiasi", "yonlendirme") else "tamam"
    ok = sat["durum"] == beklenen and not p.get("sizinti")
    return ok, "tamam" if ok else f"yetkisiz:{sat['durum']}:{sat.get('neden')}"


def izler_adimi(sinir: int = 0) -> None:
    from sayman.tools.audit_log import DenetimKaydi
    from sayman.tools.identity import kart_uret
    from sayman.tools.server import SaymanSunucusu
    from sayman.tools.permissions import yetki_oku
    from sayman.assistant.orchestrator import Asistan, Ayar
    from sayman.documents import library
    from sayman.documents.search import arayici_kur
    from sayman.training.prompt import KayitciModel
    from sayman.training.teacher import Ogretmen
    from sayman.evaluation import scoring
    from sayman.data.clock import Saat, saat_oku

    sorular = sorulari_oku()
    if sinir:
        sorular = sorular[:sinir]
    veri_bitis = saat_oku(SIRKET / "saat.json").veri_bitis
    saat = [Saat(dt.date.fromisoformat(sorular[0]["gun"]), veri_bitis)]
    anahtar = secrets.token_bytes(32)
    kumeler = kapsam_kumeleri()
    parca_rolleri = {p.parca_id: p.roller for p in library.parcalar(library.oku(BELGE / "belgeler.jsonl"))}
    arayici = arayici_kur(BELGE, "anlam")
    model = KayitciModel(Ogretmen())
    sayac, bas = Counter(), time.time()
    CALISMA.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=CALISMA) as gecici, open(IZLER, "w", encoding="utf-8", newline="\n") as f:
        sunucu = SaymanSunucusu(ambar=AMBAR, saat=lambda: saat[0], anahtar=anahtar, yetki=yetki_oku(),
                                kayit=DenetimKaydi(Path(gecici) / "denetim.jsonl"),
                                kapsam_klasoru=Path(gecici) / "kapsam", belge=arayici, belge_yontemi="anlam")
        try:
            asistan = Asistan(sunucu, model, Ayar())
            for i, s in enumerate(sorular, 1):
                saat[0] = Saat(dt.date.fromisoformat(s["gun"]), veri_bitis)
                model.soru = s
                tur = asistan.sor(s["soru"], kart_uret(s["kullanici"], anahtar, 900))
                cagrilar = model.bosalt()
                sat = satir(tur)
                p = scoring.puanla(s, sat, kumeler, parca_rolleri, int(s["gun"][:4]))
                ok, neden = tutulur_mu(s, sat, p)
                sayac[(s["bolum"], neden)] += 1
                f.write(json.dumps({"soru_id": s["soru_id"], "tutuldu": ok, "neden": neden, "satir": sat,
                                    "cagrilar": [{"mesajlar": c["mesajlar"], "araclar_ozeti": ozet(c["araclar"]),
                                                  "icerik": c["cevap"].icerik,
                                                  "arac_cagrilari": c["cevap"].arac_cagrilari} for c in cagrilar]},
                                   ensure_ascii=False, default=_json) + "\n")
                if i % 50 == 0:
                    print(f"{i}/{len(sorular)} {time.time() - bas:.0f} sn", flush=True)
        finally:
            sunucu.kapat()
    for (bolum, neden), n in sorted(sayac.items()):
        print(f"  {bolum:9s} {neden:40s} {n}")


# ------------------------------------------------------------------ çizim


def ciz_adimi() -> None:
    from sayman.assistant.orchestrator import ModelCevabi, arac_tanimlari
    from sayman.training.prompt import istem_ciz, tamamlama

    sorular = {s["soru_id"]: s for s in sorulari_oku()}
    araclar = arac_tanimlari()
    araclar_ozeti = ozet(araclar)
    VERI.mkdir(parents=True, exist_ok=True)
    ornekler, gorulen, bas = {"egitim": [], "dogrulama": []}, set(), time.time()
    with open(IZLER, encoding="utf-8") as f:
        izler = [json.loads(x) for x in f if x.strip()]
    for i, iz in enumerate(izler, 1):
        if i % 100 == 0:
            print(f"{i}/{len(izler)} iz, {len(gorulen)} örnek, {time.time() - bas:.0f} sn", flush=True)
        if not iz["tutuldu"]:
            continue
        s = sorular[iz["soru_id"]]
        for adim, c in enumerate(iz["cagrilar"], 1):
            if c["araclar_ozeti"] != araclar_ozeti:
                raise SystemExit("izlerdeki araç listesi bugünkünden farklı; izler adımını yeniden çalıştırın")
            istem = istem_ciz(c["mesajlar"], araclar)
            tam = tamamlama(ModelCevabi(icerik=c["icerik"], arac_cagrilari=[tuple(x) for x in c["arac_cagrilari"]]))
            if (istem, tam) in gorulen:
                continue
            gorulen.add((istem, tam))
            ornekler[s["ayrim"]].append({"soru_id": s["soru_id"], "adim": adim, "bolum": s["bolum"],
                                         "niyet": s["niyet"], "istem": istem, "tamamlama": tam})
    for ad, liste in ornekler.items():
        with open(VERI / f"{ad}.jsonl", "w", encoding="utf-8", newline="\n") as f:
            for o in liste:
                f.write(json.dumps(o, ensure_ascii=False) + "\n")
    rapor = {ad: {"ornek": len(liste), "soru": len({o["soru_id"] for o in liste}),
                  "bolum": dict(Counter(o["bolum"] for o in liste)),
                  "istem_karakter_en_cok": max((len(o["istem"]) for o in liste), default=0),
                  "ozet": ozet([[o["istem"], o["tamamlama"]] for o in liste])} for ad, liste in ornekler.items()}
    rapor["izler"] = {"soru": len(izler), "tutulan": sum(i["tutuldu"] for i in izler),
                      "nedenler": dict(Counter(i["neden"] for i in izler))}
    (KOK / "reports" / "faz8_egitim_verisi.json").write_text(json.dumps(rapor, ensure_ascii=False, indent=1) + "\n",
                                                             encoding="utf-8")
    print(json.dumps(rapor, ensure_ascii=False, indent=1))


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Faz 8 eğitim verisi")
    ap.add_argument("adim", choices=("sorular", "gomme", "izler", "ciz"))
    ap.add_argument("--sinir", type=int, default=0, help="izler: yalnızca ilk N soru (deneme)")
    a = ap.parse_args()
    {"sorular": sorular_adimi, "gomme": gomme_adimi, "ciz": ciz_adimi}.get(a.adim, lambda: izler_adimi(a.sinir))()


if __name__ == "__main__":
    main()
