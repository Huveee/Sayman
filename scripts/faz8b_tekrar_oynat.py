"""Faz 8b: kayıtlı Faz 7/8 cevaplarını doğrulayıcıdan yeniden geçirir (model çalıştırmadan).

Önce düzeneğin kendisi sınanır: bugünkü doğrulayıcı, kayıtlı her taslak için o gün verilen kararı (gecerli,
kaynaksız sayılar) aynen vermelidir. Sonra yeni kuralların her biri için sayılır: gösterilen cevaplardan kaçı yeni
kurala takılır, bunların kaçı puanlayıcıya göre doğru (yanlışlıkla durdurulan) ve kaçı yanlış (yakalanan) cevaptır.

Kullanım (proje kökünden):
    .venv\\Scripts\\python -I scripts\\faz8b_tekrar_oynat.py
"""

from __future__ import annotations

import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path

KOK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(KOK / "src"))

from sayman.asistan.dogrulayici import dogrula  # noqa: E402

RAPOR = KOK / "reports"
OLCUM = KOK / "data" / "olcum"

# (etiket, her soru kaydı, puan raporu ya da None). Kâhin her soruyu doğru cevaplar: yanlış ret ölçüsü için.
KOSULAR = [
    ("faz7_gelistirme", RAPOR / "faz7_gelistirme.jsonl", RAPOR / "faz7_olcum_gelistirme.json"),
    ("faz7_saklanan", RAPOR / "faz7_saklanan.jsonl", RAPOR / "faz7_olcum_saklanan.json"),
    ("faz8_temel_kendi_gelistirme", RAPOR / "faz8_temel_kendi_gelistirme.jsonl",
     RAPOR / "faz8_temel_kendi_olcum_gelistirme.json"),
    ("faz8_temel_kendi_saklanan", RAPOR / "faz8_temel_kendi_saklanan.jsonl",
     RAPOR / "faz8_temel_kendi_olcum_saklanan.json"),
    ("faz8_ayarli_gelistirme", RAPOR / "faz8_ayarli_gelistirme.jsonl", RAPOR / "faz8_ayarli_olcum_gelistirme.json"),
    ("faz8_ayarli_saklanan", RAPOR / "faz8_ayarli_saklanan.jsonl", RAPOR / "faz8_ayarli_olcum_saklanan.json"),
    ("kahin_gelistirme", OLCUM / "gelistirme_kahin.jsonl", None),
    ("kahin_saklanan", OLCUM / "saklanan_kahin.jsonl", None),
]
# Negatif kontrol: müşteri sorularında başka bir müşterinin değerini yazar; sahiplik kuralı bunu yakalamalı.
KONTROLLER = [
    ("baska_musteri_gelistirme", OLCUM / "gelistirme_baska_musteri.jsonl"),
    ("baska_musteri_saklanan", OLCUM / "saklanan_baska_musteri.jsonl"),
]


def sorular() -> tuple[dict, dt.date]:
    v = json.loads((RAPOR / "faz7_soru_seti.json").read_text(encoding="utf-8"))
    return {s["soru_id"]: s for s in v["sorular"]}, dt.date.fromisoformat(v["simulasyon_gunu"])


def cagrilar(kayit: dict) -> list[dict]:
    """Kayıttaki çağrılar doğrulayıcının beklediği biçimde (kayıtta "hata", doğrulayıcıda "hata_mi")."""
    return [{"arac": c["arac"], "argumanlar": c.get("argumanlar") or {}, "hata_mi": c.get("hata") is not None,
             "yanit": c["yanit"]} for c in kayit.get("cagrilar", [])]


def kayitlar(yol: Path) -> list[dict]:
    return [json.loads(s) for s in yol.read_text(encoding="utf-8").splitlines() if s.strip()]


def puanlar(yol: Path | None) -> dict:
    if yol is None or not yol.exists():
        return {}
    return {p["soru_id"]: p for p in json.loads(yol.read_text(encoding="utf-8"))["sorular"]}


