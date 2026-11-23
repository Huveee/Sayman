"""Faz 3: araçları gerçek veride dener ve raporlar.

- 60 yetkisiz deneme (tests/faz3_denemeler.py), MCP istemci oturumundan; sızıntı sayısı.
- C grubunda kaç deneme gerçekten saklanacak bir veriye dokundu (aynı soruya CFO farklı cevap aldı mı).
- Araç listesinin boyutu; her aracın en uzun cevabının boyutu ve süresi (en_fazla=10, en geniş aralık).
- --token: Ollama açıksa, Faz 0'da seçilen modelin bu metinleri gerçekte kaç token saydığı
  (prompt_eval_count, kısa bir mesajın sayısı çıkarılarak).

Girdiler: data/ambar/sayman.duckdb (faz2_ambar_kur.py), data/uretilen, data/saat.json
Çıktılar: reports/faz3_araclar.json; data/denetim/faz3_denemeler.jsonl (her çalıştırmada baştan yazılır)
Kimlik kartları bu çalıştırmaya özel rastgele bir anahtarla imzalanır; .env'deki anahtar kullanılmaz.

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\faz3_araclar_rapor.py [--token]
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
sys.path[:0] = [str(KOK / "src"), str(KOK / "tests")]

import support.authorization_trials as fd  # noqa: E402

from sayman.tools.audit_log import DenetimKaydi, dogrula  # noqa: E402
from sayman.tools.identity import kart_uret  # noqa: E402
from sayman.tools.server import SaymanSunucusu  # noqa: E402
from sayman.tools.permissions import yetki_oku  # noqa: E402
from sayman.data.table_io import SEMA, csv_oku, veri_hash  # noqa: E402
from sayman.data.clock import saat_oku  # noqa: E402

AMBAR = KOK / "data" / "ambar" / "sayman.duckdb"
URETILEN = KOK / "data" / "uretilen"
KAYIT = KOK / "data" / "denetim" / "faz3_denemeler.jsonl"
FAZ0 = KOK / "reports" / "faz0_hiz.json"
NUM_CTX = 8192


def ollama_araclari(sunucu) -> list[dict]:
    """Modelin gördüğü biçim (Ollama): ad, açıklama, parametre şeması."""
    return [{"type": "function", "function": {"name": t.name, "description": t.description,
                                              "parameters": t.inputSchema}} for t in sunucu.araclar()]


class TokenSayaci:
    def __init__(self, model: str):
        import ollama

        self.istemci, self.model = ollama.Client(), model
        self.taban = self._say("a")

    def _say(self, metin: str, araclar=None) -> int:
        r = self.istemci.chat(model=self.model, messages=[{"role": "user", "content": metin}], tools=araclar,
                              think=False, keep_alive="10m",
                              options={"num_ctx": NUM_CTX, "num_predict": 1, "temperature": 0, "seed": 42})
        return r.prompt_eval_count

    def metin(self, metin: str) -> int:
        return self._say(metin) - self.taban + 1  # "a" tek token

    def arac_listesi(self, araclar: list[dict]) -> int:
        return self._say("a", araclar) - self.taban


def main() -> int:
    for akis in (sys.stdout, sys.stderr):
        akis.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", action="store_true", help="Ollama ile gerçek token sayısını ölç")
    a = ap.parse_args()

    saat = saat_oku(KOK / "data" / "saat.json")
    T = csv_oku(URETILEN)
    anahtar, zaman = secrets.token_bytes(32), time.time()
    KAYIT.unlink(missing_ok=True)
    gecici = Path(tempfile.mkdtemp(prefix="sayman_faz3_"))

    bas = time.perf_counter()
    sunucu = SaymanSunucusu(AMBAR, lambda: saat, anahtar, yetki_oku(), DenetimKaydi(KAYIT),
                            kapsam_klasoru=KOK / "data" / "ambar" / "kapsam", zaman=lambda: zaman)
    kurulum_s = round(time.perf_counter() - bas, 2)
    hakem = fd.Hakem(T, saat.simdi, gecici)
    try:
        # ---- 60 deneme
        liste = fd.denemeler(T, saat.simdi, saat.veri_bitis)
        bas = time.perf_counter()
        sonuclar = fd.denemeleri_calistir(sunucu, liste, anahtar, zaman, hakem)
        deneme_s = round(time.perf_counter() - bas, 2)
        gruplar = {g: {"adet": sum(r["grup"] == g for r in sonuclar),
                       "sorunsuz": sum(r["grup"] == g and not r["sorunlar"] for r in sonuclar)} for g in "ABCDE"}
        c_grubu = [r for r in sonuclar if r["grup"] == "C"]

        # ---- kapsam ambarları
        kapsam_satirlari = {k: {t: sunucu.baglantilar[k].execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                                for t in ("musteriler", "faturalar", "tahsilatlar", "cekler")}
                            for k in sorted(sunucu.baglantilar)}
        bos_tablolar = {k: sorted(t for t in SEMA if not sunucu.baglantilar[k].execute(
            f"SELECT count(*) FROM {t}").fetchone()[0]) for k in fd.KAPSAMLAR}

        # ---- en uzun cevaplar
        en_cok = sunucu.baglantilar["hepsi"].execute(
            "SELECT m.ad FROM faturalar f JOIN musteriler m USING (musteri_id) GROUP BY m.ad "
            "ORDER BY count(*) DESC, m.ad LIMIT 1").fetchone()[0]
        cevaplar = []
        for kullanici in fd.KULLANICILAR.values():
            rol = sunucu.yetki.roller[sunucu.yetki.kullanicilar[kullanici]]
            kart = kart_uret(kullanici, anahtar, 3600, zaman)
            for arac, args in fd.en_genis_cagrilar(saat.simdi, en_cok):
                if arac not in rol.araclar:
                    continue
                bas = time.perf_counter()
                yanit, hata = fd.mcp_cagir(sunucu, [(arac, args, True, kart)])[0]
                sure = round(time.perf_counter() - bas, 3)
                if hata:
                    raise RuntimeError(f"{kullanici} {arac}: {yanit}")
                cevaplar.append({"arac": arac, "en_fazla": args.get("en_fazla"), "kullanici": kullanici,
                                 "karakter": len(json.dumps(yanit, ensure_ascii=False)), "sure_s": sure,
                                 "_metin": json.dumps(yanit, ensure_ascii=False)})
        en_uzun = {}
        for c in cevaplar:
            anahtar_ = (c["arac"], c["en_fazla"])
            if anahtar_ not in en_uzun or c["karakter"] > en_uzun[anahtar_]["karakter"]:
                en_uzun[anahtar_] = c
        en_uzun = sorted(en_uzun.values(), key=lambda c: -c["karakter"])

        # ---- araç listesi ve tokenlar
        liste_json = json.dumps(ollama_araclari(sunucu), ensure_ascii=False)
        faz0 = json.loads(FAZ0.read_text(encoding="utf-8"))
        aday0 = next(x for x in faz0["adaylar"] if x["model"] == faz0["secilen_model"])
        token_tahmini = round(len(liste_json) * aday0["arac_blogu_token_yaklasik"] / 6698)
        olculen = None
        cikti = KOK / "reports" / "faz3_araclar.json"
        if a.token:
            sayac = TokenSayaci(faz0["secilen_model"])
            olculen = {"model": faz0["secilen_model"], "olcum_zamani": dt.datetime.now().astimezone().isoformat(
                timespec="seconds"), "arac_listesi": sayac.arac_listesi(ollama_araclari(sunucu))}
            for c in en_uzun:
                c["token"] = sayac.metin(c["_metin"])
        elif cikti.exists():
            # Ollama'sız çalıştırmada önceki ölçüm korunur (cevabın uzunluğu değişmediyse token sayısı da aynıdır).
            onceki = json.loads(cikti.read_text(encoding="utf-8"))
            olculen = onceki.get("token_olcumu")
            if olculen:
                olculen.setdefault("olcum_zamani", onceki.get("olcum_zamani"))
            eski = {(c["arac"], c["en_fazla"], c["kullanici"], c["karakter"]): c.get("token")
                    for c in onceki.get("en_uzun_cevaplar", [])}
            for c in en_uzun:
                if eski.get((c["arac"], c["en_fazla"], c["kullanici"], c["karakter"])) is not None:
                    c["token"] = eski[(c["arac"], c["en_fazla"], c["kullanici"], c["karakter"])]
        if olculen and all("token" in c for c in en_uzun):
            # Pencere bütçesi: araç listesi, sistem istemi + soru (Faz 0'da ölçüldü), cevap yazma payı (512) ve
            # her tur için bir araç çağrısı (Faz 0'da ~34 token) + o çağrının cevabı.
            en_buyuk = max(c["token"] for c in en_uzun)
            tur = max(t["uretim_token"] for t in aday0["turlar"])
            kalan = NUM_CTX - olculen["arac_listesi"] - aday0["aracsiz_istem_token"] - 512
            olculen["en_buyuk_cevap"] = en_buyuk
            olculen["baglam"] = {"num_ctx": NUM_CTX, "arac_listesi": olculen["arac_listesi"],
                                 "sistem_ve_soru": aday0["aracsiz_istem_token"], "cevap_payi": 512,
                                 "arac_cagrisi_turu": tur, "kalan": kalan,
                                 "en_buyuk_cevaptan_sigan": kalan // (en_buyuk + tur),
                                 "sonrasinda_bos_kalan": kalan % (en_buyuk + tur)}
        for c in en_uzun:
            c.pop("_metin")
        en_yavas = max(cevaplar, key=lambda c: c["sure_s"])

        kayit = DenetimKaydi.oku(KAYIT)
        kapi_en_yavas = max(kayit, key=lambda r: r["sure_ms"])
        rapor = {
            "olcum_zamani": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
            "veri_hash": veri_hash(T)[0], "simdi": saat.simdi.isoformat(), "veri_bitis": saat.veri_bitis.isoformat(),
            "sunucu_kurulum_s": kurulum_s,
            "araclar": [t.name for t in sunucu.araclar()],
            "roller": {k: {"ad": r.ad, "kapsam": r.kapsam, "araclar": sorted(r.araclar),
                           "kullanicilar": sorted(u for u, rr in sunucu.yetki.kullanicilar.items() if rr == k)}
                       for k, r in sunucu.yetki.roller.items()},
            "kapsam_satirlari": kapsam_satirlari, "satis_kapsaminda_bos_tablolar": bos_tablolar,
            "arac_listesi": {"karakter": len(liste_json), "token_tahmini_faz0_oraniyla": token_tahmini,
                             "token_olculen": olculen["arac_listesi"] if olculen else None},
            "denemeler": {
                "toplam": len(sonuclar), "sorunsuz": sum(not r["sorunlar"] for r in sonuclar),
                "sizinti": sum(any("kapsam dışı" in s or "farklı" in s for s in r["sorunlar"]) for r in sonuclar),
                "gruplar": gruplar, "sure_s": deneme_s,
                "c_grubu_gizledi": f"{sum(r['gizledi'] for r in c_grubu)}/{len(c_grubu)}",
                "c_grubu_gizlemeyen": [{"kullanici": r["kullanici"], "arac": r["arac"]} for r in c_grubu
                                       if not r["gizledi"]],
                "sonuclar": sonuclar,
            },
            "denetim_kaydi": {"dosya": str(KAYIT.relative_to(KOK)).replace("\\", "/"), "satir": len(kayit),
                              "zincir_hatalari": dogrula(kayit)},
            "en_uzun_cevaplar": en_uzun,
            "sureler": {"cagri_sayisi": len(kayit),
                        "kapida_en_yavas_ms": kapi_en_yavas["sure_ms"], "kapida_en_yavas_arac": kapi_en_yavas["arac"],
                        "istemciden_en_yavas_s": en_yavas["sure_s"], "istemciden_en_yavas_arac": en_yavas["arac"]},
            "token_olcumu": olculen,
        }
    finally:
        hakem.kapat()
        sunucu.kapat()
    cikti.write_text(json.dumps(rapor, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    d = rapor["denemeler"]
    print(f"Sunucu kuruldu ({kurulum_s} s); {len(rapor['araclar'])} araç, 3 rol. Bugün: {saat.simdi}")
    for k, s in kapsam_satirlari.items():
        print(f"  {k}: {s['musteriler']} müşteri, {s['faturalar']} fatura, {s['cekler']} çek")
    print(f"60 deneme: {d['sorunsuz']}/{d['toplam']} beklendiği gibi, sızıntı {d['sizinti']} "
          f"(C grubunda gerçekten saklanan veri: {d['c_grubu_gizledi']}) - {deneme_s} s")
    for g, x in gruplar.items():
        print(f"  {g}: {x['sorunsuz']}/{x['adet']}")
    for r in sonuclar:
        if r["sorunlar"]:
            print(f"  SORUN {r['no']} {r['arac']} {r['argumanlar']}: {r['sorunlar']}")
    print(f"Denetim kaydı: {len(kayit)} satır, zincir {'sağlam' if not rapor['denetim_kaydi']['zincir_hatalari'] else 'BOZUK'}")
    tok = f", ölçülen {olculen['arac_listesi']} token" if olculen else ""
    print(f"Araç listesi: {len(liste_json)} karakter (Faz 0 oranıyla ~{token_tahmini} token{tok})")
    for c in en_uzun[:6]:
        tk = f", {c['token']} token" if "token" in c else ""
        print(f"  {c['arac']} (en_fazla={c['en_fazla']}, {c['kullanici']}): {c['karakter']} karakter{tk}, {c['sure_s']} s")
    print(f"En yavaş çağrı: kapıda {kapi_en_yavas['sure_ms']} ms ({kapi_en_yavas['arac']}), istemciden "
          f"{en_yavas['sure_s']} s ({en_yavas['arac']}); {len(kayit)} çağrı")
    if olculen and "baglam" in olculen:
        b = olculen["baglam"]
        kaynak = "ölçüldü" if a.token else f"önceki ölçüm, {olculen.get('olcum_zamani', '?')}"
        print(f"Bağlam ({kaynak}): {NUM_CTX} - araç listesi {b['arac_listesi']} - sistem ve soru {b['sistem_ve_soru']} - "
              f"cevap payı 512 = {b['kalan']} token; en büyük cevaptan (+ çağrı turu) {b['en_buyuk_cevaptan_sigan']} "
              f"tane sığar, sonra {b['sonrasinda_bos_kalan']} token boş kalır")
    print(f"Yazıldı: {cikti.relative_to(KOK)}")
    return 0 if d["sorunsuz"] == d["toplam"] and not rapor["denetim_kaydi"]["zincir_hatalari"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
