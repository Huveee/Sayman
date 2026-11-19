"""Faz 7 ölçümü: soru setini orkestratörden geçirir, her soruyu bir JSONL satırına yazar, sonra puanlayıp raporlar.

Modeller:
- gercek: Ollama'daki qwen3.5:9b (Faz 0 ayarları) ve gerçek anlam araması. Uzun sürer; kaldığı yerden devam eder.
- kahin: her cevabı bilen sahte model (düzeneğin kabul testi: her metrikte tam puan almalı).
- yanlis_arac, bir_hane, hep_ret, baska_musteri, yaziyla: negatif kontroller (src/sayman/olcum/kahin.py).

Her şey gerçek kapıdan (SaymanSunucusu), gerçek orkestratörden ve gerçek doğrulayıcıdan geçer. Kimlik kartları bu
çalıştırmaya özel rastgele bir anahtarla imzalanır (.env'deki anahtar kullanılmaz).

Kaldığı yerden devam: JSONL'de olan sorular atlanır. Her satırda sistem, kod, soru seti ve hedef özetleri vardır;
dosyadaki satırlar şimdikilerle aynı değilse çalıştırma durur (ölçüm ortasında sistem, soru seti ya da hedefler
değiştirilmemeli); --yeniden dosyayı baştan yazar.

Altyapı kuralları (Faz 8 aynı kurallarla ölçer):
- Model çağrısı hata verirse (model_hatasi, ör. Ollama kapandı) satır yazılmaz; 60 saniye sonra soru yeniden sorulur,
  üç denemede de hata alınırsa çalıştırma durur ve aynı komutla kaldığı yerden sürdürülür. Böylece ölçümün ortasında
  çöken Ollama kalan bütün soruları "başarısız" yazmaz.
- Bir model çağrısı aşırı yavaşsa (istemin tamamını saniyede 10, cevabı saniyede 1 tokenla işlemekten uzun sürdüyse;
  Faz 0 hızının 3-5 katı yavaş) soru 30 saniye sonra bir kez yeniden sorulur ve ikinci sonuç yazılır. Geliştirme
  duman testinde bir çağrı bellek darlığında saniyede 0,16 token üretti (normali ~5) ve 651 saniye sürdü. Yeniden
  sorulan sorular satırda ("altyapi") ve raporda sayılır.
Süre, adım ve bağlam sınırları modelin sonucudur ve yazılır.

Çıktı:
- gercek: reports/faz7_<set>.jsonl (her soru) ve reports/faz7_olcum_<set>.json (rapor)
- kâhin ve kontroller: data/olcum/<set>_<model>.jsonl; --kahin-hepsi ile reports/faz7_kahin.json

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I -u scripts\\faz7_olc.py --kahin-hepsi
    .venv\\Scripts\\python -I -u scripts\\faz7_olc.py --set gelistirme --model gercek
    .venv\\Scripts\\python -I -u scripts\\faz7_olc.py --set saklanan --model gercek
    .venv\\Scripts\\python -I -u scripts\\faz7_olc.py --set saklanan --model gercek --tekrar 10
    .venv\\Scripts\\python -I -u scripts\\faz7_olc.py --set saklanan --model gercek --yalniz-rapor
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import secrets
import sys
import tempfile
import time
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))

from sayman.ambar.kurulum import baglan, satirlar  # noqa: E402
from sayman.araclar.kayit import DenetimKaydi  # noqa: E402
from sayman.araclar.kimlik import kart_uret  # noqa: E402
from sayman.araclar.sunucu import SaymanSunucusu  # noqa: E402
from sayman.araclar.yetki import yetki_oku  # noqa: E402
from sayman.asistan.orkestrator import Asistan, Ayar  # noqa: E402
from sayman.belge import kutuphane  # noqa: E402
from sayman.olcum import parmak_izi, puanlama  # noqa: E402
from sayman.olcum.kahin import KONTROLLER, Kahin, KahinArayici  # noqa: E402
from sayman.veri.saat import saat_oku  # noqa: E402

SORU_SETI = KOK / "reports" / "faz7_soru_seti.json"
AMBAR = KOK / "data" / "ambar" / "sayman.duckdb"
BELGE = KOK / "data" / "belge"
CALISMA = KOK / "data" / "olcum"
MODELLER = ("gercek", "kahin", *KONTROLLER)


def jsonl_yolu(set_adi: str, model: str, tekrar: bool = False) -> Path:
    if model == "gercek":
        return KOK / "reports" / f"faz7_{set_adi}{'_tekrar' if tekrar else ''}.jsonl"
    return CALISMA / f"{set_adi}_{model}{'_tekrar' if tekrar else ''}.jsonl"


def jsonl_oku(yol: Path) -> list[dict]:
    if not yol.exists():
        return []
    return [json.loads(s) for s in yol.read_text(encoding="utf-8").splitlines() if s.strip()]


def kapsam_kumeleri() -> dict:
    """Satış rollerinin görebileceği kayıt kimlikleri (kapsam kontrolü için; ambarın kendisinden)."""
    con = baglan(AMBAR)
    try:
        kumeler = {}
        for rol, kosul in (("satis_yurtici", "m.yurtici"), ("satis_ihracat", "NOT m.yurtici")):
            m = f"SELECT musteri_id FROM musteriler m WHERE {kosul}"
            kume = {"doviz_kurlari": None}
            kume["musteriler"] = {r["musteri_id"] for r in satirlar(con, m)}
            for tablo, kimlik in (("faturalar", "fatura_id"), ("tahsilatlar", "tahsilat_id"), ("cekler", "cek_id")):
                kume[tablo] = {r[kimlik] for r in satirlar(con, f"SELECT {kimlik} FROM {tablo} WHERE musteri_id IN ({m})")}
            kumeler[rol] = kume
        return kumeler
    finally:
        con.close()


def parcalar() -> dict:
    return {p.parca_id: p for p in kutuphane.parcalar(kutuphane.oku(BELGE / "belgeler.jsonl"))}


def altyapi_yavas_mi(tur) -> bool:
    """Bir model çağrısı, istemin tamamını 10 token/sn, cevabı 1 token/sn ile işlemekten (artı 10 sn) uzun sürdü mü?"""
    return any(t.get("sure_s") and t.get("istem") is not None and t.get("uretim") is not None
               and t["sure_s"] > t["istem"] / 10 + t["uretim"] + 10 for t in tur.tokenlar)


def set_izi() -> dict:
    import hashlib
    veri = json.loads(SORU_SETI.read_text(encoding="utf-8"))
    hedef = hashlib.sha256(json.dumps(veri["hedefler"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {"soru_seti_ozeti": veri["ozet"], "hedef_ozeti": hedef}


def _iz(kayit: dict) -> tuple:
    return tuple(kayit.get(k) for k in ("sistem_ozeti", "kod_ozeti", "soru_seti_ozeti", "hedef_ozeti"))


def _kayit(soru: dict, tur, sistem: dict, model_izi: dict, seti: dict) -> dict:
    return {
        "soru_id": soru["soru_id"], "sistem_ozeti": sistem["ozet"], "kod_ozeti": sistem["kod_ozeti"], **seti,
        "model_ozeti": model_izi["ozet"], "durum": tur.durum, "cevap": tur.cevap, "sure_s": tur.sure_s,
        "model_cagrisi": tur.model_cagrisi, "neden": tur.neden,
        "cagrilar": [{"arac": c["arac"], "argumanlar": c["argumanlar"], "hata": c["yanit"].get("hata"),
                      "yanit": c["yanit"]} for c in tur.cagrilar],
        "taslaklar": [{"metin": t, "gecerli": d.gecerli, "kaynaksiz": [x.metin for x in d.kaynaksiz],
                       "gecersiz_alinti": [x.neden for x in d.gecersiz_alintilar], "alinti_eksik": d.alinti_eksik}
                      for t, d in zip(tur.taslaklar, tur.dogrulamalar)],
        "tokenlar": tur.tokenlar, "zaman": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def calistir(sorular: list[dict], model_adi: str, yol: Path, yeniden: bool = False, sessiz: bool = False) -> None:
    sistem = parmak_izi.sistem()
    model_izi = parmak_izi.model(ollama_ozeti=model_adi == "gercek")
    if model_adi != "gercek":
        model_izi = {"model": model_adi, "ozet": model_adi}
    if yeniden and yol.exists():
        yol.unlink()
    seti = set_izi()
    simdiki = (sistem["ozet"], sistem["kod_ozeti"], seti["soru_seti_ozeti"], seti["hedef_ozeti"])
    onceki = jsonl_oku(yol)
    if {_iz(s) for s in onceki} - {simdiki}:
        raise SystemExit(f"{yol.name}: önceki satırlar başka bir sistem, soru seti ya da hedeflerle üretilmiş; "
                         "--yeniden ile baştan başlatın.")
    yapilan = {s["soru_id"] for s in onceki}
    kalan = [s for s in sorular if s["soru_id"] not in yapilan]
    if not kalan:
        return
    yol.parent.mkdir(parents=True, exist_ok=True)
    CALISMA.mkdir(parents=True, exist_ok=True)
    saat = saat_oku(KOK / "data" / "saat.json")
    anahtar = secrets.token_bytes(32)
    tum_parcalar = parcalar()
    if model_adi == "gercek":
        from sayman.asistan.model import OllamaModeli
        from sayman.asistan.orkestrator import arac_tanimlari
        from sayman.belge.arama import arayici_kur
        model, arayici = OllamaModeli(), arayici_kur(BELGE, "anlam")
        bas = time.perf_counter()
        model([{"role": "user", "content": "Merhaba"}], arac_tanimlari(), 600)  # model belleğe yüklensin
        arayici.ara("ödeme vadesi", "cfo", 1, "anlam")
        print(f"ısınma {time.perf_counter() - bas:.1f} sn", flush=True)
    else:
        model = Kahin(None if model_adi == "kahin" else model_adi, tum_parcalar)
        arayici = KahinArayici(tum_parcalar)
    with tempfile.TemporaryDirectory() as gecici:
        sunucu = SaymanSunucusu(ambar=AMBAR, saat=lambda: saat, anahtar=anahtar, yetki=yetki_oku(),
                                kayit=DenetimKaydi(CALISMA / f"denetim_{yol.stem}.jsonl"),
                                kapsam_klasoru=Path(gecici) / "kapsam", belge=arayici, belge_yontemi="anlam")
        try:
            asistan = Asistan(sunucu, model, Ayar(), kayit=DenetimKaydi(CALISMA / f"turlar_{yol.stem}.jsonl"))
            with open(yol, "a", encoding="utf-8", newline="\n") as f:
                for i, s in enumerate(kalan, 1):
                    if hasattr(model, "soru"):
                        model.soru = s
                    if hasattr(arayici, "soru"):
                        arayici.soru = s
                    altyapi, hata = [], 0
                    while True:
                        tur = asistan.sor(s["soru"], kart_uret(s["kullanici"], anahtar, 900))
                        if tur.durum == "model_hatasi":
                            hata += 1
                            if hata == 3:
                                raise SystemExit(f"{s['soru_id']}: model üç kez hata verdi; satır yazılmadı. Ollama'yı "
                                                 "kontrol edip aynı komutla kaldığı yerden sürdürün.")
                            print(f"{s['soru_id']}: model hatası ({tur.neden}); 60 sn sonra yeniden", flush=True)
                            time.sleep(60)
                            continue
                        if altyapi_yavas_mi(tur) and not altyapi:
                            altyapi.append({"durum": tur.durum, "sure_s": tur.sure_s, "tokenlar": tur.tokenlar})
                            print(f"{s['soru_id']}: aşırı yavaş model çağrısı ({tur.sure_s} sn); 30 sn sonra yeniden",
                                  flush=True)
                            time.sleep(30)
                            continue
                        break
                    kayit = _kayit(s, tur, sistem, model_izi, seti)
                    if altyapi:
                        kayit["altyapi"] = {"yeniden_soruldu": altyapi, "ikincisi_de_yavas": altyapi_yavas_mi(tur)}
                    f.write(json.dumps(kayit, ensure_ascii=False) + "\n")
                    f.flush()
                    if not sessiz:
                        print(f"{len(yapilan) + i:3d}/{len(sorular)} {s['soru_id']:28s} {tur.durum:13s} "
                              f"{tur.sure_s:6.1f} sn", flush=True)
        finally:
            sunucu.kapat()


def puanla_hepsi(sorular: list[dict], kayitlar: list[dict], kumeler: dict, parca_rolleri: dict, bu_yil: int):
    sozluk = {s["soru_id"]: s for s in sorular}
    kayit = {k["soru_id"]: k for k in kayitlar}
    puanlar = [puanlama.puanla(s, kayit[s["soru_id"]], kumeler, parca_rolleri, bu_yil)
               for s in sorular if s["soru_id"] in kayit]
    return puanlar, puanlama.ozetle(sozluk, puanlar)


def tekrar_karsilastir(asil: list[dict], tekrar: list[dict]) -> dict:
    a = {k["soru_id"]: k for k in asil}
    satirlar_ = []
    for t in tekrar:
        k = a.get(t["soru_id"])
        if k is None:
            continue
        cagri = lambda x: [(c["arac"], json.dumps(c["argumanlar"], sort_keys=True)) for c in x["cagrilar"]]  # noqa: E731
        satirlar_.append({"soru_id": t["soru_id"], "ayni_durum": k["durum"] == t["durum"],
                          "ayni_cevap": k["cevap"] == t["cevap"], "ayni_cagrilar": cagri(k) == cagri(t),
                          "sure_s": [k["sure_s"], t["sure_s"]]})
    n = len(satirlar_)
    return {"soru": n, "ayni_durum": sum(s["ayni_durum"] for s in satirlar_),
            "ayni_cevap": sum(s["ayni_cevap"] for s in satirlar_),
            "ayni_cagrilar": sum(s["ayni_cagrilar"] for s in satirlar_), "sorular": satirlar_}


def rapor(set_adi: str, model_adi: str, veri: dict, kumeler: dict, parca_rolleri: dict, bolumler) -> dict:
    sorular = [s for s in veri["sorular"] if s["set"] == set_adi and s["bolum"] in bolumler]
    kayitlar = jsonl_oku(jsonl_yolu(set_adi, model_adi))
    bu_yil = int(veri["simulasyon_gunu"][:4])
    puanlar, ozet = puanla_hepsi(sorular, kayitlar, kumeler, parca_rolleri, bu_yil)
    sonuc = {
        "olcum_zamani": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "set": set_adi, "model": model_adi, "soru_seti_ozeti": veri["ozet"],
        "sistem": parmak_izi.sistem(), "model_izi": parmak_izi.model(ollama_ozeti=model_adi == "gercek")
        if model_adi == "gercek" else {"model": model_adi},
        "tamamlanan": f"{len(puanlar)}/{len(sorular)}",
        "altyapi_yeniden_sorulan": [k["soru_id"] for k in kayitlar if k.get("altyapi")],
        "hedefler": puanlama.hedefleri_degerlendir(ozet, veri["hedefler"]), "ozet": ozet,
        "sorular": [{k: p[k] for k in p if k not in ("taslak_tablosu", "alintilar")} for p in puanlar],
    }
    eski = sorted({k.get("soru_seti_ozeti") for k in kayitlar} - {veri["ozet"]})
    if eski:  # kayıtlar başka bir set özetiyle alınmış: model yeniden çalıştırılmadı, cevaplar yeni anahtarla puanlandı
        sonuc["yeniden_puanlama"] = {
            "kayit_soru_seti_ozetleri": eski,
            "not": "Bu kayıtlar soru setinin önceki bir sürümüyle alındı ve şimdiki setle yeniden puanlandı. Yeniden "
                   "puanlama yalnızca soru metinleri, soran kişiler ve sistem aynıyken geçerlidir (README Faz 7)."}
    tekrar = jsonl_oku(jsonl_yolu(set_adi, model_adi, tekrar=True))
    if tekrar:
        sonuc["tekrar"] = tekrar_karsilastir(kayitlar, tekrar)
    return sonuc


def kahin_hepsi(veri: dict, kumeler: dict, parca_rolleri: dict) -> dict:
    """Kâhin ve kontrolleri iki sette çalıştırır; kâhin her metrikte tam puan almalı, kontroller kaybetmeli."""
    sonuc = {"olcum_zamani": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
             "soru_seti_ozeti": veri["ozet"], "modeller": {}}
    for model_adi in ("kahin", *KONTROLLER):
        sonuc["modeller"][model_adi] = {}
        for set_adi in ("gelistirme", "saklanan"):
            sorular = [s for s in veri["sorular"] if s["set"] == set_adi]
            bas = time.perf_counter()
            calistir(sorular, model_adi, jsonl_yolu(set_adi, model_adi), yeniden=True, sessiz=True)
            r = rapor(set_adi, model_adi, veri, kumeler, parca_rolleri, ("arac", "yetkisiz", "belge"))
            o = r["ozet"]
            musteri = [p for p in r["sorular"] if p["bolum"] == "arac"
                       and veri_soru(veri, p["soru_id"]).get("diger_musteri")]
            sonuc["modeller"][model_adi][set_adi] = {
                "sure_s": round(time.perf_counter() - bas, 1),
                "arac_secimi": o["arac"]["arac_secimi"], "parametre": o["arac"]["parametre"],
                "rakam_dogrulugu": o["arac"]["cevap_dogrulugu"],
                "dogrulanmis_yanlis_sayi": o["arac"]["dogrulanmis_yanlis_sayi"],
                "musteri_sorularinda_rakam_dogrulugu": puanlama._oran(p["cevap_dogru"] for p in musteri),
                "belge_isabet": o["belge"]["isabet"], "belge_cevap": o["belge"]["cevap_dogrulugu"],
                "belge_alinti": o["belge"]["alinti_gecerli"], "yetkisiz_sizinti": o["yetkisiz"]["sizinti"],
                "kapsam_disi": o["genel"]["kapsam_disi_satir"], "durumlar": o["genel"]["durumlar"],
                "dogrulayici_tablosu": {k: v for k, v in o["dogrulayici_tablosu"].items() if k != "aciklama"},
                "hata_turleri": o["arac"]["hata_turleri"],
            }
            print(model_adi, set_adi, json.dumps({k: v for k, v in sonuc["modeller"][model_adi][set_adi].items()
                                                  if k not in ("hata_turleri", "dogrulayici_tablosu")},
                                                 ensure_ascii=False), flush=True)
    return sonuc


def belge_tavani(veri: dict) -> dict:
    """Anlam aramasına sorunun kendi metni verilirse doğru madde ilk 3'te mi (soranın rolüyle)? Model aramayı kendi
    sözleriyle yapar; bu oran, modelin soruyu aynen arattığı durumdaki isabettir (gerçek koşudaki isabetle
    karşılaştırmak için). Yeni sorular Ollama'daki bge-m3 ile vektöre çevrilir."""
    from sayman.belge.arama import arayici_kur
    arayici = arayici_kur(BELGE, "anlam")
    sonuc = {}
    for set_adi in ("gelistirme", "saklanan"):
        sorular = [s for s in veri["sorular"] if s["set"] == set_adi and s["bolum"] == "belge"]
        kacan = [s["soru_id"] for s in sorular
                 if s["dogru_parca"] not in [p.parca_id for p, _ in arayici.ara(s["soru"], s["rol"], 3, "anlam")]]
        sonuc[set_adi] = {"soru": len(sorular), "isabet@3": round(1 - len(kacan) / len(sorular), 4), "kacan": kacan}
    return sonuc


