"""Kâhin model ve negatif kontroller: düzeneğin kendisini sınamak için sahte modeller.

Kâhin her sorunun cevabını bilir: önce soru setindeki aracı beklenen argümanlarla çağırır, sonra beklenen değerleri
Türkçe biçimde yazar. Gerçek orkestratörden, kapıdan ve doğrulayıcıdan geçer; düzenek doğruysa her metrikte tam puan
alır. Belge sorularında kâhinin arayıcısı doğru maddeyi getirir (KahinArayici); kâhin maddeden anahtar değerleri
taşıyan bir bölümü alıntılar.

Negatif kontroller kâhinin bozulmuş hâlleridir; her biri kendi metriğinde kaybetmelidir:
- yanlis_arac: soruyla ilgisiz izinli bir aracı çağırır ve onun sayısını yazar → araç seçimi ve rakam doğruluğu düşer.
- bir_hane: beklenen değerin bir hanesini değiştirir → doğrulayıcı reddeder, rakam doğruluğu düşer.
- hep_ret: hiç araç çağırmadan "veremem" der → rakam doğruluğu ve belge cevabı düşer (sızıntı yoktur).
- baska_musteri: müşteri sorularında aynı aracı başka bir müşteri için çağırır, onun değerini sorulan müşteriye
  yazar → doğrulayıcı geçirir (bilinen kör nokta), puanlayıcı yanlış sayar.
- yaziyla: beklenen değerden farklı bir sayıyı yazıyla yazar ("yetmiş yedi milyon") → doğrulayıcı geçirir (yazıyla
  sayılara bakmaz), puanlayıcı yanlış sayar.
- yanlis_birim: tutarları doğru sayıyla ama yanlış para birimiyle yazar (EUR tutarına "TL", TL tutarına "EUR") →
  doğrulayıcı geçirir (birime bakmaz), puanlayıcı tutarlı sorularda yanlış sayar. Geliştirme setinde gerçek modelin
  bir cevabında bulundu ("383.072,08 TL", beklenen EUR).
- uydurma_kimlik (Faz 8b kimlik ölçümü): aracı doğru çağırır ama cevaba araç sonucunda olmayan bir kayıt numarası
  yazar. Biçim soru kimliğine göre döner: sistem biçimi (F09xxxx), tireli (F-2026-xxx), harf-rakam karışık (TA3B7C1D9
  gibi). Doğruluk bilindiği için kimlik analizinin etiketlerini ve "hangi kural durdurdu" dökümünü sınar.

Kâhin, kimlik setinin "kimlik_yok" sorularında (istenen numara araç sonucunda yok) numara vermez, bunu söyler.
"""

from __future__ import annotations

import json
import re
import zlib
from decimal import Decimal

from sayman.assistant.orchestrator import ModelCevabi
from sayman.documents.library import gorebilir
from sayman.evaluation.scoring import yol_al
from sayman.evaluation.value_formats import duz_sayi, kucult, sayi_yaziyla, tr_sayi, tr_tarih

KONTROLLER = ("yanlis_arac", "bir_hane", "hep_ret", "baska_musteri", "yaziyla", "yanlis_birim",
              "uydurma_kimlik")
UYDURMA_BICIMLERI = ("sistem", "tireli", "karisik")
_YEDEK_ARACLAR = [("doviz_kuru", {"para_birimi": "USD"}, ["alis"], "kur"),
                  ("dso_hesapla", {"donem": "son_30_gun"}, ["dso_gun"], "gun")]


def _birim(b) -> str:
    return "TL" if b in (None, "TL", "TRY") else b


def deger_yaz(a: dict) -> str:
    tur, d = a["tur"], a["deger"]
    if tur in ("tutar", "doviz"):
        return f"{tr_sayi(abs(Decimal(str(d))), 2)} {_birim(a.get('birim'))}"
    if tur == "kur":
        hane = max(-Decimal(str(d)).as_tuple().exponent, 0)
        return f"{duz_sayi(d, hane, ',')} TL"
    if tur == "gun":
        x = Decimal(str(d))
        return f"{duz_sayi(x, 0) if x == x.to_integral_value() else duz_sayi(x, 2, ',')} gün"
    if tur == "adet":
        return str(int(d))
    if tur == "tarih":
        return tr_tarih(d)
    return str(d)


