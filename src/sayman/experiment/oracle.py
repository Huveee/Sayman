"""Faz 9: otomatik kolun kâhini (her cevabı bilen sahte model).

Elle araçların kâhini (sayman.olcum.kahin.Kahin) yalnızca ilk turda tek bir çağrı yapar. Otomatik kolda bazı
cevaplar zincir ister (ör. müşteri adı -> müşteri kimliği -> müşteri kartı) ya da iki çağrının karşılaştırılmasını
(portföydeki ve tahsildeki çeklerin ilk vadelisi). Bu kâhin, dondurulmuş sınıflandırma dosyasındaki
(reports/faz9_siniflar.json) planı adım adım yürütür: her model çağrısında bir araç çağırır; bir adımın argümanı
önceki bir sonuçtan gelebilir ({"$": [çağrı sırası, yol...]}). Plan bitince cevabı araç sonuçlarından okur (soru
setindeki beklenen değerden değil) ve elle kâhinle aynı biçimde yazar.

Sınıfı "toplama" ya da "yok" olan sorularda (cevap için kayıtları toplamak ya da API'de olmayan bir tanım gerekir)
kâhin araç çağırmaz, cevap veremeyeceğini söyler: otomatik kolun bu düzenekte ulaşabileceği tavan budur.
"""

from __future__ import annotations

import json

from sayman.assistant.orchestrator import ModelCevabi
from sayman.evaluation.oracle import deger_yaz
from sayman.evaluation.scoring import yol_al

DOGRUDAN = ("tek_cagri", "zincir")
CEVAPSIZ = ("Bu soru, kayıtları toplamayı ya da kendim hesaplama yapmayı gerektiriyor; araç sonuçlarında hazır bir "
            "değer olmadığı için cevap veremiyorum.")


def cozumle(deger, sonuclar: list[dict]):
    """{"$": [i, yol...]} yer tutucularını i. araç sonucunun "sonuc" alanındaki değerle değiştirir."""
    if isinstance(deger, dict):
        if set(deger) == {"$"}:
            i, *yol = deger["$"]
            return yol_al(sonuclar[i]["sonuc"], yol)
        return {k: cozumle(v, sonuclar) for k, v in deger.items()}
    return deger


class OtomatikKahin:
    """Asistan'a model olarak verilir. Çalıştırıcı her sorudan önce `soru` alanını soru setindeki kayıtla doldurur."""

    def __init__(self, planlar: dict[str, dict]):
        self.planlar = planlar
        self.soru: dict | None = None

    def __call__(self, mesajlar: list[dict], araclar: list[dict], zaman_asimi: float) -> ModelCevabi:
        s = self.soru
        p = self.planlar[s["soru_id"]]
        if p["sinif"] not in DOGRUDAN:
            return ModelCevabi(icerik=CEVAPSIZ)
        sonuclar = [json.loads(m["content"]) for m in mesajlar if m["role"] == "tool"]
        if any("sonuc" not in r for r in sonuclar):
            return ModelCevabi(icerik="Sonuç alınamadı.")
        if len(sonuclar) < len(p["plan"]):
            adim = p["plan"][len(sonuclar)]
            return ModelCevabi(arac_cagrilari=[(adim["arac"], cozumle(adim["argumanlar"], sonuclar))])
        anahtarlar = s["alternatifler"][p["alternatif"]]["anahtarlar"]
        satirlar = []
        for c in p["cevap"]:
            a = anahtarlar[c["anahtar"]]
            satirlar.append(f"{a['etiket']}: {deger_yaz({**a, 'deger': yol_al(sonuclar[c['cagri']]['sonuc'], c['yol'])})}")
        return ModelCevabi(icerik="\n".join(satirlar))
