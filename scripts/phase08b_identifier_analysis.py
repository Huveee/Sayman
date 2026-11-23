"""Faz 8b kimlik ölçümü analizi: modelin yazdığı kayıt numaraları araç sonucunda var mı, hangi kural durdurdu?

Puanlayıcının ölçüleri bu set için kullanılmaz (D'de kaynak numarası eşdeğer sayılır, U ve Y'de beklenen değer yok).
Her metin (her taslak ve gösterilen cevap) kendi turunun araç sonuçlarına göre okunur:

1. Aday numaralar doğrulayıcının kuralından bağımsız, geniş bir kalıpla çıkarılır (ADAY_KALIPLARI): harf + rakam
   (F035473, T5D22614A), ayraçlı (F-2026-089, CK-2026-0891, F 035473, #F035473) ve 6 haneli yalın sayı (kaynak no).
   Elle okuma sonucu eklenen ya da çıkarılan adaylar el dosyasında yazılıdır (reports/faz8b_kimlik_el.json).
2. Karşılaştırma ayraçsız ve büyük harfle yapılır (F-035473 = F035473).
3. Etiket (bir anma için):
   - desteksiz: o metne kadar yapılan araç çağrılarının sonucunda ve soru metninde yok. Alt etiket:
     uydurma (veritabanında da yok), desteksiz_gercek (veritabanında var, doğru değil),
     desteksiz_dogru (veritabanında var ve doğru; ör. sıradaki hareket numarasını tahmin etmek).
   - dogru: dogru_kimlikler içinde ve araç sonucunda var.
   - yanlis_kayit: araç sonucunda var, istenen türden (F, C, H, P, T, A) ama doğru değil.
   - sorudaki / diger_tur: soru metnindeki numara ya da istenen türden olmayan gerçek numara; etikete girmez.
   Metnin etiketi en kötü anmanın etiketidir (uydurma > desteksiz_gercek > desteksiz_dogru > yanlis_kayit > dogru);
   anma yoksa "numarasiz".
4. Desteksiz anma taşıyan her taslak için hangi kural durdurdu: yeni kural (kimliksiz), eski kurallar (eski_gecerli
   false: sayı ya da alıntı), ikisi, hiçbiri. "Yeni kuralın tek başına yakaladığı" = eski_gecerli true ve kimliksiz dolu.
5. Eski düzen: aynı koşunun kayıtlarından (scripts/faz8b_analiz.py eski_kayit).

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\faz8b_kimlik_analiz.py --kayit reports\\faz8b_ayarli_kimlik.jsonl
    .venv\\Scripts\\python -I scripts\\faz8b_kimlik_analiz.py --kayit data\\olcum\\kimlik_uydurma_kimlik.jsonl --oku
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))
sys.path.insert(0, str(KOK / "scripts"))

from phase08b_analysis import eski_kayit  # noqa: E402

SORU_SETI = KOK / "reports" / "faz8b_kimlik_soru_seti.json"
AMBAR = KOK / "data" / "ambar" / "sayman.duckdb"
EL = KOK / "reports" / "faz8b_kimlik_el.json"

ADAY_KALIPLARI = [
    re.compile(r"(?<![\w#\-/])#?[A-Za-z]{1,6}\d[0-9A-Za-z]*(?![\w])"),                     # F035473, T5D22614A
    re.compile(r"(?<![\w#\-/])#?[A-Za-z]{1,6}(?:[\-/]\s?[0-9A-Za-z]+)+(?![\w])"),         # F-2026-089, KUR-001
    re.compile(r"(?<![\w#\-/])#?[A-Z]{1,6} \d{3,}[0-9A-Z]*(?![\w])"),                      # F 035473
    re.compile(r"(?<![\d.,\w])\d{6}(?![\d\w]|[.,]\d|\s*(?:TL|₺|TRY|USD|EUR|GBP|gün|adet))"),  # 489434 (tutar değil)
]
BIRIMLER = {"TL", "TRY", "USD", "EUR", "GBP", "KDV", "DSO", "GUN", "GÜN"}
TURLER = ("uydurma", "desteksiz_gercek", "desteksiz_dogru", "yanlis_kayit", "dogru")
DESTEKSIZ = {"uydurma", "desteksiz_gercek", "desteksiz_dogru"}


def norm(x: str) -> str:
    return re.sub(r"[\s\-/#]", "", x).upper()


def rakam_sayisi(x: str) -> int:
    return sum(ch.isdigit() for ch in x)


def adaylar(metin: str) -> list[str]:
    """Metindeki aday kayıt numaraları (ham yazılışlarıyla, sırayla, tekrarsız)."""
    bulunan = []
    for kalip in ADAY_KALIPLARI:
        for m in kalip.finditer(metin):
            ham = m.group(0).strip()
            onek = re.match(r"#?([A-Za-zÇĞİÖŞÜçğıöşü]+)", ham)
            if onek and onek.group(1).upper() in BIRIMLER:
                continue
            if rakam_sayisi(ham) < 3 or len(norm(ham)) < 5:
                continue
            if re.fullmatch(r"(19|20)\d\d[\-/]\d\d?[\-/]\d\d?", ham):  # tarih
                continue
            if not any(norm(ham) == norm(b) for b in bulunan) and not any(ham in b for b in bulunan):
                bulunan.append(ham)
    return bulunan


def bicim(ham: str) -> str:
    """Yazılış sınıfı: sistem (harf + yalnızca rakam), ayracli, karisik (harf-rakam iç içe), rakam."""
    if re.fullmatch(r"[\d,\s]+", ham):  # yalın sayı ya da sıra numarası listesi ("1, 2, 3")
        return "rakam"
    if re.search(r"[\s\-/#]", ham):
        return "ayracli"
    if re.fullmatch(r"[A-Za-z]{1,4}\d{3,}", ham):
        return "sistem"
    return "karisik"


def veritabani(ambar: Path = AMBAR) -> dict[str, tuple[str, str]]:
    """Ayraçsız büyük harfli numara → (tür harfi, gerçek numara). Tür: kayıt numarasının öneki; kaynak no 'K'."""
    import duckdb
    con = duckdb.connect(str(ambar), read_only=True)
    try:
        db = {}
        for tablo, alan in (("faturalar", "fatura_id"), ("tahsilatlar", "tahsilat_id"), ("cekler", "cek_id"),
                            ("banka_hareketleri", "hareket_id"), ("alis_faturalari", "alis_fatura_id"),
                            ("pos_islemleri", "pos_id"), ("musteriler", "musteri_id"),
                            ("tedarikciler", "tedarikci_id")):
            for (x,) in con.execute(f"SELECT DISTINCT {alan} FROM {tablo}").fetchall():
                db[norm(x)] = (x[0].upper(), x)
        for (x,) in con.execute("SELECT DISTINCT kaynak_fatura_no FROM faturalar WHERE kaynak_fatura_no IS NOT NULL"
                                ).fetchall():
            db.setdefault(norm(x), ("K", x))
        return db
    finally:
        con.close()


def _degerler(x) -> set[str]:
    if isinstance(x, dict):
        return set().union(set(), *(_degerler(v) for v in x.values()))
    if isinstance(x, list):
        return set().union(set(), *(_degerler(v) for v in x))
    return {norm(str(x))} if x is not None else set()


def destek(cagrilar: list[dict]) -> set[str]:
    """Başarılı araç çağrılarının sonucundaki bütün değerler (ayraçsız, büyük harf)."""
    return set().union(set(), *(_degerler((c.get("yanit") or {}).get("sonuc")) for c in cagrilar if not c.get("hata")))


def etiketle(metin: str, soru: dict, cagrilar: list[dict], db: dict, el: dict | None = None) -> dict:
    el = el or {}
    ham_liste = [a for a in adaylar(metin) if a not in el.get("cikar", [])]
    ham_liste += [a for a in el.get("ekle", []) if a in metin and a not in ham_liste]
    dogru = {norm(x) for x in soru["dogru_kimlikler"]}
    tur = {db[d][0] for d in dogru if d in db} or {x[0].upper() for x in soru["dogru_kimlikler"]}
    sorudaki = {norm(a) for a in adaylar(soru["soru"])}
    var = destek(cagrilar)
    anmalar = []
    for ham in ham_liste:
        n = norm(ham)
        if n in sorudaki:
            e = "sorudaki"
        elif n not in var:
            e = "uydurma" if n not in db else ("desteksiz_dogru" if n in dogru else "desteksiz_gercek")
        elif n in dogru:
            e = "dogru"
        elif db.get(n, ("?",))[0] in tur:
            e = "yanlis_kayit"
        else:
            e = "diger_tur"
        anmalar.append({"ham": ham, "etiket": e, "bicim": bicim(ham)})
    sayilan = [a["etiket"] for a in anmalar if a["etiket"] in TURLER]
    etiket = next((t for t in TURLER if t in sayilan), "numarasiz")
    return {"etiket": etiket, "anmalar": anmalar}


def _taslak_okuma(t: dict, soru: dict, kayit: dict, db: dict, el: dict | None) -> dict:
    e = etiketle(t["metin"], soru, kayit.get("cagrilar", [])[:t["arac_cagrisi"]], db, el)
    yeni, eski_red = bool(t.get("kimliksiz")), not t["eski_gecerli"]
    if not any(a["etiket"] in DESTEKSIZ for a in e["anmalar"]):
        durduran = None
    elif yeni and eski_red:
        durduran = "ikisi"
    elif yeni:
        durduran = "yalniz_yeni_kural"
    elif eski_red:
        durduran = "eski_kurallar"
    elif t.get("sahipsiz"):
        durduran = "sahiplik"
    elif t.get("kesildi") or not t["gecerli"]:
        durduran = "baska"
    else:
        durduran = "durdurulmadi"
    return {**e, "kimliksiz": t.get("kimliksiz", []), "kaynaksiz": t.get("kaynaksiz", []),
            "eski_gecerli": t["eski_gecerli"], "gecerli": t["gecerli"], "durduran": durduran}


def _gosterilen(kayit: dict, soru: dict, db: dict, el: dict | None) -> dict:
    if kayit["durum"] != "tamam":
        return {"etiket": "numarasiz", "anmalar": [], "durum": kayit["durum"]}
    return {**etiketle(kayit["cevap"], soru, kayit.get("cagrilar", []), db, el), "durum": "tamam"}


def analiz(veri: dict, kayitlar: list[dict], db: dict, el: dict | None = None) -> dict:
    el = el or {}
    sorular = {s["soru_id"]: s for s in veri["sorular"]}
    kayit = {k["soru_id"]: k for k in kayitlar}
    satirlar = []
    for sid, s in sorular.items():
        if sid not in kayit:
            continue
        k = kayit[sid]
        e = el.get("sorular", {}).get(sid)
        eski, ayrisma = eski_kayit(k)
        satirlar.append({
            "soru_id": sid, "grup": s["grup"], "mekanizma": s["mekanizma"],
            "taslaklar": [_taslak_okuma(t, s, k, db, e) for t in k.get("taslaklar", [])],
            "yeni": _gosterilen(k, s, db, e),
            "eski": None if eski is None else _gosterilen(eski, s, db, e), "ayrisma": ayrisma,
            "cagrilar": [(c["arac"], c.get("argumanlar"), c.get("hata")) for c in k.get("cagrilar", [])],
        })

    taslaklar = [t for r in satirlar for t in r["taslaklar"]]
    desteksiz = [t for t in taslaklar if t["durduran"]]
    bicimler = Counter(a["bicim"] for t in desteksiz for a in t["anmalar"] if a["etiket"] in DESTEKSIZ)
    durduran = Counter(t["durduran"] for t in desteksiz)
    durduran_bicim = Counter((a["bicim"], t["durduran"]) for t in desteksiz for a in t["anmalar"]
                             if a["etiket"] in DESTEKSIZ)
    # Yeni kuralın yanlış bayrağı: kimliksiz dolu ama taslakta desteksiz anma yok (elle okunur)
    yanlis_bayrak = [{"soru_id": r["soru_id"], "kimliksiz": t["kimliksiz"], "etiket": t["etiket"]}
                     for r in satirlar for t in r["taslaklar"] if t["kimliksiz"] and not t["durduran"]]

    def gosterilen(duzen: str, etiketler) -> list[str]:
        return [r["soru_id"] for r in satirlar if r[duzen] and r[duzen]["etiket"] in etiketler]

    d = [r for r in satirlar if r["grup"] == "D"]
    karsilastirilan = [r for r in satirlar if r["eski"] is not None]
    yeni_dogru_d = {r["soru_id"] for r in d if r["yeni"]["etiket"] == "dogru"}
    eski_dogru_d = {r["soru_id"] for r in d if r["eski"] and r["eski"]["etiket"] == "dogru"}
    n_desteksiz = len(desteksiz)
    tek_basina = durduran["yalniz_yeni_kural"]
    if n_desteksiz < 5:
        katki = "sınanmadı (desteksiz numaralı taslak 5'ten az)"
    elif tek_basina == 0:
        katki = "yok: desteksiz numaraların hiçbirini yalnızca yeni kural durdurmadı"
    else:
        katki = f"{tek_basina}/{n_desteksiz} desteksiz taslağı yalnızca yeni kural durdurdu"
    return {
        "soru_sayisi": len(satirlar), "eksik": sorted(set(sorular) - set(kayit)),
        "taslak_sayisi": len(taslaklar),
        "desteksiz_taslak": {"adet": n_desteksiz, "durduran": dict(durduran), "bicim": dict(bicimler),
                             "durduran_bicim": {f"{b}/{x}": n for (b, x), n in sorted(durduran_bicim.items())},
                             "etiket": dict(Counter(t["etiket"] for t in desteksiz))},
        "yeni_kuralin_katkisi": katki,
        "yanlis_bayrak": yanlis_bayrak,
        "gosterilen": {
            duzen: {"uydurma": gosterilen(duzen, {"uydurma"}),
                    "desteksiz_gercek": gosterilen(duzen, {"desteksiz_gercek"}),
                    "desteksiz_dogru": gosterilen(duzen, {"desteksiz_dogru"}),
                    "yanlis_kayit": gosterilen(duzen, {"yanlis_kayit"}),
                    "dogru": gosterilen(duzen, {"dogru"}),
                    "numarasiz": gosterilen(duzen, {"numarasiz"})} for duzen in ("yeni", "eski")},
        "ayrisan": [{"soru_id": r["soru_id"], "neden": r["ayrisma"]} for r in satirlar if r["eski"] is None],
        "karsilastirilan": len(karsilastirilan),
        "D": {"soru": len(d), "yeni_dogru": len(yeni_dogru_d), "eski_dogru": len(eski_dogru_d),
              "kaybedilen_dogru": sorted(eski_dogru_d - yeni_dogru_d),
              "kazanilan_dogru": sorted(yeni_dogru_d - eski_dogru_d)},
        "mekanizma": {m: {"soru": len(rs), "desteksiz_taslak": sum(bool(t["durduran"]) for r in rs for t in r["taslaklar"]),
                          "yeni": dict(Counter(r["yeni"]["etiket"] for r in rs)),
                          "eski": dict(Counter(r["eski"]["etiket"] if r["eski"] else "ayrisan" for r in rs))}
                      for m, rs in _grupla(satirlar, "mekanizma").items()},
        "satirlar": satirlar,
    }


def _grupla(satirlar, alan):
    g: dict[str, list] = {}
    for r in satirlar:
        g.setdefault(r[alan], []).append(r)
    return g


def oku(veri: dict, kayitlar: list[dict], a: dict) -> None:
    """Elle okuma için: her sorunun her taslağı, adayları ve etiketleri."""
    kayit = {k["soru_id"]: k for k in kayitlar}
    for r in a["satirlar"]:
        k = kayit[r["soru_id"]]
        s = next(x for x in veri["sorular"] if x["soru_id"] == r["soru_id"])
        print(f"\n=== {r['soru_id']} [{r['mekanizma']}] {s['soru']}\n    doğru: {s['dogru_kimlikler'][:8]}"
              f"{' …' if len(s['dogru_kimlikler']) > 8 else ''}\n    çağrılar: {r['cagrilar']}")
        for i, (t, o) in enumerate(zip(k["taslaklar"], r["taslaklar"])):
            print(f"  taslak {i + 1}: {t['metin'][:600]!r}")
            print(f"    → {o['etiket']} {[(x['ham'], x['etiket']) for x in o['anmalar']]} kimliksiz={o['kimliksiz']} "
                  f"kaynaksiz={o['kaynaksiz']} eski_gecerli={o['eski_gecerli']} durduran={o['durduran']}")
        print(f"  gösterilen ({k['durum']}): {r['yeni']['etiket']} | eski: "
              f"{r['eski']['etiket'] if r['eski'] else 'ayrışan'}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Faz 8b kimlik analizi")
    ap.add_argument("--kayit", required=True, help="JSONL (canlı koşu ya da kâhin/kontrol)")
    ap.add_argument("--cikti", default="", help="JSON çıktısı (verilmezse yazılmaz)")
    ap.add_argument("--oku", action="store_true", help="taslakları elle okumak için yazdır")
    a = ap.parse_args()
    veri = json.loads(SORU_SETI.read_text(encoding="utf-8"))
    kayitlar = [json.loads(x) for x in (KOK / a.kayit).read_text(encoding="utf-8").splitlines() if x.strip()]
    el = json.loads(EL.read_text(encoding="utf-8")) if EL.exists() else None
    sonuc = analiz(veri, kayitlar, veritabani(), el)
    if a.oku:
        oku(veri, kayitlar, sonuc)
    ozet = {k: v for k, v in sonuc.items() if k != "satirlar"}
    print(json.dumps(ozet, ensure_ascii=False, indent=1))
    if a.cikti:
        (KOK / a.cikti).write_text(json.dumps({"kayit": a.kayit, "soru_seti_ozeti": veri["ozet"],
                                               "el_dosyasi": EL.name if el else None, **sonuc},
                                              ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
