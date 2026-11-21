"""Faz 8: iki ölçümün soru soru eşleştirilmesi (temel model ↔ ince ayarlı model).

Her metrik, Faz 7 puanlayıcısının soru başına verdiği evet/hayır alanıdır (`puanlama.puanla`). Oranlar Faz 7 özetiyle
aynı biçimde hesaplanır: alanın "evet" olduğu soruların payı. İyi yön metriğe göre değişir; yanlış sayı şüphesinde ve
sızıntıda "hayır" iyidir.

- iyilesen: temel modelde kötü, ince ayarlı modelde iyi olan sorular.
- kotulesen: temel modelde iyi, ince ayarlı modelde kötü olan sorular.
- p: kesin McNemar testi. Yalnızca değişen sorulara bakar ve "iki model aslında eşit, değişimler yazı tura gibi
  rastgele" varsayımı altında bu kadar dengesiz bir dağılımın çıkma olasılığını verir.
"""

from __future__ import annotations

from math import comb

# bölüm -> (ad, puan alanı, "evet" iyi mi)
METRIKLER: dict[str, tuple[tuple[str, str, bool], ...]] = {
    "arac": (("arac_secimi", "arac_dogru", True), ("parametre", "parametre_dogru", True),
             ("cevap_dogrulugu", "cevap_dogru", True), ("dogrulanmis_yanlis_sayi", "yanlis_sayi_suphesi", False)),
    "belge": (("belge_ara_cagrildi", "belge_ara_cagrildi", True), ("isabet", "isabet", True),
              ("cevap_dogrulugu", "cevap_dogru", True), ("alinti_gecerli", "alinti_gecerli", True),
              ("dogru_parcadan_alinti", "dogru_parcadan_alinti", True)),
    "yetkisiz": (("sizinti", "sizinti", False), ("reddetti", "reddetti", True)),
}


def mcnemar_p(iyilesen: int, kotulesen: int) -> float | None:
    """Kesin, iki yönlü McNemar p değeri. Değişen soru yoksa None."""
    n = iyilesen + kotulesen
    if n == 0:
        return None
    k = min(iyilesen, kotulesen)
    return round(min(1.0, 2 * sum(comb(n, i) for i in range(k + 1)) / 2 ** n), 4)


def _oran(sayi: int, n: int) -> float | None:
    return round(sayi / n, 4) if n else None


def metrik_satiri(temel: list[dict], ayarli: list[dict], alan: str, evet_iyi: bool) -> dict:
    """Aynı sıradaki soru puanları için bir metriğin eşleştirilmiş satırı."""
    t = [bool(p[alan]) for p in temel]
    a = [bool(p[alan]) for p in ayarli]
    iyi_t = [x == evet_iyi for x in t]
    iyi_a = [x == evet_iyi for x in a]
    iyilesen = sum(1 for x, y in zip(iyi_t, iyi_a) if not x and y)
    kotulesen = sum(1 for x, y in zip(iyi_t, iyi_a) if x and not y)
    n = len(t)
    return {"n": n, "temel": _oran(sum(t), n), "ayarli": _oran(sum(a), n), "evet_iyi": evet_iyi,
            "iyilesen": iyilesen, "kotulesen": kotulesen, "p": mcnemar_p(iyilesen, kotulesen)}


def eslestir(temel: list[dict], ayarli: list[dict], gruplar: dict[str, str] | None = None) -> dict:
    """temel, ayarli: Faz 7 özetindeki `sorular` listeleri. gruplar: soru_id -> grup adı (ör. eğitimde yok/var).

    İki listede aynı sorular aynı bölümlerle bulunmalıdır; yoksa ValueError.
    """
    t = {p["soru_id"]: p for p in temel}
    a = {p["soru_id"]: p for p in ayarli}
    if len(t) != len(temel) or len(a) != len(ayarli):
        raise ValueError("bir ölçümde aynı soru iki kez var")
    if set(t) != set(a):
        raise ValueError(f"sorular farklı: yalnız temelde {sorted(set(t) - set(a))[:5]}, "
                         f"yalnız ayarlıda {sorted(set(a) - set(t))[:5]}")
    farkli = [s for s in t if t[s]["bolum"] != a[s]["bolum"]]
    if farkli:
        raise ValueError(f"bölümü farklı sorular: {farkli[:5]}")
    gruplar = gruplar or {}

    sonuc: dict = {"bolum": {}, "degisenler": []}
    for bolum, metrikler in METRIKLER.items():
        idler = sorted(s for s in t if t[s]["bolum"] == bolum)
        if not idler:
            continue
        kayit = {"n": len(idler), "metrikler": {}, "gruplar": {}}
        for ad, alan, evet_iyi in metrikler:
            kayit["metrikler"][ad] = metrik_satiri([t[s] for s in idler], [a[s] for s in idler], alan, evet_iyi)
            for s in idler:
                if bool(t[s][alan]) != bool(a[s][alan]):
                    iyilesti = bool(a[s][alan]) == evet_iyi
                    sonuc["degisenler"].append({"soru_id": s, "bolum": bolum, "metrik": ad,
                                                "yon": "iyilesti" if iyilesti else "kotulesti",
                                                "grup": gruplar.get(s)})
        for g in sorted({gruplar[s] for s in idler if s in gruplar}):
            gidler = [s for s in idler if gruplar.get(s) == g]
            kayit["gruplar"][g] = {"n": len(gidler), "metrikler": {
                ad: metrik_satiri([t[s] for s in gidler], [a[s] for s in gidler], alan, evet_iyi)
                for ad, alan, evet_iyi in metrikler}}
        sonuc["bolum"][bolum] = kayit
    return sonuc


def hedef_tablosu(temel_hedefler: list[dict], ayarli_hedefler: list[dict]) -> list[dict]:
    """Faz 7 hedef listelerini yan yana koyar. İki tarafta aynı hedefler aynı eşiklerle olmalıdır."""
    a = {h["ad"]: h for h in ayarli_hedefler}
    if [h["ad"] for h in temel_hedefler] != [h["ad"] for h in ayarli_hedefler]:
        raise ValueError("hedef listeleri farklı")
    satirlar = []
    for h in temel_hedefler:
        k = a[h["ad"]]
        if (h["metrik"], h["yon"], h["esik"]) != (k["metrik"], k["yon"], k["esik"]):
            raise ValueError(f"hedef tanımı farklı: {h['ad']}")
        satirlar.append({"ad": h["ad"], "yon": h["yon"], "esik": h["esik"], "zorunlu": h.get("zorunlu", False),
                         "temel": h["deger"], "temel_gecti": h["gecti"], "ayarli": k["deger"],
                         "ayarli_gecti": k["gecti"]})
    return satirlar
