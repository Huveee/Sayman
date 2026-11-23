"""Araç çağrısı geçerlilik kontrolü ve Faz 0 model seçim kuralı.

Geçerli araç çağrısı: araç adı listede var, argümanlar bir JSON nesnesi olarak
okunabiliyor, zorunlu alanlar mevcut, listede olmayan alan yok, tipler ve seçenek
(enum) değerleri doğru. Tipler JSON Schema'ya göre katı kontrol edilir: tamsayı
beklenen yerde "60" metni geçersizdir.

Seçim kuralı (Faz 0 kabul ölçütü): ön bellek ısındıktan sonra her araç çağırma turunu
en fazla 30 saniyede bitiren ve 5 örnek sorunun 5'inde geçerli araç çağrısı üreten
en büyük model demo modeli olur. Hiçbir aday sağlamazsa kural gevşetilmez; sonuç
"seçilemedi" olarak raporlanır.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from typing import Any

from sayman.model_selection.tool_drafts import ARACLAR_ADA_GORE

SURE_ESIGI_S = 30.0
GEREKEN_GECERLI = 5

_TARIH_DESENI = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def argumanlari_coz(argumanlar: Any) -> tuple[dict | None, str | None]:
    """Argümanları sözlüğe çevirir. Hata varsa (None, neden) döner."""
    if isinstance(argumanlar, dict):
        return argumanlar, None
    if isinstance(argumanlar, str):
        try:
            cozulen = json.loads(argumanlar)
        except json.JSONDecodeError:
            return None, "argümanlar JSON olarak okunamadı"
        if not isinstance(cozulen, dict):
            return None, "argümanlar bir JSON nesnesi değil"
        return cozulen, None
    if argumanlar is None:
        return {}, None
    return None, f"argüman tipi beklenmiyor: {type(argumanlar).__name__}"


def _deger_gecerli_mi(alan: str, deger: Any, sema: dict) -> str | None:
    tip = sema.get("type")
    if tip == "string":
        if not isinstance(deger, str):
            return f"'{alan}' metin olmalı"
        if "enum" in sema and deger not in sema["enum"]:
            return f"'{alan}' için izin verilmeyen değer: {deger!r}"
        if sema.get("format") == "date":
            if not _TARIH_DESENI.match(deger):
                return f"'{alan}' YYYY-MM-DD biçiminde değil"
            try:
                _dt.date.fromisoformat(deger)
            except ValueError:
                return f"'{alan}' geçerli bir tarih değil"
        return None
    if tip == "integer":
        if isinstance(deger, bool) or not isinstance(deger, int):
            return f"'{alan}' tamsayı olmalı"
        return None
    if tip == "number":
        if isinstance(deger, bool) or not isinstance(deger, (int, float)):
            return f"'{alan}' sayı olmalı"
        return None
    if tip == "boolean":
        if not isinstance(deger, bool):
            return f"'{alan}' doğru/yanlış olmalı"
        return None
    return f"'{alan}' için bilinmeyen şema tipi: {tip}"


def arac_cagrisi_gecerli_mi(cagri: dict, araclar: dict | None = None) -> tuple[bool, str]:
    """Tek bir araç çağrısını kontrol eder. cagri = {"ad": ..., "argumanlar": ...}."""
    araclar = ARACLAR_ADA_GORE if araclar is None else araclar
    ad = cagri.get("ad")
    if ad not in araclar:
        return False, f"bilinmeyen araç: {ad!r}"
    argumanlar, hata = argumanlari_coz(cagri.get("argumanlar"))
    if hata:
        return False, hata
    sema = araclar[ad]["function"]["parameters"]
    ozellikler = sema.get("properties", {})
    eksik = [z for z in sema.get("required", []) if z not in argumanlar]
    if eksik:
        return False, "eksik zorunlu alan: " + ", ".join(eksik)
    fazla = [k for k in argumanlar if k not in ozellikler]
    if fazla and sema.get("additionalProperties") is False:
        return False, "tanımsız alan: " + ", ".join(fazla)
    for alan, deger in argumanlar.items():
        hata = _deger_gecerli_mi(alan, deger, ozellikler[alan])
        if hata:
            return False, hata
    return True, "geçerli"


def cevap_gecerli_mi(cagrilar: list[dict], araclar: dict | None = None) -> tuple[bool, str]:
    """Bir model cevabındaki bütün araç çağrılarını kontrol eder; en az bir çağrı gerekir."""
    if not cagrilar:
        return False, "araç çağrısı yok"
    for cagri in cagrilar:
        gecerli, neden = arac_cagrisi_gecerli_mi(cagri, araclar)
        if not gecerli:
            return False, neden
    return True, "geçerli"


def beklenen_cagri_mi(cagrilar: list[dict], beklenen_arac: str, beklenen_argumanlar: dict) -> bool:
    """Çağrılardan biri beklenen araç mı ve beklenen argümanları birebir taşıyor mu?"""
    for cagri in cagrilar:
        if cagri.get("ad") != beklenen_arac:
            continue
        argumanlar, hata = argumanlari_coz(cagri.get("argumanlar"))
        if hata:
            continue
        if all(argumanlar.get(k) == v for k, v in beklenen_argumanlar.items()):
            return True
    return False


def kurali_sagliyor_mu(aday: dict, esik_s: float = SURE_ESIGI_S, gereken: int = GEREKEN_GECERLI) -> bool:
    """aday = {"model", "parametre_milyar", "turlar": [{"sure_s", "gecerli"}, ...]}."""
    turlar = aday.get("turlar", [])
    if len(turlar) < gereken:
        return False
    if any(t["sure_s"] > esik_s for t in turlar):
        return False
    return sum(1 for t in turlar if t["gecerli"]) >= gereken


def secim_kurali(adaylar: list[dict], esik_s: float = SURE_ESIGI_S, gereken: int = GEREKEN_GECERLI) -> str | None:
    """Kuralı sağlayan en büyük modelin adını, kimse sağlamıyorsa None döner."""
    gecenler = [a for a in adaylar if kurali_sagliyor_mu(a, esik_s, gereken)]
    if not gecenler:
        return None
    return max(gecenler, key=lambda a: a["parametre_milyar"])["model"]
