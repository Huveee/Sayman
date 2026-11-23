"""Öğretmen: eğitim sorularında doğru davranışı gösteren sahte model (Faz 7 kâhininin eğitim için olanı).

Kâhinden farkları:
- Cevaplar etiketli satırlar değil, doğal kısa Türkçe cümlelerdir (soru üretilirken hazırlanır).
- Belge sorularında gerçek arayıcının döndürdüğü sonuçlara bakar: doğru madde geldiyse onu alıntılar; gelmediyse bir
  kez daha, karşı tarafın adı ve konuyla arar; yine gelmezse cevap vermez (bu soru eğitime girmez).
- İlk adım rolden bağımsızdır: model kullanıcının rolünü görmez (sistem istemi ve araç listesi herkes için aynı), bu
  yüzden aynı soru her rolde aynı araç çağrısıyla başlar; yetkiyi kapı denetler. Rol yalnızca araç sonucunu, dolayısıyla
  ikinci adımı değiştirir.

Soru kaydı alanları: bolum, cagri ([araç, argümanlar] ya da None), cevap (araç soruları ve araçsız cevaplar için),
belge sorularında dogru_parca, anahtarlar, ikinci_sorgu, konu_cumlesi.
"""

from __future__ import annotations

import json
import random
import re

from sayman.assistant.orchestrator import ModelCevabi
from sayman.evaluation.oracle import alinti_penceresi


class CevapYok(Exception):
    """Öğretmen bu durumda doğru bir cevap bilmiyor; tur eğitime alınmaz."""


def _sonuclar(mesajlar: list[dict]) -> list[dict]:
    return [json.loads(m["content"]) for m in mesajlar if m["role"] == "tool"]


def _belge_sonuclari(sonuclar: list[dict]) -> list[list[dict]]:
    """Her belge_ara çağrısının döndürdüğü maddeler (sırayla)."""
    return [(r.get("sonuc") or {}).get("sonuclar", []) for r in sonuclar if r.get("arac") == "belge_ara"]


def belge_cevabi(soru: dict, madde_metni: str, parca_id: str) -> str:
    """Kısa özet (yalnızca rakamları maddede aynen geçiyorsa) + maddeden aynen alıntı + parça kimliği."""
    pencere = alinti_penceresi(madde_metni, soru.get("anahtarlar") or [])
    rakamlar = re.findall(r"\d+(?:[.,]\d+)*", soru.get("deger") or "")
    if soru.get("deger") and all(r in madde_metni for r in rakamlar):
        bas = f"{soru['konu_cumlesi']}: {soru['deger']}."
    else:
        bas = f"{soru['konu_cumlesi']} maddede şöyle geçiyor:"
    return f"{bas} \"{pencere}\" [{parca_id}]"


def bulunamadi_cevabi(soru: dict, ilk: dict | None, rng: random.Random) -> str:
    """Aranan bilgi gelen maddelerde yok. Doğrulayıcı, belge araması sonuç döndürdüyse alıntı ister: bakılan ilk
    maddeden kısa bir alıntı verilir."""
    bas = rng.choice(["Görebildiğim belgelerde bu soruyu cevaplayan bir madde bulamadım.",
                      "Erişebildiğim belgelerde bu konuda bir hüküm bulamadım."])
    if not ilk:
        return bas
    pencere = " ".join(ilk["metin"].split()[:8]).rstrip(" .,;:")
    if '"' in pencere:
        pencere = pencere.split('"')[0].rstrip(" .,;:")
    return f"{bas} Arama en yakın olarak şunu getirdi: \"{pencere}\" [{ilk['parca_id']}]"


class Ogretmen:
    """Asistan'a model olarak verilir; çalıştırıcı her sorudan önce `soru` alanını doldurur."""

    def __init__(self):
        self.soru: dict | None = None

    def __call__(self, mesajlar: list[dict], araclar: list[dict], zaman_asimi: float) -> ModelCevabi:
        s = self.soru
        rng = random.Random(f"ogretmen:{s['soru_id']}")
        sonuclar = _sonuclar(mesajlar)
        asistan_mesaji = any(m["role"] == "assistant" for m in mesajlar)
        if sum(m["role"] == "user" for m in mesajlar) > 1:
            raise CevapYok("düzeltme turu: öğretmenin cevabı doğrulayıcıdan geçmedi")
        if not sonuclar and not asistan_mesaji:
            if s.get("cagri"):
                arac, arg = s["cagri"]
                return ModelCevabi(arac_cagrilari=[(arac, dict(arg))])
            return ModelCevabi(icerik=s["cevap"])
        son = sonuclar[-1]
        if son.get("hata"):
            raise CevapYok(f"araç hata verdi: {son.get('hata')}")
        if s["bolum"] == "arac":
            return ModelCevabi(icerik=s["cevap"])
        if s["bolum"] == "belge":
            return self._belge(s, sonuclar, rng)
        # yetkisiz: araç sonuç döndürdü ama kapsam dışı (müşteri bulunamadı ya da gizli madde gelmedi)
        if s.get("tur") == "belge_yetkisi":
            gelen = [m for grup in _belge_sonuclari(sonuclar) for m in grup]
            if any(m["parca_id"] == s["gizli_parca"] for m in gelen):
                raise CevapYok("gizli madde aramada geldi: kapı denetimi beklenmedik")
            return ModelCevabi(icerik=bulunamadi_cevabi(s, gelen[0] if gelen else None, rng))
        if (son.get("sonuc") or {}).get("bulunamadi"):
            return ModelCevabi(icerik=s["cevap"])
        raise CevapYok("yetkisiz denemede araç sonuç döndürdü")

    def _belge(self, s: dict, sonuclar: list[dict], rng: random.Random) -> ModelCevabi:
        gruplar = _belge_sonuclari(sonuclar)
        for grup in gruplar:
            for m in grup:
                if m["parca_id"] == s["dogru_parca"]:
                    return ModelCevabi(icerik=belge_cevabi(s, m["metin"], m["parca_id"]))
        if len(gruplar) == 1 and s.get("ikinci_sorgu"):
            return ModelCevabi(arac_cagrilari=[("belge_ara", {"sorgu": s["ikinci_sorgu"]})])
        raise CevapYok("doğru madde iki aramada da gelmedi")
