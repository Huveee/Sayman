"""Alıntı kontrolü: modelin bir belgeden yaptığı alıntı, gösterdiği parçada gerçekten geçiyor mu?

Model cevabında alıntıyı tırnak içinde, hemen ardından parça kimliğini köşeli parantezde verir:

    Vade 30 gündür: "fatura tarihinden itibaren 30 gün içinde banka havalesiyle öder" [SOZ-S001#m3]

Karşılaştırmadan önce yalnızca üç şey eşitlenir: boşluklar (birden çok boşluk ve satır sonu tek boşluk sayılır),
tırnak ve kesme işaretleri (’ ‘ ” “ « » hepsi düz işarete döner) ve büyük/küçük harf (Türkçe kurala göre: I→ı,
İ→i). Rakamlara, noktalamaya ve kelimelere dokunulmaz: "%12" ile "%1,2" farklıdır, "30 gün" ile "otuz gün" farklıdır.

Alıntı metinde kelime ve sayı sınırlarında geçmelidir: bir sayının ya da kelimenin ortasından başlayan veya
ortasında biten eşleşme sayılmaz ("20 günü", "120 günü" içinde geçmiş sayılmaz).

Alıntının ortasında "..." ya da "…" varsa parçalar ayrı ayrı ve aynı sırayla aranır.

Biçime uymayan atıflar da geçersiz sayılır: tırnaklı bir alıntı olmadan verilen parça kimliği ("alintisiz_atif";
tek tırnakla yazılan alıntılar da buraya düşer, çünkü Türkçede tek tırnak kesme işaretiyle karışır) ve arkasından
parça kimliği gelmeyen tırnaklı alıntı ("kimliksiz_alinti").

Bilinen sınır: bu kontrol alıntının metinde geçtiğini doğrular, iddiayı desteklediğini doğrulamaz. Gerçek ama
konuyla ilgisiz bir alıntı geçer.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

EN_AZ_KELIME = 3
_TIRNAK = str.maketrans({"’": "'", "‘": "'", "‚": "'", "`": "'", "´": "'", "”": '"', "“": '"', "„": '"', "«": '"',
                         "»": '"'})
_ATIF = re.compile(r'["“”«»]([^"“”«»]+)["“”«»]\s*\[([A-Za-z0-9_\-]+#m\d+)\]')
_KIMLIK = re.compile(r"\[([A-Za-z0-9_\-]+#m\d+)\]")
_TIRNAKLI = re.compile(r'["“”«»]([^"“”«»]+)["“”«»]')
_ATLAMA = re.compile(r"\s*(?:\.\.\.|…)\s*")


def normalle(metin: str) -> str:
    metin = unicodedata.normalize("NFC", metin).translate(_TIRNAK)
    metin = metin.replace("I", "ı").replace("İ", "i").lower()
    return " ".join(metin.split())


@dataclass(frozen=True)
class AlintiSonucu:
    parca_id: str
    alinti: str
    gecerli: bool
    neden: str                       # "tamam", "bilinmeyen_parca", "cok_kisa", "metinde_yok", "baska_parcada",
                                     # "alintisiz_atif", "kimliksiz_alinti"
    bulundugu: tuple[str, ...] = ()  # alıntı başka parçalarda geçiyorsa onların kimlikleri


def _parcalari(alinti: str) -> list[str]:
    parcalar = [normalle(p).strip(" .,;:") for p in _ATLAMA.split(alinti.strip().strip("\"'“”«»"))]
    return [p for p in parcalar if p]


def _sinirda(metin: str, bas: int, bit: int) -> bool:
    """Eşleşme bir kelimenin ya da sayının ortasından başlamıyor ve ortasında bitmiyor mu?

    "20 günü" alıntısı "120 günü" içinde, "50.000 TL" alıntısı "250.000 TL" içinde, "%2" alıntısı "%2,25" içinde
    geçmiş sayılmaz."""
    ilk, son = metin[bas], metin[bit - 1]
    if bas > 0:
        once = metin[bas - 1]
        if (once.isalnum() and ilk.isalnum()) or (
                ilk.isdigit() and once in ".," and bas > 1 and metin[bas - 2].isdigit()):
            return False
    if bit < len(metin):
        sonra = metin[bit]
        if (sonra.isalnum() and son.isalnum()) or (
                son.isdigit() and sonra in ".," and bit + 1 < len(metin) and metin[bit + 1].isdigit()):
            return False
    return True


def _geciyor(parcalar: list[str], metin: str) -> bool:
    """Parçalar metinde bu sırayla, her biri kelime ve sayı sınırlarında geçiyor mu?"""
    konum = 0
    for p in parcalar:
        bulunan = metin.find(p, konum)
        while bulunan >= 0 and not _sinirda(metin, bulunan, bulunan + len(p)):
            bulunan = metin.find(p, bulunan + 1)
        if bulunan < 0:
            return False
        konum = bulunan + len(p)
    return True


def kontrol_et(alinti: str, parca_id: str, metinler: dict[str, str]) -> AlintiSonucu:
    """metinler: parça kimliği -> maddenin kendi metni (aranan metin değil)."""
    if parca_id not in metinler:
        return AlintiSonucu(parca_id, alinti, False, "bilinmeyen_parca")
    parcalar = _parcalari(alinti)
    if sum(len(p.split()) for p in parcalar) < EN_AZ_KELIME:
        return AlintiSonucu(parca_id, alinti, False, "cok_kisa")
    if _geciyor(parcalar, normalle(metinler[parca_id])):
        return AlintiSonucu(parca_id, alinti, True, "tamam")
    baska = tuple(sorted(pid for pid, m in metinler.items() if pid != parca_id and _geciyor(parcalar, normalle(m))))
    return AlintiSonucu(parca_id, alinti, False, "baska_parcada" if baska else "metinde_yok", baska)


def tirnak_araliklari(metin: str) -> list[tuple[int, int]]:
    """Metindeki tırnaklı bölümlerin (başlangıç, bitiş) konumları; sayı doğrulayıcı bunların içine bakmaz."""
    return [(m.start(), m.end()) for m in _TIRNAKLI.finditer(metin)]


def atiflar(cevap: str) -> list[tuple[str, str]]:
    """Cevaptaki (alıntı, parça kimliği) çiftleri, geçtikleri sırayla."""
    return [(m.group(1), m.group(2)) for m in _ATIF.finditer(cevap)]


def cevabi_kontrol_et(cevap: str, metinler: dict[str, str]) -> list[AlintiSonucu]:
    """Cevaptaki her atıf ve biçime uymayan her kimlik ya da tırnaklı alıntı için bir sonuç, metindeki sırasıyla.

    Hiç atıf içermeyen bir cevap için boş liste döner; belgeye dayanması gereken bir cevabın atıfsız olup olmadığına
    çağıran karar verir."""
    sonuc, atif_basi, kimlik_basi = [], set(), set()
    for m in _ATIF.finditer(cevap):
        sonuc.append((m.start(), kontrol_et(m.group(1), m.group(2), metinler)))
        atif_basi.add(m.start())
        kimlik_basi.add(m.start(2) - 1)
    for m in _KIMLIK.finditer(cevap):
        if m.start() not in kimlik_basi:
            sonuc.append((m.start(), AlintiSonucu(m.group(1), "", False, "alintisiz_atif")))
    for m in _TIRNAKLI.finditer(cevap):
        if m.start() not in atif_basi:
            sonuc.append((m.start(), AlintiSonucu("", m.group(1), False, "kimliksiz_alinti")))
    return [s for _, s in sorted(sonuc, key=lambda x: x[0])]
