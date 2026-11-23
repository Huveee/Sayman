"""Eğitim verisi ile değerlendirme seti arasındaki sızıntı denetimi.

Eğitim soruları ayrı bir şirketten (tohum 43) ve ayrı yazılmış kalıplardan gelir. Bu modül bunu kendisi doğrular;
bir ihlal bulursa eğitim verisi yazılmaz. Denetlenenler:

1. Kalıp: eğitim kalıbının iskeleti (değişkenler maskelenmiş) bir değerlendirme kalıbının iskeletiyle aynı ya da
   kelime kümesi olarak ESIK_KALIP kadar benzer olmamalı.
2. Soru: doldurulmuş eğitim sorusu, doldurulmuş bir değerlendirme sorusuyla ESIK_SORU kadar benzer olmamalı.
3. Varlık: eğitim sorusundaki müşteri, fatura ya da karşı taraf kimliği değerlendirme sorularındaki bir kimlik
   olmamalı; müşteri/karşı taraf adı değerlendirme şirketindeki bir adla (sadeleştirilmiş hâliyle) aynı olmamalı.
4. Madde: belge sorusunun hedef maddesinin metni, değerlendirme şirketinin kütüphanesinde aynen geçmemeli
   (kalıp cümleli maddeler iki şirkette aynı çıkabiliyor).
5. Kur: döviz kuru sorusunun beklenen (para birimi, kur) değeri değerlendirme setindeki bir kur sorusunun beklenen
   değeri olmamalı (iki şirket aynı TCMB tablosunu kullanır).

Eşikler bu modül yazılırken, eğitim kalıpları denetlenmeden önce kondu.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

ESIK_KALIP = 0.6   # iskelet kelime kümelerinin Jaccard benzerliği bundan küçük olmalı
ESIK_SORU = 0.8    # doldurulmuş soruların karakter dizisi benzerliği (difflib oranı) bundan küçük olmalı

_TR = str.maketrans("İIÇĞÖŞÜÂÎÛ", "iıçğöşüâîû")


def kucuk(metin: str) -> str:
    return metin.translate(_TR).lower()


def normal(metin: str) -> str:
    """Küçük harf, noktalama yok, tek boşluk."""
    return " ".join(re.sub(r"[^\w{}<>]+", " ", kucuk(metin)).split())


def iskelet(kalip: str) -> str:
    """'{musteri} için açık alacak?' → '<v> için açık alacak'. Büyük harfli değişken ({Donem}) de maskelenir."""
    return normal(re.sub(r"\{[^}]+\}", " <v> ", kalip))


def jaccard(a: str, b: str) -> float:
    x, y = set(a.split()), set(b.split())
    return len(x & y) / len(x | y) if x | y else 1.0


def soru_benzerligi(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, normal(a), normal(b), autojunk=False).ratio()


@dataclass
class Degerlendirme:
    """Değerlendirme tarafında karşılaştırılacak her şey."""
    kaliplar: list[str]                      # Faz 7 araç/yetkisiz kalıpları ve Faz 5 belge kalıpları
    sorular: list[str]                       # doldurulmuş Faz 7 soruları (iki set)
    kimlikler: set[str]                      # soru setindeki müşteri ve fatura kimlikleri
    adlar: set[str]                          # değerlendirme şirketinin bütün müşteri ve tedarikçi adları (normal)
    madde_metinleri: set[str]                # değerlendirme kütüphanesindeki bütün madde metinleri
    kurlar: set[tuple[str, str]]             # (para birimi, alış kuru) beklenen değerleri


@dataclass
class Rapor:
    ihlaller: dict[str, list] = field(default_factory=dict)
    en_yakin_kalip: float = 0.0
    en_yakin_soru: float = 0.0

    @property
    def temiz(self) -> bool:
        return not any(self.ihlaller.values())


def denetle(egitim: list[dict], d: Degerlendirme, soru_karsilastir: bool = True) -> Rapor:
    """egitim: her soru {soru_id, soru, kalip, varliklar?, adlar?, hedef_metin?, kur?}."""
    r = Rapor(ihlaller={"kalip": [], "soru": [], "kimlik": [], "ad": [], "madde": [], "kur": []})
    deg_iskelet = {iskelet(k): k for k in d.kaliplar}
    deg_sorular = [(s, normal(s)) for s in d.sorular]
    kalip_sonucu: dict[str, tuple[float, str]] = {}
    for s in egitim:
        k = s.get("kalip")
        if k is not None:
            if k not in kalip_sonucu:
                i = iskelet(k)
                kalip_sonucu[k] = max(((1.0 if i == di else jaccard(i, di)), dk) for di, dk in deg_iskelet.items())
            puan, dk = kalip_sonucu[k]
            r.en_yakin_kalip = max(r.en_yakin_kalip, puan)
            if puan >= ESIK_KALIP:
                r.ihlaller["kalip"].append({"soru_id": s["soru_id"], "kalip": k, "degerlendirme": dk,
                                            "benzerlik": round(puan, 3)})
        if soru_karsilastir:
            n = normal(s["soru"])
            # Hızlı ön eleme: ortak kelimesi az olan çiftlerde dizi benzerliği yüksek olamaz.
            kelimeler = set(n.split())
            en, en_s = 0.0, None
            for ds, dn in deg_sorular:
                if len(kelimeler & set(dn.split())) * 2 < min(len(kelimeler), len(dn.split())):
                    continue
                b = soru_benzerligi(n, dn)
                if b > en:
                    en, en_s = b, ds
            r.en_yakin_soru = max(r.en_yakin_soru, en)
            if en >= ESIK_SORU:
                r.ihlaller["soru"].append({"soru_id": s["soru_id"], "soru": s["soru"], "degerlendirme": en_s,
                                           "benzerlik": round(en, 3)})
        for v in s.get("varliklar", []):
            if v in d.kimlikler:
                r.ihlaller["kimlik"].append({"soru_id": s["soru_id"], "kimlik": v})
        for ad in s.get("adlar", []):
            if normal(ad) in d.adlar:
                r.ihlaller["ad"].append({"soru_id": s["soru_id"], "ad": ad})
        if s.get("hedef_metin") is not None and s["hedef_metin"] in d.madde_metinleri:
            r.ihlaller["madde"].append({"soru_id": s["soru_id"], "parca": s.get("hedef_parca")})
        if s.get("kur") is not None and tuple(s["kur"]) in d.kurlar:
            r.ihlaller["kur"].append({"soru_id": s["soru_id"], "kur": list(s["kur"])})
    return r