def _bir_hane_degistir(metin: str) -> str:
    """İlk rakam grubunun son rakamını değiştirir: '75.826.754,65 TL' → '75.826.755,65 TL'."""
    m = re.search(r"\d(?=\D|$)", metin)
    if not m:
        return metin
    r = str((int(m.group()) + 1) % 10)
    return metin[:m.start()] + r + metin[m.end():]


def _yaziyla_yanlis(a: dict) -> str:
    """Beklenen değerden farklı (bir büyük) bir sayının yazıyla yazılışı."""
    v = abs(Decimal(str(a["deger"])))
    if v >= 10 ** 6:
        n, olcek = int(v // 10 ** 6) + 1, " milyon"
    elif v >= 1000:
        n, olcek = int(v // 1000) + 1, " bin"
    else:
        n, olcek = int(v) + 1, ""
    if n >= 1000:
        n = 999
    return f"yaklaşık {sayi_yaziyla(max(n, 2))}{olcek}"


class KahinArayici:
    """Belge sorularında doğru maddeyi getiren arayıcı (rol izin veriyorsa); başka sorularda hiçbir şey getirmez."""

    def __init__(self, parcalar: dict):
        self.parca = parcalar
        self.soru: dict | None = None

    def ara(self, sorgu: str, rol: str, k: int = 3, yontem: str = "anlam"):
        pid = (self.soru or {}).get("dogru_parca") or (self.soru or {}).get("gizli_parca")
        p = self.parca.get(pid)
        return [(p, 1.0)] if p is not None and gorebilir(rol, p.roller) else []


def _anahtar_konumlari(kelimeler: list[str], a: dict) -> list[int]:
    """Anahtarın madde kelimeleri içindeki konumları: rakamla ("120", "%2,25", "50.000") ya da yazıyla ("on iki")."""
    k = [kucult(x).strip(".,;:()%\"'") for x in kelimeler]
    if a["tur"] == "tarih":
        yil = a["deger"][:4]
        return [i for i, x in enumerate(k) if yil in x]
    if a["tur"] == "metin":  # kelimeler ek alabilir ("genel müdürün")
        aranan = kucult(a["deger"]).split()
        return [i for i in range(len(k) - len(aranan) + 1)
                if all(k[i + j].startswith(w) for j, w in enumerate(aranan))]
    d = a["deger"]
    konum = [i for i, x in enumerate(k) if x.replace(".", "").replace(",", ".") == d]
    n = Decimal(d)
    if not konum and n == n.to_integral_value() and 1 < n < 1000:
        yazi = sayi_yaziyla(int(n)).split()
        konum = [i for i in range(len(k)) if k[i:i + len(yazi)] == yazi]
    return konum


def alinti_penceresi(metin: str, anahtarlar: list[dict], pay: int = 3) -> str:
    """Madde metninden, bütün anahtarların ilk geçtiği yerleri kapsayan bir kelime penceresi (iki yanda `pay` kelime).
    Pencere maddede aynen geçer; çift tırnak içeren kelimede kesilir."""
    kelimeler = metin.split()
    konumlar = [min(k) for a in anahtarlar if (k := _anahtar_konumlari(kelimeler, a))]
    bas = max(min(konumlar, default=0) - pay, 0)
    bit = min(max(konumlar, default=0) + pay + 1, len(kelimeler))
    if bit - bas < 4:
        bit = min(bas + 4, len(kelimeler))
    secili = []
    for x in kelimeler[bas:bit]:
        if '"' in x:
            break
        secili.append(x)
    return " ".join(secili).rstrip(" .,;:")


def _sayisiz_pencere(metin: str, anahtarlar: list[dict]) -> str:
    """Rakam ve anahtar sayı kelimesi içermeyen dört kelimelik bir pencere (yaziyla kontrolü için)."""
    kelimeler = metin.split()
    yasak = {w for a in anahtarlar if a["tur"] == "sayi" and Decimal(a["deger"]) == Decimal(a["deger"]).to_integral_value()
             and 1 < Decimal(a["deger"]) < 1000 for w in sayi_yaziyla(int(Decimal(a["deger"]))).split()}
    metinler = [kucult(a["deger"]).split()[0] for a in anahtarlar if a["tur"] == "metin"]
    for i in range(len(kelimeler) - 3):
        p = kelimeler[i:i + 4]
        if not any(c.isdigit() or c == '"' for w in p for c in w) and not any(kucult(w).strip(".,;:") in yasak for w in p) \
                and not any(kucult(w).startswith(m) for w in p for m in metinler):
            return " ".join(p).rstrip(" .,;:")
    return " ".join(kelimeler[:4]).rstrip(" .,;:")


def uydurma_kimlik(soru_id: str) -> tuple[str, str]:
    """(biçim, numara): veritabanında olmayan bir kayıt numarası; biçim soru kimliğine göre döner."""
    crc = zlib.crc32(soru_id.encode("utf-8"))
    bicim = UYDURMA_BICIMLERI[crc % 3]
    if bicim == "sistem":  # fatura numaraları F041314'e kadar; F09xxxx yok
        return bicim, f"F09{crc % 10000:04d}"
    if bicim == "tireli":
        return bicim, f"F-2026-{crc % 1000:03d}"
    # harf ve tek rakam sırayla: yeni kuralın kalıbına (harf + en az 3 rakam) uymaz
    return bicim, "T" + "".join("ABCDEF"[(crc >> (4 * i)) % 6] if i % 2 == 0 else str((crc >> (4 * i)) % 10)
                                for i in range(8))


class Kahin:
    """Asistan'a model olarak verilir. Çalıştırıcı her sorudan önce `soru` alanını soru setindeki kayıtla doldurur."""

    def __init__(self, kontrol: str | None = None, parcalar: dict | None = None):
        if kontrol not in (None, *KONTROLLER):
            raise ValueError(f"bilinmeyen kontrol: {kontrol}")
        self.kontrol = kontrol
        self.parca = parcalar or {}
        self.soru: dict | None = None

    # -- model arayüzü -----------------------------------------------------------------------------

    def __call__(self, mesajlar: list[dict], araclar: list[dict], zaman_asimi: float) -> ModelCevabi:
        s = self.soru
        sonuclar = [json.loads(m["content"]) for m in mesajlar if m["role"] == "tool"]
        if not sonuclar and not any(m["role"] == "assistant" for m in mesajlar):
            cagrilar = self._cagrilar(s)
            if cagrilar:
                return ModelCevabi(arac_cagrilari=cagrilar)
        return ModelCevabi(icerik=self._cevap(s, sonuclar))

    # -- araç çağrıları ------------------------------------------------------------------------------

    def _cagrilar(self, s: dict) -> list[tuple[str, dict]]:
        if self.kontrol == "hep_ret":
            return []
        if self.kontrol == "yanlis_arac":
            return [self._yedek(s)[:2]]
        if s["bolum"] == "arac":
            a = s["alternatifler"][0]
            argumanlar = dict(a["argumanlar"])
            if self.kontrol == "baska_musteri" and s.get("diger_musteri") and "musteri_adi" in argumanlar:
                argumanlar["musteri_adi"] = s["diger_musteri"]
            return [(a["arac"], argumanlar)]
        if s["bolum"] == "belge":
            return [("belge_ara", {"sorgu": s["soru"][:300]})]
        return [tuple(s["kahin_cagri"])] if s.get("kahin_cagri") and s["kahin_cagri"][0] else []

    def _yedek(self, s: dict):
        beklenen = {a["arac"] for a in s.get("alternatifler", [])}
        return next(y for y in _YEDEK_ARACLAR if y[0] not in beklenen)

    # -- cevap -----------------------------------------------------------------------------------------

    def _cevap(self, s: dict, sonuclar: list[dict]) -> str:
        if self.kontrol == "hep_ret":
            return "Bu bilgiyi veremem."
        if self.kontrol == "uydurma_kimlik":
            return f"Kayıt numarası: {uydurma_kimlik(s['soru_id'])[1]}."
        if self.kontrol == "yanlis_arac":
            arac, _, yol, tur = self._yedek(s)
            sonuc = next((r["sonuc"] for r in sonuclar if r.get("arac") == arac and "sonuc" in r), None)
            if sonuc is None:
                return "Sonuç alınamadı."
            return f"Sonuç: {deger_yaz({'tur': tur, 'deger': yol_al(sonuc, yol)})}."
        if s["bolum"] == "arac":
            return self._arac_cevabi(s, sonuclar)
        if s["bolum"] == "belge":
            return self._belge_cevabi(s, sonuclar)
        if s["tur"] == "belge_yetkisi":
            return "Bu konuda erişebildiğim bir belge bulamadım."
        if s["tur"] == "kimlik_yok":
            return "Araç sonuçlarında istenen numara yok; bu yüzden numara veremiyorum."
        return "Bu bilgi yetki alanımın dışında; size gösteremiyorum."

    def _arac_cevabi(self, s: dict, sonuclar: list[dict]) -> str:
        alt = s["alternatifler"][0]
        anahtarlar = alt["anahtarlar"]
        if self.kontrol == "baska_musteri" and s.get("diger_musteri"):
            sonuc = next((r["sonuc"] for r in sonuclar if r.get("arac") == alt["arac"] and "sonuc" in r), None)
            if sonuc is None or sonuc.get("bulunamadi") or sonuc.get("birden_fazla"):
                return "Sonuç alınamadı."
            try:
                anahtarlar = [{**a, "deger": yol_al(sonuc, a["yol"])} for a in anahtarlar]
            except (KeyError, IndexError, StopIteration, TypeError):  # öteki müşteride bu kayıt yok
                return "Sonuç alınamadı."
            if any(a["deger"] is None for a in anahtarlar):  # öteki müşteride bu alan boş (ör. hiç ödemesi yok)
                return "Sonuç alınamadı."
        degerler = [deger_yaz(a) for a in anahtarlar]
        if self.kontrol == "yanlis_birim":  # tutarın sayısı doğru, para birimi yanlış
            degerler = [f"{tr_sayi(abs(Decimal(str(a['deger']))), 2)} {'EUR' if _birim(a.get('birim')) == 'TL' else 'TL'}"
                        if a["tur"] in ("tutar", "doviz") else d for a, d in zip(anahtarlar, degerler)]
        if self.kontrol == "bir_hane":  # etiketteki sayıya ("3. haftanın") değil değere dokunulur
            sayili = next((i for i, a in enumerate(anahtarlar) if a["tur"] != "ad"), 0)
            degerler[sayili] = _bir_hane_degistir(degerler[sayili])
        satirlar = [f"{a['etiket']}: {d}" for a, d in zip(anahtarlar, degerler)]
        if self.kontrol == "yaziyla":
            sayili = next((a for a in anahtarlar if a["tur"] in ("tutar", "doviz", "adet", "gun", "kur")), None)
            if sayili is None:
                return "Kayıt bulunamadı."
            return f"{sayili['etiket']}: {_yaziyla_yanlis(sayili)}."
        return "\n".join(satirlar)

    def _belge_cevabi(self, s: dict, sonuclar: list[dict]) -> str:
        getirilen = {x["parca_id"]: x["metin"] for r in sonuclar for x in (r.get("sonuc") or {}).get("sonuclar", [])}
        pid = s["dogru_parca"]
        if pid not in getirilen:
            return "İlgili bir madde bulamadım."
        metin = getirilen[pid]
        if self.kontrol == "yaziyla":
            sayi = next((a for a in s["anahtarlar"] if a["tur"] == "sayi"), None)
            n = int(Decimal(sayi["deger"])) + 1 if sayi else 2
            return f"Süre {sayi_yaziyla(min(max(n, 2), 999))} gündür. \"{_sayisiz_pencere(metin, s['anahtarlar'])}\" [{pid}]"
        pencere = alinti_penceresi(metin, s["anahtarlar"])
        # Beklenen değerin rakamları maddede rakamla geçiyorsa cevap onu da yazar; geçmiyorsa ("12" ama maddede
        # "on iki") yalnızca alıntı verilir: maddede olmayan bir rakam doğrulayıcıdan geçmez.
        rakamlar = re.findall(r"\d+(?:[.,]\d+)*", s["deger"])
        ozet = s["deger"] + ". " if all(r in metin for r in rakamlar) else "Maddeye göre: "
        cevap = f"{ozet}\"{pencere}\" [{pid}]"
        if self.kontrol == "bir_hane":
            cevap = _bir_hane_degistir(cevap)
        return cevap