_SORU_ONBELLEK: dict = {}


def veri_soru(veri: dict, soru_id: str) -> dict:
    if not _SORU_ONBELLEK:
        _SORU_ONBELLEK.update({s["soru_id"]: s for s in veri["sorular"]})
    return _SORU_ONBELLEK[soru_id]


def uyanik_tut() -> None:
    """Windows'ta süreç sürdükçe bilgisayarın boşta uykuya geçmesini engeller (SetThreadExecutionState). Sistem
    ayarlarını değiştirmez; süreç bitince istek kendiliğinden kalkar. Kapak kapanırsa bilgisayar yine uyur."""
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)  # ES_CONTINUOUS | ES_SYSTEM_REQUIRED


def main() -> None:
    uyanik_tut()
    ap = argparse.ArgumentParser(description="Faz 7 ölçümü")
    ap.add_argument("--set", choices=("gelistirme", "saklanan"))
    ap.add_argument("--model", choices=MODELLER, default="gercek")
    ap.add_argument("--bolum", default="arac,yetkisiz,belge")
    ap.add_argument("--sinir", type=int, default=0, help="yalnızca ilk N soru (deneme için)")
    ap.add_argument("--sorular", default="", help="yalnızca bu soru kimlikleri (virgülle; duman testi için)")
    ap.add_argument("--yeniden", action="store_true", help="JSONL'i baştan yaz")
    ap.add_argument("--tekrar", type=int, default=0, help="belirlenimcilik: setten eşit aralıklı N soruyu yeniden sor")
    ap.add_argument("--yalniz-rapor", action="store_true")
    ap.add_argument("--kahin-hepsi", action="store_true", help="kâhin ve kontroller, iki set; reports/faz7_kahin.json")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    veri = json.loads(SORU_SETI.read_text(encoding="utf-8"))
    kumeler = kapsam_kumeleri()
    parca_rolleri = {pid: p.roller for pid, p in parcalar().items()}
    if a.kahin_hepsi:
        sonuc = kahin_hepsi(veri, kumeler, parca_rolleri)
        try:
            sonuc["belge_tavani"] = belge_tavani(veri)
        except Exception as e:  # Ollama kapalıysa
            sonuc["belge_tavani"] = {"hata": f"{type(e).__name__}: {str(e)[:200]}"}
        print("belge tavanı", json.dumps(sonuc["belge_tavani"], ensure_ascii=False), flush=True)
        (KOK / "reports" / "faz7_kahin.json").write_text(json.dumps(sonuc, ensure_ascii=False, indent=1) + "\n",
                                                         encoding="utf-8")
        return
    if not a.set:
        ap.error("--set gerekli")
    bolumler = tuple(a.bolum.split(","))
    sorular = [s for s in veri["sorular"] if s["set"] == a.set and s["bolum"] in bolumler]
    if a.sorular:
        secili = set(a.sorular.split(","))
        sorular = [s for s in sorular if s["soru_id"] in secili]
    if a.sinir:
        sorular = sorular[:a.sinir]
    if a.tekrar:
        asil = [k["soru_id"] for k in jsonl_oku(jsonl_yolu(a.set, a.model))]
        adim = max(len(asil) // a.tekrar, 1)
        secili = set(asil[::adim][:a.tekrar])
        calistir([s for s in sorular if s["soru_id"] in secili], a.model, jsonl_yolu(a.set, a.model, tekrar=True),
                 yeniden=a.yeniden)
    elif not a.yalniz_rapor:
        calistir(sorular, a.model, jsonl_yolu(a.set, a.model), yeniden=a.yeniden)
    r = rapor(a.set, a.model, veri, kumeler, parca_rolleri, bolumler)
    hedef = KOK / "reports" / f"faz7_olcum_{a.set}.json" if a.model == "gercek" else CALISMA / f"rapor_{a.set}_{a.model}.json"
    hedef.write_text(json.dumps(r, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"tamamlanan": r["tamamlanan"], "hedefler": [(h["ad"], h["deger"], h["gecti"]) for h in r["hedefler"]]},
                     ensure_ascii=False, indent=1))
    print(json.dumps({k: r["ozet"][k] for k in ("arac", "belge", "yetkisiz", "sure")}, ensure_ascii=False, indent=1)[:4000])


if __name__ == "__main__":
    main()
