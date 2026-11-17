"""Cevap doğrulayıcı: modelin cevabındaki her sayı bu turdaki araç sonuçlarında geçiyor mu, her alıntı gerçek mi?

Sayılar:
- Cevap Türkçe biçimde okunur: "75.826.754,65" (nokta binlik, virgül ondalık). Noktanın ardından tam üç rakam
  gelen yazılış her zaman binlik sayılır ("55.865" = 55865). Araçların kendi biçimi ("48.1745", noktadan sonra üç
  rakam değil) ondalık okunur. "bin", "milyon", "milyar" çarpandır.
- Her sayının bir türü vardır; türü yanındaki kelime belirler: TL/USD/EUR/lira/dolar → tutar, % ya da "yüzde" →
  yüzde, gün → gün, ay → ay, fatura/müşteri/çek/hareket... → adet, takvim günü → tarih, 2025 gibi yıl → yıl.
  Türü belli olmayan sayı tutar, kur, gün, ay, hafta ya da adet türünden bir kaynakla eşleşebilir; yüzde ve tarih
  yalnızca kendi türüyle eşleşir. Böylece uydurulmuş "%12", "son 12 ay"daki 12 ile eşleşemez.
- Kaynaklar yalnızca şunlardır: bu turda başarılı dönen araç sonuçlarındaki değerler (alan adına göre türlenir),
  alan adlarındaki dönem kalıpları (son_12_ay → 12 ay, kur_yuzde_10 → %10, gecikme_31_60 → 31 ve 60 gün), listelerin
  uzunluğu, aracın gün ve satır sayısı argümanları, belge araması sonuçlarındaki madde metinleri, kullanıcının
  sorusu ve bugünün tarihi. Reddedilen çağrılar, açıklama ve not metinleri kaynak değildir.
- Eşleşme: kaynak değer cevapta gösterilen hassasiyete yuvarlanınca ya da kesilince cevaptaki sayıyı vermelidir
  (75.826.754,65 → "75,8 milyon", "75,82 milyon", "75.826.755" geçer; "75,81 milyon" geçmez). İşaret karşılaştırılmaz
  (çıkışlar eksi işaretsiz yazılabilir).
- Numaralı liste işaretleri ("1. ") ile harfe bitişik sayılar (F032047, KANUN-6102) sayı sayılmaz. Tırnak içindeki
  sayılara ve köşeli parantezdeki parça kimliklerine ([KANUN-6102#m1535]) bakılmaz; tırnaklı alıntıları alıntı
  kontrolü denetler. Belge sonuçlarında madde metni, belge adı ve madde başlığı metin olarak okunur; parça
  kimliğindeki kanun ve madde numarası da kaynaktır ("6102 sayılı Kanun", "1535. madde").

Alıntılar: Faz 5 alıntı kontrolü, yalnızca bu turda belge aramasının döndürdüğü madde metinlerine karşı.

Bilinen sınırlar:
- Sayının neye ait olduğuna bakılmaz: A müşterisinin tutarı B müşterisi için yazılırsa geçer (kör nokta).
- Kullanıcının sorusundaki sayılar kaynaktır: "DSO 59 gün mü?" sorusuna "Evet, 59 gün" cevabı geçer (ikinci kör nokta).
- Yalnızca rakamla yazılan sayılara bakılır: "yirmi yıl" denetlenmez (üçüncü kör nokta). Belge cevaplarında alıntı
  şart olmadığı için böyle bir cevap hiçbir kontrole takılmadan geçebilir.
- Çift tırnak yalnızca belge alıntısı içindir: parça kimliği olmayan tırnaklı metin "kimliksiz_alinti" olarak
  reddedilir (üç ya da daha çok kelimelik ya da rakam içeren tırnaklı bir müşteri adı da). Bu bilerek katıdır;
  gevşetilirse sayılar tırnak içine saklanabilirdi. Rakamsız, bir-iki kelimelik tırnaklı söz ('"kaç yıl" sorusuna')
  alıntı sayılmaz.
- Modelin kendi yaptığı işlemin sonucu (iki tutarın toplamı, bir oran) kaynaksız sayılır ve yakalanır.
- Küçük tam sayılar (3, 4) türü belli değilse birçok kaynakla kolayca eşleşir.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, localcontext

from sayman.belge.alinti import EN_AZ_KELIME, AlintiSonucu, cevabi_kontrol_et, tirnak_araliklari

AYLAR = ["ocak", "şubat", "mart", "nisan", "mayıs", "haziran", "temmuz", "ağustos", "eylül", "ekim", "kasım",
         "aralık"]
OLCEK = {"bin": Decimal(1000), "milyon": Decimal(10) ** 6, "milyar": Decimal(10) ** 9}
UYUM = {
    "tutar": {"tutar", "kur", "bilinmeyen"},
    "kur": {"kur", "tutar", "bilinmeyen"},
    "yuzde": {"yuzde"},
    "gun": {"gun", "bilinmeyen"},
    "ay": {"ay", "bilinmeyen"},
    "hafta": {"hafta", "adet", "bilinmeyen"},
    "yil": {"yil"},
    "adet": {"adet", "bilinmeyen"},
    "bilinmeyen": {"tutar", "kur", "gun", "ay", "hafta", "adet", "bilinmeyen"},
}
PARA = re.compile(r"^(tl|try|₺|lira\w*|usd|eur|gbp|dolar\w*|avro\w*|euro\w*|sterlin\w*|\$|€|£)$")
ADET = re.compile(r"^(fatura|müşteri|çek|hareket|işlem|adet|kalem|hesap|tahsilat|ödeme|tedarikçi|alacak|kayıt|"
                  r"sözleşme|madde|belge|satır)\w*$")
TUR_KELIMESI = [(PARA, "tutar"), (re.compile(r"^gün\w*$"), "gun"), (re.compile(r"^hafta\w*$"), "hafta"),
                (re.compile(r"^(ay|aya|ayda|aydan|aydır|aylık|ayın|ayı|aylar\w*|aylık\w*)$"), "ay"),
                (re.compile(r"^(yıl|sene)\w*$"), "yil"), (ADET, "adet")]
ONCEKI_PARA = re.compile(r"(?:[$€₺£]|\b(?:USD|EUR|GBP|TL|TRY))\s*$")

_ISO = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_NOKTALI = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)")
_AYLI = re.compile(r"(?<!\d)(\d{1,2})\s+(" + "|".join(a.capitalize() for a in AYLAR) + "|" + "|".join(AYLAR)
                   + r")(?:'\w+)?(?:\s+(\d{4})(?!\d))?")
_SAYI = re.compile(r"(?<![\w#.,])(?<![^\W\d_]-)(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+,\d+|\d+\.\d+|\d+)(?!\w)")
_PARCA_KIMLIGI = re.compile(r"\[[A-Za-z0-9_\-]+#m\d+\]")
_PARCA_NO = re.compile(r"^(?:KANUN-(\d+))?.*#m(\d+)$")  # kanun numarası (varsa) ve madde numarası
_OLCEK = re.compile(r"\s*(bin|milyon|milyar)\b")
_KELIME = re.compile(r"[^\s()\[\]:;,.!?\"“”«»]+")
_KENAR = re.compile(r"^[^\w₺$€£]+|[^\w₺$€£]+$")
_AY_ADI = "|".join(a.capitalize() for a in AYLAR) + "|" + "|".join(AYLAR)
_ARALIK = re.compile(r"(?<!\d)(\d{1,2})\s*[-–]\s*(\d{1,2})\s+(" + _AY_ADI + r")(?:'\w+)?(?:\s+(\d{4})(?!\d))?")
_DONEM = re.compile(r"(?:^|_)son_(\d+)_(gun|ay|yil|hafta)(?:_|$)")
_YUZDE_ANAHTAR = re.compile(r"(?:^|_)yuzde_(\d+)(?:_|$)")
_DILIM = re.compile(r"(?:^|_)gecikme_(\d+)_(\d+|ustu)(?:_|$)")
_ISO_TAM = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SAYI_METNI = re.compile(r"^-?\d+(?:\.\d+)?$")
_YILAN = re.compile(r"^[a-z0-9_]+$")


@dataclass(frozen=True)
class Sayi:
    metin: str
    bas: int
    bit: int
    tur: str
    deger: Decimal | None = None      # mutlak değer, çarpan uygulanmış
    birim: Decimal | None = None      # gösterilen son basamağın değeri
    tarih: dt.date | None = None
    gun_ay: tuple[int, int] | None = None   # yılsız tarih: (gün, ay)


@dataclass(frozen=True)
class Kaynak:
    tur: str
    alan: str
    cagri_id: str
    deger: Decimal | None = None
    tarih: dt.date | None = None


@dataclass(frozen=True)
class SayiSonucu:
    metin: str
    tur: str
    gecerli: bool
    kaynaklar: tuple[tuple[str, str], ...] = ()   # (cagri_id, alan)


@dataclass
class Dogrulama:
    sayilar: list[SayiSonucu] = field(default_factory=list)
    alintilar: list[AlintiSonucu] = field(default_factory=list)

    @property
    def kaynaksiz(self) -> list[SayiSonucu]:
        return [s for s in self.sayilar if not s.gecerli]

    @property
    def gecersiz_alintilar(self) -> list[AlintiSonucu]:
        return [a for a in self.alintilar if not a.gecerli]

    @property
    def gecerli(self) -> bool:
        return not self.kaynaksiz and not self.gecersiz_alintilar


# ---------------------------------------------------------------- metinden sayı okuma


def _kucult(metin: str) -> str:
    return metin.replace("I", "ı").replace("İ", "i").lower()


def _tarih(yil: int, ay: int, gun: int) -> dt.date | None:
    try:
        return dt.date(yil, ay, gun)
    except ValueError:
        return None


def _tarihler(metin: str) -> list[Sayi]:
    bulunan: list[Sayi] = []
    for m in _ISO.finditer(metin):
        bulunan.append(Sayi(m.group(0), m.start(), m.end(), "tarih",
                            tarih=_tarih(int(m.group(1)), int(m.group(2)), int(m.group(3)))))
    for m in _NOKTALI.finditer(metin):
        bulunan.append(Sayi(m.group(0), m.start(), m.end(), "tarih",
                            tarih=_tarih(int(m.group(3)), int(m.group(2)), int(m.group(1)))))
    for m in _AYLI.finditer(metin):
        ay = AYLAR.index(_kucult(m.group(2))) + 1
        if m.group(3):
            bulunan.append(Sayi(m.group(0), m.start(), m.end(), "tarih",
                                tarih=_tarih(int(m.group(3)), ay, int(m.group(1)))))
        else:
            bulunan.append(Sayi(m.group(0), m.start(), m.end(), "tarih", gun_ay=(int(m.group(1)), ay)))
    for m in _ARALIK.finditer(metin):  # "9-15 Eylül": iki tarih
        ay = AYLAR.index(_kucult(m.group(3))) + 1
        for grup, bit in ((1, m.end(1)), (2, m.end())):
            gun = int(m.group(grup))
            if m.group(4):
                bulunan.append(Sayi(metin[m.start(grup):bit], m.start(grup), bit, "tarih",
                                    tarih=_tarih(int(m.group(4)), ay, gun)))
            else:
                bulunan.append(Sayi(metin[m.start(grup):bit], m.start(grup), bit, "tarih", gun_ay=(gun, ay)))
    bulunan.sort(key=lambda s: (s.bas, -s.bit))
    sonuc, son = [], -1
    for s in bulunan:  # çakışanlardan ilk ve en uzunu
        if s.bas >= son:
            sonuc.append(s)
            son = s.bit
    return sonuc


def _deger(metin: str) -> tuple[Decimal, Decimal]:
    """Türkçe öncelikli okuma: (değer, son basamağın değeri)."""
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", metin):
        tam, _, kesir = metin.partition(",")
        tam = tam.replace(".", "")
    elif "," in metin:
        tam, _, kesir = metin.partition(",")
    elif "." in metin:
        tam, _, kesir = metin.partition(".")
    else:
        tam, kesir = metin, ""
    return Decimal(f"{tam}.{kesir}" if kesir else tam), Decimal(1).scaleb(-len(kesir))


def _sonraki_kelimeler(metin: str, konum: int, adet: int = 2) -> list[str]:
    kalan = metin[konum:]
    ek = re.match(r"['’]\w+", kalan)  # 3'ü, 30'u: sayıya bitişik ek
    if ek:
        kalan = kalan[ek.end():]
    satir = kalan.split("\n", 1)[0]
    kelimeler = []
    for k in _KELIME.findall(satir):
        k = _KENAR.sub("", k)  # "TL**" gibi biçim işaretleri
        if k:
            kelimeler.append(re.split(r"['’]", _kucult(k), maxsplit=1)[0])
        if len(kelimeler) == adet:
            break
    return kelimeler


def _tur(metin: str, bas: int, bit: int, deger_metni: str, olcekli: bool) -> str:
    once = metin[max(0, bas - 7):bas]
    if once.rstrip().endswith("%") or _kucult(once).rstrip().endswith("yüzde") or metin[bit:bit + 1] == "%":
        return "yuzde"
    if ONCEKI_PARA.search(once):
        return "tutar"
    for kelime in _sonraki_kelimeler(metin, bit):
        for kalip, tur in TUR_KELIMESI:
            if kalip.match(kelime):
                return tur
    if not olcekli and re.fullmatch(r"(19|20)\d\d", deger_metni):
        return "yil"
    return "bilinmeyen"


def metinden_sayilar(metin: str, atla: list[tuple[int, int]] = ()) -> list[Sayi]:
    """Türkçe bir metindeki sayılar ve tarihler. atla: bakılmayacak (başlangıç, bitiş) aralıkları."""
    def disarida(bas, bit):
        return not any(b <= bas and bit <= s for b, s in atla)

    sayilar = [t for t in _tarihler(metin) if disarida(t.bas, t.bit)]
    maske = list(metin)
    for t in _tarihler(metin):
        maske[t.bas:t.bit] = " " * (t.bit - t.bas)
    maskeli = "".join(maske)
    for m in _SAYI.finditer(maskeli):
        bas, bit = m.start(), m.end()
        if not disarida(bas, bit):
            continue
        satir_basi = maskeli.rfind("\n", 0, bas) + 1
        if not maskeli[satir_basi:bas].strip(" \t*#>-_") and maskeli[bit:bit + 2] in (". ", ") "):
            continue  # numaralı liste işareti (önünde "*", "-", "**" gibi biçim işaretleri olabilir)
        deger, birim = _deger(m.group(1))
        olcek = _OLCEK.match(maskeli, bit)
        son = bit
        if olcek:
            carpan = OLCEK[olcek.group(1)]
            deger, birim, son = deger * carpan, birim * carpan, olcek.end()
        tur = _tur(maskeli, bas, son, m.group(1), bool(olcek))
        if tur == "adet" and deger != deger.to_integral_value():
            tur = "bilinmeyen"  # kesirli sayı adet olamaz ("43.510.100,56 TL ödeme")
        if tur == "tutar" and deger == 1 and not olcek and re.match(r"\s*\S+\s*=", maskeli[son:]):
            continue  # "1 USD = 48,17 TL": birim, iddia değil
        yuzde_bas = bas - 1 if maskeli[bas - 1:bas] == "%" else bas
        sayilar.append(Sayi(metin[yuzde_bas:son], yuzde_bas, son, tur, deger, birim))
    return sorted(sayilar, key=lambda s: s.bas)


# ---------------------------------------------------------------- kaynaklar


def _anahtar_kaliplari(ad: str, cagri_id: str) -> list[Kaynak]:
    k = []
    for m in _DONEM.finditer(ad):
        k.append(Kaynak(m.group(2), ad, cagri_id, Decimal(m.group(1))))
    for m in _YUZDE_ANAHTAR.finditer(ad):
        k.append(Kaynak("yuzde", ad, cagri_id, Decimal(m.group(1))))
    for m in _DILIM.finditer(ad):
        k.append(Kaynak("gun", ad, cagri_id, Decimal(m.group(1))))
        if m.group(2) != "ustu":
            k.append(Kaynak("gun", ad, cagri_id, Decimal(m.group(2))))
    return k


def _degerlerden(deger, alan: str, cagri_id: str, kaynaklar: list[Kaynak]) -> None:
    if isinstance(deger, dict):
        for k, v in deger.items():
            kaynaklar.extend(_anahtar_kaliplari(k, cagri_id))
            _degerlerden(v, k, cagri_id, kaynaklar)
    elif isinstance(deger, list):
        kaynaklar.append(Kaynak("adet", f"{alan}[]", cagri_id, Decimal(len(deger))))
        for v in deger:
            _degerlerden(v, alan, cagri_id, kaynaklar)
    elif isinstance(deger, bool) or deger is None:
        return
    elif isinstance(deger, int):
        tur = "gun" if "gun" in alan else ("hafta" if alan == "hafta" else "adet")
        kaynaklar.append(Kaynak(tur, alan, cagri_id, abs(Decimal(deger))))
    elif isinstance(deger, str):
        if _ISO_TAM.match(deger):
            t = dt.date.fromisoformat(deger)
            kaynaklar.extend([Kaynak("tarih", alan, cagri_id, tarih=t), Kaynak("yil", alan, cagri_id, Decimal(t.year))])
        elif _SAYI_METNI.match(deger):
            tur = "kur" if alan in ("kur", "alis") else ("gun" if "gun" in alan else "tutar")
            kaynaklar.append(Kaynak(tur, alan, cagri_id, abs(Decimal(deger))))
        elif _YILAN.match(deger):  # "son_90_gun" gibi seçenek değerleri
            kaynaklar.extend(_anahtar_kaliplari(deger, cagri_id))


def _metinden_kaynaklar(metin: str, alan: str, cagri_id: str) -> list[Kaynak]:
    k = []
    for s in metinden_sayilar(metin):
        if s.tur == "tarih":
            if s.tarih:
                k.extend([Kaynak("tarih", alan, cagri_id, tarih=s.tarih),
                          Kaynak("yil", alan, cagri_id, Decimal(s.tarih.year))])
        else:
            k.append(Kaynak(s.tur, alan, cagri_id, s.deger))
    return k


def _argumanlardan(argumanlar: dict, cagri_id: str) -> list[Kaynak]:
    k = []
    for ad, v in argumanlar.items():
        if isinstance(v, int) and not isinstance(v, bool):
            k.append(Kaynak("gun" if "gun" in ad else "adet", f"arguman:{ad}", cagri_id, abs(Decimal(v))))
        elif isinstance(v, str) and _ISO_TAM.match(v):
            t = dt.date.fromisoformat(v)
            k.extend([Kaynak("tarih", f"arguman:{ad}", cagri_id, tarih=t),
                      Kaynak("yil", f"arguman:{ad}", cagri_id, Decimal(t.year))])
        elif isinstance(v, str) and _YILAN.match(v):
            k.extend(_anahtar_kaliplari(v, cagri_id))
    return k


def _belge_kaynaklari(sonuc: dict, cagri_id: str) -> list[Kaynak]:
    """Belge araması: madde metni, belge adı ve madde başlığı Türkçe metin olarak okunur; parça kimliğinden kanun ve
    madde numarası gelir (KANUN-6102#m1535 → 6102 ve 1535)."""
    k = []
    for p in sonuc.get("sonuclar", []):
        for alan in ("metin", "belge", "madde"):
            if isinstance(p.get(alan), str):
                k += _metinden_kaynaklar(p[alan], alan, cagri_id)
        m = _PARCA_NO.search(p.get("parca_id", ""))
        if m:
            k += [Kaynak("adet", "parca_id", cagri_id, Decimal(n)) for n in m.groups() if n]
    return k


def kaynaklar(soru: str, cagrilar: list[dict], bugun: dt.date) -> list[Kaynak]:
    """cagrilar: {"arac", "argumanlar", "hata_mi", "yanit"} sözlükleri; yalnızca başarılı olanlar kaynak olur."""
    k = _metinden_kaynaklar(soru, "soru", "soru")
    k += [Kaynak("tarih", "bugun", "bugun", tarih=bugun), Kaynak("yil", "bugun", "bugun", Decimal(bugun.year))]
    for c in cagrilar:
        if c.get("hata_mi"):
            continue
        y = c["yanit"]
        cid = y["cagri_id"]
        if c["arac"] == "belge_ara":
            k += _belge_kaynaklari(y.get("sonuc") or {}, cid)
        else:
            _degerlerden(y.get("sonuc"), "sonuc", cid, k)
        if y.get("tarih") and _ISO_TAM.match(y["tarih"]):
            t = dt.date.fromisoformat(y["tarih"])
            k += [Kaynak("tarih", "tarih", cid, tarih=t), Kaynak("yil", "tarih", cid, Decimal(t.year))]
        k += _argumanlardan(c.get("argumanlar") or {}, cid)
    return k


# ---------------------------------------------------------------- eşleştirme


def _eslesir(s: Sayi, k: Kaynak) -> bool:
    if s.tur == "tarih":
        if k.tur != "tarih" or k.tarih is None:
            return False
        if s.tarih:
            return s.tarih == k.tarih
        return s.gun_ay == (k.tarih.day, k.tarih.month)
    if k.tur not in UYUM[s.tur] or k.deger is None:
        return False
    with localcontext() as c:
        c.prec = 50
        oran = k.deger / s.birim
        hedef = s.deger / s.birim
        return hedef in (oran.quantize(Decimal(1), ROUND_HALF_UP), oran.quantize(Decimal(1), ROUND_FLOOR))


def dogrula(cevap: str, soru: str, cagrilar: list[dict], bugun: dt.date) -> Dogrulama:
    kaynak = kaynaklar(soru, cagrilar, bugun)
    sonuc = Dogrulama()
    atla = tirnak_araliklari(cevap) + [(m.start(), m.end()) for m in _PARCA_KIMLIGI.finditer(cevap)]
    for s in metinden_sayilar(cevap, atla):
        bulunan = tuple(dict.fromkeys((k.cagri_id, k.alan) for k in kaynak if _eslesir(s, k)))
        sonuc.sayilar.append(SayiSonucu(s.metin, s.tur, bool(bulunan), bulunan))
    metinler = {}
    for c in cagrilar:
        if not c.get("hata_mi") and c["arac"] == "belge_ara":
            for p in c["yanit"]["sonuc"].get("sonuclar", []):
                metinler[p["parca_id"]] = p["metin"]
    # Rakamsız ve üç kelimeden kısa tırnaklı söz ('"kaç yıl" sorusuna') alıntı sayılmaz: içinde sayı saklanamaz,
    # alıntı olarak da zaten geçersiz olurdu (cok_kisa).
    sonuc.alintilar = [a for a in cevabi_kontrol_et(cevap, metinler)
                       if not (a.neden == "kimliksiz_alinti" and not any(ch.isdigit() for ch in a.alinti)
                               and len(a.alinti.split()) < EN_AZ_KELIME)]
    return sonuc
