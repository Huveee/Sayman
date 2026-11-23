"""Doğrulayıcı kör nokta düzeltmesi: kayıtlı taslaklar eski ve yeni doğrulayıcıdan geçirilir (model çalıştırılmaz).

Düzeltme (README "Doğrulayıcının iki kör noktası"): kayıt numarası alanları (kaynak_fatura_no, cek_no) "numara"
türünde kaynaktır; belge maddelerindeki ve sorudaki yazıyla sayılar ("on iki ay") kaynaktır. Eski doğrulayıcı
düzeltmeden önceki commit'ten okunur, yenisi çalışma ağacından; ikisi aynı taslaklara aynı girdilerle uygulanır.

Ölçülenler:
- Kararı değişen taslaklar: ret → kabul ve kabul → ret; her biri puanlayıcıyla (scripts/faz7_olc.puanla_hepsi'nin
  kullandığı puanlama.puanla) "beklenen değeri taşıyor" (dogru) ya da "taşımıyor" (eksik) diye ayrılır.
- Gösterilecek cevap: orkestratör ilk geçen taslağı gösterir. Yeni doğrulayıcıyla ilk geçen taslak eskisinden önce
  geliyorsa ve ondan önceki taslakların hiçbiri kabul → ret olmadıysa kayıt aynen oynatılabilir; sonuç sayılır.
- Kâhin koşularında (her cevap doğru) hiçbir taslak kabul → ret olmamalı; uydurma kontrollerinde (bir hane, yanlış
  birim, yanlış araç, başka müşteri, uydurma kimlik) hiçbir taslak ret → kabul olmamalı.

Çıktı: reports/kor_nokta_olcum.json (önceki faz raporlarına dokunulmaz).

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\kor_nokta_olcum.py
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import types
from collections import Counter
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))
sys.path.insert(0, str(KOK / "scripts"))

import phase07_evaluate  # noqa: E402
from sayman.assistant import verifier as yeni  # noqa: E402
from sayman.evaluation import scoring  # noqa: E402

ESKI_COMMIT = "6f1a970"   # düzeltmeden önceki son commit
RAPOR = KOK / "reports"
OLCUM = KOK / "data" / "olcum"
CIKTI = RAPOR / "kor_nokta_olcum.json"
F7, F8B, KIM = (RAPOR / "faz7_soru_seti.json", RAPOR / "faz8b_soru_seti.json", RAPOR / "faz8b_kimlik_soru_seti.json")

MODEL_KOSULARI = [
    *[(f"{e}_{s}", RAPOR / f"{e}_{s}.jsonl", F7) for e in ("faz7", "faz8_temel_kendi", "faz8_ayarli")
      for s in ("gelistirme", "saklanan")],
    ("faz7_saklanan_tekrar", RAPOR / "faz7_saklanan_tekrar.jsonl", F7),
    ("faz8_temel_tekrar_gelistirme", RAPOR / "faz8_temel_tekrar_gelistirme.jsonl", F7),
    ("faz8b_ayarli_yeni", RAPOR / "faz8b_ayarli_yeni.jsonl", F8B),
    ("faz8b_ayarli_kimlik", RAPOR / "faz8b_ayarli_kimlik.jsonl", KIM),
    ("faz9_el_yeni", RAPOR / "faz9_el_yeni.jsonl", F8B),
    ("faz9_oto_yeni", RAPOR / "faz9_oto_yeni.jsonl", F8B),
    ("faz9_duzeltme_yeni", RAPOR / "faz9_duzeltme_yeni.jsonl", F8B),
]
TOPLAM_ANAHTARLARI = ("taslak", "ret_kabul_dogru", "ret_kabul_eksik", "kabul_ret_dogru", "kabul_ret_eksik",
                      "kazanilan_dogru_cevap", "kaybedilen_dogru_cevap", "gosterilen_yeni_eksik")
UYDURMALAR = ("bir_hane", "yanlis_birim", "yanlis_arac", "baska_musteri", "uydurma_kimlik")
KONTROL_KOSULARI = [
    (f"{s}_{k}", OLCUM / f"{s}_{k}.jsonl", seti)
    for s, seti in (("gelistirme", F7), ("saklanan", F7), ("yeni", F8B), ("kimlik", KIM))
    for k in ("kahin", *UYDURMALAR, "yaziyla")
] + [("yeni_kahin_oto", OLCUM / "yeni_kahin_oto.jsonl", F8B)]


def _git_module(name: str, path: str) -> types.ModuleType:
    """Runs one source file as it was at ESKI_COMMIT and registers it under `name`."""
    kod = subprocess.run(["git", "show", f"{ESKI_COMMIT}:{path}"], cwd=KOK,
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout
    m = types.ModuleType(name)
    sys.modules[name] = m   # dataclasses look their module up in sys.modules
    exec(compile(kod, path.rsplit("/", 1)[-1], "exec"), m.__dict__)
    return m


def eski_dogrulayici() -> types.ModuleType:
    # The old verifier imports the old citation module by its old (Turkish) package path, which no longer exists.
    # Load that module from the same commit too and expose it under the old path only while the verifier loads.
    eski_paketler = ("sayman.belge", "sayman.belge.alinti")
    sys.modules["sayman.belge"] = types.ModuleType("sayman.belge")
    try:
        _git_module("sayman.belge.alinti", "src/sayman/belge/alinti.py")
        return _git_module("eski_dogrulayici", "src/sayman/asistan/dogrulayici.py")
    finally:
        for ad in eski_paketler:
            sys.modules.pop(ad, None)


def cagrilar(kayit: dict) -> list[dict]:
    return [{"arac": c["arac"], "argumanlar": c.get("argumanlar") or {}, "hata_mi": c.get("hata") is not None,
             "yanit": c["yanit"]} for c in kayit.get("cagrilar", [])]


def taslak_dogru_mu(soru: dict, kayit: dict, metin: str, kumeler: dict, roller: dict, bu_yil: int) -> bool | None:
    """Puanlayıcıya göre bu taslak gösterilseydi doğru olur muydu? Yetkisiz sorularda None."""
    if soru["bolum"] == "yetkisiz":
        return None
    tek = {**kayit, "durum": "tamam", "cevap": metin}
    return bool(scoring.puanla(soru, tek, kumeler, roller, bu_yil)["cevap_dogru"])


def kosu_olc(etiket: str, yol: Path, seti: Path, eski, kumeler: dict, roller: dict) -> dict:
    v = json.loads(seti.read_text(encoding="utf-8"))
    sorular = {s["soru_id"]: s for s in v["sorular"]}
    bugun = dt.date.fromisoformat(v["simulasyon_gunu"])
    say = Counter()
    ornekler = []
    sorular_listesi = {"kazanilan": [], "kaybedilen": [], "yeni_gosterilen_eksik": []}
    for satir in yol.read_text(encoding="utf-8").splitlines():
        if not satir.strip():
            continue
        k = json.loads(satir)
        s = sorular[k["soru_id"]]
        c = cagrilar(k)
        kararlar = []
        for t in k.get("taslaklar", []):
            e = eski.dogrula(t["metin"], s["soru"], c, bugun).gecerli
            n = yeni.dogrula(t["metin"], s["soru"], c, bugun).gecerli
            kararlar.append((e, n))
            say["taslak"] += 1
            if e != n:
                yon = "ret_kabul" if n else "kabul_ret"
                dogru = taslak_dogru_mu(s, k, t["metin"], kumeler, roller, bu_yil=bugun.year)
                sinif = {True: "dogru", False: "eksik", None: "yetkisiz"}[dogru]
                say[f"{yon}_{sinif}"] += 1
                if len(ornekler) < 12:
                    ornekler.append({"soru_id": k["soru_id"], "yon": yon, "sinif": sinif,
                                     "taslak": t["metin"][:240]})
        # gösterilecek cevap: ilk geçen taslak
        ilk_eski = next((i for i, (e, _) in enumerate(kararlar) if e), None)
        ilk_yeni = next((i for i, (_, n) in enumerate(kararlar) if n), None)
        if ilk_eski == ilk_yeni:
            continue
        oynanabilir = ilk_yeni is not None and all(not (e and not n) for e, n in kararlar[:ilk_yeni]) and (
            ilk_eski is None or ilk_yeni < ilk_eski)
        if not oynanabilir:
            say["gosterilen_degisti_oynatilamaz"] += 1
            continue
        metin = k["taslaklar"][ilk_yeni]["metin"]
        dogru = taslak_dogru_mu(s, k, metin, kumeler, roller, bugun.year)
        eski_dogru = (taslak_dogru_mu(s, k, k["taslaklar"][ilk_eski]["metin"], kumeler, roller, bugun.year)
                      if ilk_eski is not None else False)
        say[f"gosterilen_{'once' if ilk_eski is not None else 'yeni'}_{ {True: 'dogru', False: 'eksik', None: 'yetkisiz'}[dogru]}"] += 1
        if dogru and not eski_dogru:
            say["kazanilan_dogru_cevap"] += 1
            sorular_listesi["kazanilan"].append(k["soru_id"])
        if eski_dogru and dogru is False:
            say["kaybedilen_dogru_cevap"] += 1
            sorular_listesi["kaybedilen"].append(k["soru_id"])
        if dogru is False:
            sorular_listesi["yeni_gosterilen_eksik"].append(k["soru_id"])
    return {"sayilar": dict(sorted(say.items())), "sorular": sorular_listesi, "ornekler": ornekler}


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    eski = eski_dogrulayici()
    kumeler = phase07_evaluate.kapsam_kumeleri()
    roller = {pid: p.roller for pid, p in phase07_evaluate.parcalar().items()}
    sonuc = {"aciklama": __doc__.split("\n\n")[1].replace("\n", " "), "eski_commit": ESKI_COMMIT,
             "model_kosulari": {}, "kontroller": {}}
    for bolum, kosular in (("model_kosulari", MODEL_KOSULARI), ("kontroller", KONTROL_KOSULARI)):
        for etiket, yol, seti in kosular:
            if not yol.exists():
                sonuc[bolum][etiket] = {"yok": True}
                continue
            sonuc[bolum][etiket] = r = kosu_olc(etiket, yol, seti, eski, kumeler, roller)
            print(etiket, r["sayilar"])
    toplam = Counter()
    for r in sonuc["model_kosulari"].values():
        toplam.update(r.get("sayilar", {}))
    # sıfır olanlar da yazılır (Faz 11 raporu eksik anahtarı hata sayar)
    sonuc["model_toplami"] = {a: toplam.get(a, 0) for a in sorted({*toplam, *TOPLAM_ANAHTARLARI})}
    kahin_kabul_ret = sum(r.get("sayilar", {}).get(k, 0) for e, r in sonuc["kontroller"].items() if "kahin" in e
                          for k in ("kabul_ret_dogru", "kabul_ret_eksik", "kabul_ret_yetkisiz"))
    uydurma_ret_kabul = sum(r.get("sayilar", {}).get(k, 0) for e, r in sonuc["kontroller"].items()
                            if any(u in e for u in UYDURMALAR)
                            for k in ("ret_kabul_dogru", "ret_kabul_eksik", "ret_kabul_yetkisiz"))
    sonuc["kontrol_ozeti"] = {"kahin_kabul_ret": kahin_kabul_ret, "uydurma_ret_kabul": uydurma_ret_kabul}
    print("model toplamı:", sonuc["model_toplami"])
    print("kontrol özeti:", sonuc["kontrol_ozeti"])
    CIKTI.write_text(json.dumps(sonuc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("yazıldı:", CIKTI.relative_to(KOK))


if __name__ == "__main__":
    main()
