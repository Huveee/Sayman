"""İmzalı kimlik kartı: kim soruyor?

Kartı güvenilen arka uç (Faz 6 orkestratörü, Faz 10 demo sunucusu) üretir ve her araç çağrısının MCP
üst bilgisine (_meta) koyar. Model kartı görmez, araç argümanlarında kimlik alanı yoktur; bu yüzden model
kendini başka biri gibi gösteremez.

Kart = base64url(yük) + "." + base64url(HMAC-SHA256(anahtar, yük)). Yükte yalnızca kullanıcı adı ve son
geçerlilik zamanı vardır. Rol karttan değil, sunucudaki kullanıcı listesinden okunur: kartta rol yazsa bile
dikkate alınmaz. İmza sabit sürede karşılaştırılır (hmac.compare_digest). Anahtar yoksa sunucu açılmaz.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass

META_ANAHTARI = "sayman_kimlik"
ANAHTAR_DEGISKENI = "SAYMAN_KIMLIK_ANAHTARI"
SURUM = 1
EN_UZUN_SURE_S = 12 * 3600  # bir kart en çok 12 saat geçerli


class KimlikHatasi(Exception):
    """Kart reddedildi. `neden` denetim kaydına yazılır; kullanıcıya hep aynı mesaj gider."""

    def __init__(self, neden: str):
        super().__init__(neden)
        self.neden = neden


@dataclass(frozen=True)
class Kimlik:
    kullanici: str
    rol: str


def _b64(veri: bytes) -> str:
    return base64.urlsafe_b64encode(veri).rstrip(b"=").decode("ascii")


def _b64_coz(metin: str) -> bytes:
    return base64.urlsafe_b64decode(metin + "=" * (-len(metin) % 4))


def anahtar_oku(ortam: dict | None = None) -> bytes:
    """İmza anahtarını ortam değişkeninden okur (onaltılık, en az 32 bayt). Yoksa hata: sunucu açılmaz."""
    deger = (ortam if ortam is not None else os.environ).get(ANAHTAR_DEGISKENI, "").strip()
    if not deger:
        raise RuntimeError(f"{ANAHTAR_DEGISKENI} tanımlı değil; scripts/faz3_anahtar_uret.py ile üretin")
    try:
        anahtar = bytes.fromhex(deger)
    except ValueError as e:
        raise RuntimeError(f"{ANAHTAR_DEGISKENI} onaltılık değil") from e
    if len(anahtar) < 32:
        raise RuntimeError(f"{ANAHTAR_DEGISKENI} en az 32 bayt olmalı")
    return anahtar


def _imza(anahtar: bytes, yuk: str) -> str:
    return _b64(hmac.new(anahtar, yuk.encode("ascii"), hashlib.sha256).digest())


def kart_uret(kullanici: str, anahtar: bytes, sure_s: int = 3600, simdi: float | None = None) -> str:
    if not 0 < sure_s <= EN_UZUN_SURE_S:
        raise ValueError("kart süresi 0 ile 12 saat arasında olmalı")
    an = time.time() if simdi is None else simdi
    yuk = _b64(json.dumps({"v": SURUM, "k": kullanici, "son": int(an) + sure_s},
                          separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    return f"{yuk}.{_imza(anahtar, yuk)}"


def karti_dogrula(kart, anahtar: bytes, kullanicilar: dict[str, str], simdi: float | None = None) -> Kimlik:
    """Kartı doğrular ve kimliği döner; geçersizse KimlikHatasi. kullanicilar: kullanıcı adı -> rol."""
    if kart is None:
        raise KimlikHatasi("kart_yok")
    if not isinstance(kart, str) or kart.count(".") != 1 or len(kart) > 2048:
        raise KimlikHatasi("kart_bicimi")
    yuk, imza = kart.split(".")
    try:
        yuk.encode("ascii")
        imza.encode("ascii")
    except UnicodeEncodeError:
        raise KimlikHatasi("kart_bicimi") from None
    if not hmac.compare_digest(_imza(anahtar, yuk), imza):
        raise KimlikHatasi("imza_gecersiz")
    try:
        icerik = json.loads(_b64_coz(yuk).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise KimlikHatasi("kart_bicimi") from None
    if not isinstance(icerik, dict) or icerik.get("v") != SURUM or not isinstance(icerik.get("son"), int) \
            or not isinstance(icerik.get("k"), str):
        raise KimlikHatasi("kart_bicimi")
    an = time.time() if simdi is None else simdi
    if icerik["son"] <= an:
        raise KimlikHatasi("kart_suresi_dolmus")
    if icerik["son"] - an > EN_UZUN_SURE_S + 60:
        raise KimlikHatasi("kart_suresi_cok_uzun")
    rol = kullanicilar.get(icerik["k"])
    if rol is None:
        raise KimlikHatasi("kullanici_bilinmiyor")
    return Kimlik(kullanici=icerik["k"], rol=rol)


def metadan_kart(meta) -> object:
    """MCP isteğinin üst bilgisinden kartı çıkarır (yoksa None)."""
    if meta is None:
        return None
    if isinstance(meta, dict):
        return meta.get(META_ANAHTARI)
    return (meta.model_dump() if hasattr(meta, "model_dump") else {}).get(META_ANAHTARI)
