"""Para tutarları: hesap Decimal ile yapılır, saklama tamsayı kuruşla (1/100 birim).

Kayan noktalı sayı kullanılmaz; "bakiye = açılış + hareketler" eşitliği birebir tutmalıdır.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

KURUS = Decimal("0.01")


def ondalik(deger) -> Decimal:
    """Metin, tamsayı ya da Decimal'i Decimal'e çevirir. float kabul edilmez."""
    if isinstance(deger, float):
        raise TypeError("float para tutarı olarak kabul edilmez; metin ya da Decimal verin")
    if isinstance(deger, Decimal):
        return deger
    return Decimal(str(deger).strip())


def kurusa(tutar: Decimal) -> int:
    """Decimal tutarı en yakın kuruşa (eşitlikte çift tarafa) yuvarlayıp tamsayı kuruş döner."""
    return int((tutar.quantize(KURUS, rounding=ROUND_HALF_EVEN) * 100).to_integral_value())


def kurustan(kurus: int) -> Decimal:
    return (Decimal(kurus) / 100).quantize(KURUS)


def oran_uygula(kurus: int, oran: Decimal) -> int:
    """Kuruş tutarına bir oran uygular, sonucu yine tamsayı kuruş olarak döner."""
    return kurusa(kurustan(kurus) * oran)