def duzenek_denetimi() -> dict:
    """Bugünkü doğrulayıcı kayıtlı kararları aynen veriyor mu?"""
    soru, bugun = sorular()
    sonuc = {}
    for etiket, yol, _ in KOSULAR:
        say = Counter()
        farklar = []
        for k in kayitlar(yol):
            c = cagrilar(k)
            taslaklar = k.get("taslaklar", [])
            for i, t in enumerate(taslaklar):
                d = dogrula(t["metin"], soru[k["soru_id"]]["soru"], c, bugun)
                ayni = d.eski_gecerli == t["gecerli"] and [x.metin for x in d.kaynaksiz] == t["kaynaksiz"]
                # Son taslak bütün çağrılardan sonra yazıldı; öncekiler sonradan yapılan çağrıları görmemiş olabilir.
                yer = "son" if i == len(taslaklar) - 1 else "ara"
                say[f"{yer}_{'ayni' if ayni else 'farkli'}"] += 1
                if not ayni and len(farklar) < 5:
                    farklar.append({"soru_id": k["soru_id"], "kayitli": [t["gecerli"], t["kaynaksiz"]],
                                    "simdi": [d.gecerli, [x.metin for x in d.kaynaksiz]]})
        sonuc[etiket] = {**say, "ornek_farklar": farklar}
    return sonuc


def _dogru_mu(p: dict | None) -> bool | None:
    """Puanlayıcıya göre gösterilen cevap doğru mu (araç ve belge soruları); yetkisizde ve puan yoksa None."""
    if p is None or p.get("bolum") == "yetkisiz":
        return None
    return bool(p.get("cevap_dogru"))


def yeni_kurallar(kosular=KOSULAR, kontroller=KONTROLLER) -> dict:
    """Gösterilen her cevabı (durumu "tamam" olan turun son taslağı) yeni kurallardan geçirir."""
    soru, bugun = sorular()
    sonuc = {}
    for etiket, yol, puan_yolu in [*kosular, *[(e, y, None) for e, y in kontroller]]:
        kahin = etiket.startswith("kahin")
        puan = puanlar(puan_yolu)
        say = Counter()
        ornekler = []
        for k in kayitlar(yol):
            if k["durum"] != "tamam" or not k.get("taslaklar"):
                continue
            s = soru[k["soru_id"]]
            d = dogrula(k["taslaklar"][-1]["metin"], s["soru"], cagrilar(k), bugun)
            dogru = True if kahin else _dogru_mu(puan.get(k["soru_id"]))
            sinif = {True: "dogru", False: "yanlis", None: "puansiz"}[dogru]
            say["gosterilen"] += 1
            for kural, takildi in (("sahipsiz", d.sahipsiz), ("kimliksiz", bool(d.kimliksiz)),
                                   ("kapsam_eksik", d.kapsam_eksik is not None)):
                if takildi:
                    say[f"{kural}_{sinif}"] += 1
                    ornekler.append({"kural": kural, "soru_id": k["soru_id"], "sinif": sinif, "rol": s["rol"],
                                     "kimliksiz": d.kimliksiz, "kapsam": d.kapsam_eksik,
                                     "cevap": k["taslaklar"][-1]["metin"][:160]})
            if s["rol"] != "cfo" and s["bolum"] != "belge":
                say["satis_gosterilen"] += 1
        sonuc[etiket] = {"sayilar": dict(sorted(say.items())), "ornekler": ornekler}
    return sonuc


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    denetim = duzenek_denetimi()
    farkli_son = {e: v.get("son_farkli", 0) for e, v in denetim.items() if v.get("son_farkli")}
    print("düzenek: son taslaklarda farklı karar:", farkli_son or "yok")
    sonuc = yeni_kurallar()
    for etiket, v in sonuc.items():
        print(etiket, v["sayilar"])
    if "--ornekler" in sys.argv:
        for etiket, v in sonuc.items():
            for o in v["ornekler"]:
                print(etiket, json.dumps(o, ensure_ascii=False))


if __name__ == "__main__":
    main()
