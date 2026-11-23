"""Eğitim örneği = (istem, tamamlama): modelin gerçek kullanımda gördüğü metin ve yazması gereken metin.

İstem Ollama'nın kendisine çizdirilir (`_debug_render_only`): orkestratörün o çağrıda gönderdiği mesajlar ve araç
listesi, OllamaModeli'nin gönderdiği biçimde (aynı ollama istemci sınıflarıyla) sunucuya verilir ve Ollama'nın
qwen3.5 çizicisinin ürettiği metin olduğu gibi alınır. Modelin HF sohbet şablonu bazı yerlerde farklı çizer
(ör. aynı turdaki araç çağrısının önüne boş düşünme bloğu koyar); eğitim metni ölçümde kullanılan çizimle aynı olsun
diye şablon taklit edilmez.

Tamamlama, modelin ürettiği ham metindir: araç çağrısı Qwen3.5'in XML biçimiyle (Ollama'nın geçmişi çizdiği biçimin
aynısı), son cevap düz metin; ikisi de <|im_end|> ile biter.
"""

from __future__ import annotations

import copy
import json

from sayman.assistant.orchestrator import ModelCevabi

BITIS = "<|im_end|>"


def istek_govdesi(mesajlar: list[dict], araclar: list[dict], model: str) -> dict:
    """OllamaModeli'nin /api/chat'e gönderdiği gövde (aynı istemci sınıflarıyla), yalnızca çizim istenerek."""
    from ollama._client import _copy_messages, _copy_tools
    from ollama._types import ChatRequest

    from sayman.assistant.model import SECENEKLER

    # Seçenekler OllamaModeli'ninkiyle aynı: farklı num_ctx gönderilirse Ollama yüklü modeli kapatıp yeniden yükler
    # (yalnızca çizim istense de); aynı anda çalışan bir ölçüm varsa ikisi de her istekte yeniden yüklemeye düşer.
    govde = ChatRequest(model=model, messages=list(_copy_messages(mesajlar)), tools=list(_copy_tools(araclar)),
                        stream=False, think=False, options=dict(SECENEKLER)).model_dump(exclude_none=True)
    govde["_debug_render_only"] = True
    return govde


def istem_ciz(mesajlar: list[dict], araclar: list[dict], model: str = "qwen3.5:9b",
              host: str = "http://127.0.0.1:11434", istemci=None) -> str:
    import httpx

    govde = istek_govdesi(mesajlar, araclar, model)
    r = (istemci or httpx).post(f"{host}/api/chat", json=govde, timeout=120)
    r.raise_for_status()
    metin = (r.json().get("_debug_info") or {}).get("rendered_template")
    if not metin:
        raise RuntimeError("Ollama çizilmiş istemi döndürmedi (_debug_render_only)")
    return metin


def _deger(v) -> str:
    return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)


def arac_cagrisi_metni(cagrilar: list[tuple[str, dict]], icerik: str = "") -> str:
    parcalar = []
    for i, (ad, arg) in enumerate(cagrilar):
        p = "" if i == 0 and not icerik.strip() else ("\n\n" if i == 0 else "\n")
        p += f"<tool_call>\n<function={ad}>\n"
        for k, v in (arg or {}).items():
            p += f"<parameter={k}>\n{_deger(v)}\n</parameter>\n"
        parcalar.append(p + "</function>\n</tool_call>")
    return icerik.strip() + "".join(parcalar)


def tamamlama(cevap: ModelCevabi) -> str:
    if cevap.arac_cagrilari:
        return arac_cagrisi_metni(cevap.arac_cagrilari, cevap.icerik) + BITIS
    return cevap.icerik.strip() + BITIS


class KayitciModel:
    """Bir modeli sarar; her çağrıda orkestratörün gönderdiği mesajları (o anki kopyası) ve modelin cevabını tutar."""

    def __init__(self, model):
        self.model = model
        self.cagrilar: list[dict] = []

    def __getattr__(self, ad):  # soru gibi alanlar sarılan modele gider
        return getattr(self.model, ad)

    def __setattr__(self, ad, deger):
        if ad in ("model", "cagrilar"):
            object.__setattr__(self, ad, deger)
        else:
            setattr(self.model, ad, deger)

    def __call__(self, mesajlar: list[dict], araclar: list[dict], zaman_asimi: float) -> ModelCevabi:
        cevap = self.model(mesajlar, araclar, zaman_asimi)
        self.cagrilar.append({"mesajlar": copy.deepcopy(mesajlar), "araclar": araclar, "cevap": cevap})
        return cevap

    def bosalt(self) -> list[dict]:
        c, self.cagrilar = self.cagrilar, []
        return c
