"""Sentetik şirket üretici: gerçek UCI faturalarının üzerine kurulan, tohumla tekrarlanabilir bir Türk toptancı.

Gerçek olan: fatura kalemleri ve tarihleri (bütün haftalar kadar ileri kaydırılmış), müşteri ayrımı,
iptal/iade faturaları (UCI) ve döviz kurları (TCMB). Sentetik olan: müşteri ve tedarikçi adları, vadeler,
tahsilat ve ödeme davranışı, çekler, POS, banka hesapları ve hareketleri.

Kurallar:
- Para tamsayı kuruş; hesap Decimal ile.
- Her bölüm kendi rastgele akışını kullanır (tohum + bölüm adı); sistem saati okunmaz.
- Banka hareketleri en son, tahsilat ve ödeme kayıtlarından türetilir. Olaylar bu kayıtların kopyası
  üzerinde uygulanır; olaylar kapalıyken temel veri birebir aynı kalır.
"""

from __future__ import annotations

import copy
import datetime as dt
import heapq
import random
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from sayman.data.exchange_rates import Kurlar
from sayman.data.money import kurusa, kurustan, oran_uygula
from sayman.data.uci import Kalem

GUN = dt.timedelta(days=1)

AVRO_ULKELERI = {
    "EIRE", "Germany", "France", "Netherlands", "Spain", "Belgium", "Portugal", "Italy", "Finland",
    "Cyprus", "Austria", "Greece", "Malta", "Lithuania", "European Community",
}
ULKE_TR = {
    "United Kingdom": "Türkiye", "EIRE": "İrlanda", "Germany": "Almanya", "France": "Fransa",
    "Netherlands": "Hollanda", "Spain": "İspanya", "Belgium": "Belçika", "Switzerland": "İsviçre",
    "Portugal": "Portekiz", "Australia": "Avustralya", "Norway": "Norveç", "Italy": "İtalya",
    "Channel Islands": "Manş Adaları", "Finland": "Finlandiya", "Cyprus": "Kıbrıs", "Sweden": "İsveç",
    "Austria": "Avusturya", "Denmark": "Danimarka", "Japan": "Japonya", "Poland": "Polonya",
    "USA": "ABD", "Israel": "İsrail", "Greece": "Yunanistan", "Malta": "Malta", "Canada": "Kanada",
    "Lithuania": "Litvanya", "Iceland": "İzlanda", "Singapore": "Singapur", "Brazil": "Brezilya",
    "United Arab Emirates": "Birleşik Arap Emirlikleri", "Saudi Arabia": "Suudi Arabistan",
    "Bahrain": "Bahreyn", "Lebanon": "Lübnan", "South Africa": "Güney Afrika", "Korea": "Kore",
    "Thailand": "Tayland", "Nigeria": "Nijerya", "West Indies": "Batı Hint Adaları", "RSA": "Güney Afrika",
    "Czech Republic": "Çekya", "Hong Kong": "Hong Kong", "Unspecified": "Belirtilmemiş",
    "European Community": "Avrupa Topluluğu", "Bermuda": "Bermuda",
}

AD_ILK = [
    "Akdeniz", "Anadolu", "Ege", "Marmara", "Karadeniz", "Toros", "Yıldız", "Güneş", "Doğa", "Atlas",
    "Kuzey", "Mavi", "Defne", "Çınar", "Lale", "Pınar", "Umut", "Başak", "Ufuk", "Kartal", "Ada", "Kale",
    "Sedef", "Nehir", "Yakut", "Zümrüt", "Akın", "Bereket", "Tuna", "Gökçe", "Ilgaz", "Kaya", "Koru",
    "Liman", "Meltem", "Nar", "Orkide", "Palmiye", "Rüzgar", "Tan", "Vadi", "Yaprak", "Zirve", "Ekin",
    "Fidan", "Gelincik", "Hilal", "İnci", "Ladin", "Sardunya",
]
AD_ALAN = [
    "Ticaret", "Hediyelik", "Dekorasyon", "Ev Gereçleri", "Kırtasiye", "Tekstil", "Züccaciye",
    "Pazarlama", "Dış Ticaret", "Mağazacılık", "Tasarım", "Oyuncak", "Ambalaj", "Gıda", "Market",
]
AD_TUR = ["Ltd. Şti.", "A.Ş.", "San. ve Tic. Ltd. Şti."]
YABANCI_ILK = [
    "Nordic", "Euro", "Atlantic", "Alpine", "Baltic", "Pacific", "Coastal", "Royal", "Green", "Bright",
    "Golden", "Silver", "Union", "Global", "Prime",
]
YABANCI_ALAN = ["Home", "Gifts", "Living", "Trading", "Design", "Retail", "Supplies", "Decor"]
YABANCI_SON = ["Co", "Group", "House", "Partners", "Store"]
YABANCI_TUR = {
    "Germany": "GmbH", "Austria": "GmbH", "Switzerland": "AG", "France": "SARL", "Netherlands": "BV",
    "Belgium": "BV", "Spain": "SL", "Italy": "SRL", "Portugal": "Lda", "USA": "Inc", "Australia": "Pty Ltd",
}
TEDARIK_ALAN = ["Üretim", "İmalat", "Tedarik", "Toptan", "Plastik", "Cam", "Kağıt", "Metal", "Tekstil", "Seramik"]
HIZMETLER = [  # (kategori, ad parçası, ortalama aylık satışa oranı)
    ("kira", "Gayrimenkul Kiralama", Decimal("0.010")),
    ("enerji", "Enerji Dağıtım", Decimal("0.004")),
    ("lojistik", "Kargo ve Lojistik", Decimal("0.015")),
    ("yazilim", "Yazılım Hizmetleri", Decimal("0.002")),
    ("telekom", "Telekom Hizmetleri", Decimal("0.001")),
]
# Faturasız diğer giderler: (gerekçe, karşı tarafın ad parçası, o yılın ortalama aylık satışına oranı, kaç ayda bir)
DIGER_GIDERLER = [
    ("SİGORTA PRİMİ", "Sigorta A.Ş.", Decimal("0.003"), 1),
    ("MALİ MÜŞAVİRLİK", "Mali Müşavirlik", Decimal("0.001"), 1),
    ("BAKIM ONARIM", "Teknik Servis Ltd. Şti.", Decimal("0.002"), 3),
    ("DEMİRBAŞ ALIMI", "Ofis Mobilyaları Ltd. Şti.", Decimal("0.006"), 6),
]
CEK_BANKALARI = ["Banka D", "Banka E", "Banka F", "Banka G", "Banka H"]

HESAPLAR = [
    ("A_TRY", "Banka A", "TRY"),
    ("B_TRY", "Banka B", "TRY"),
    ("B_EUR", "Banka B", "EUR"),
    ("C_USD", "Banka C", "USD"),
]
DOVIZ_HESABI = {"TRY": "A_TRY", "EUR": "B_EUR", "USD": "C_USD"}

OLAY_SAYILARI = (
    ("buyuk_musteri_gecikmesi", 2),
    ("karsiliksiz_cek", 3),
    ("mukerrer_odeme", 2),
    ("olagandisi_buyuk_cikis", 2),
    ("eslesmeyen_tahsilat_birikimi", 1),
    ("nakit_sikismasi", 2),
)


@dataclass(frozen=True)
class Ayarlar:
    """Üretici ayarları. "sentetik" yazan değerler kaynağa dayanmayan tasarım kararlarıdır.
    Tahsilat davranışı data/kaynak/kalibrasyon.json bantlarına göre ayarlanır; bantlar ayardan önce yazıldı."""

    tohum: int = 42
    # UCI tarihleri bu kadar yıl ileri alınır, en yakın tam haftaya yuvarlanarak: aylar (yıl sonu zirvesi,
    # Noel kapanışı) yerinde kalır, haftanın günleri korunur. 15 yıl: 5481 gün (783 hafta).
    kaydirma_yil: int = 15
    # TCMB kurlarının indirildiği son gün. Kaydırılınca bundan sonraya düşen UCI kalemleri kullanılmaz
    # (gelecek günler için kur yok). 15 yıllık kaydırmada bu, UCI'nin son dört haftasıdır.
    veri_bitis: dt.date = dt.date(2026, 11, 12)
    simdi: dt.date = dt.date(2026, 9, 1)
    olaylar_acik: bool = True
    olay_sayilari: tuple = OLAY_SAYILARI
    # Her türün son olayı "şimdi"den sonraya düşer: as-of görünümünde görünmemeli.
    simdiden_sonra_olay: tuple = ("buyuk_musteri_gecikmesi", "karsiliksiz_cek", "olagandisi_buyuk_cikis")
    # Kalibrasyonla seçilen dört ayar: profiller, batik_musteri_payi, vade_yurtici, vade_ihracat.
    # Seçim: reports/faz1_ayar_denemeleri.json deneme 4; son kod değişikliğinden sonra deneme 5'te
    # tohum 42, 43 ve 44'ün üçü de bütün bantların içinde.
    # Ödeme profilleri: (ad, müşteri payı, faturayı geç ödeme olasılığı). Sentetik.
    profiller: tuple = (("zamaninda", 0.45, 0.10), ("ara_sira", 0.33, 0.40), ("sik", 0.17, 0.75), ("kronik", 0.05, 0.95))
    # Geç ödemelerin gecikme dilimleri (en az gün, en çok gün, olasılık): Atradius Türkiye 2026 dağılımı.
    gecikme_dilimleri: tuple = ((1, 30, 0.53), (31, 60, 0.24), (61, 90, 0.12), (91, 180, 0.11))
    batik_musteri_payi: float = 0.03  # bir noktadan sonra hiç ödemeyen müşteri payı, sentetik
    # Vade (gün, müşteri payı). 0: peşin. Yıl sonu alacağı UCI'nin Ekim-Aralık zirvesine denk geldiği için
    # DSO bandına ancak ortalama ~31 günlük vadeyle girilir (Atradius'un "%56'sı iki aya kadar vade veriyor"
    # bilgisiyle tam örtüşmez; bant TCMB DSO'sundan geldiği için o esas alındı). Sentetik.
    vade_yurtici: tuple = ((0, 0.25), (30, 0.45), (45, 0.15), (60, 0.10), (90, 0.05))
    vade_ihracat: tuple = ((30, 0.8), (60, 0.2))  # sentetik
    cekle_odeyen_musteri: float = 0.35  # yurt içi müşterilerden çekle ödeme alışkanlığı olanlar, sentetik
    cekle_odeme_olasiligi: float = 0.80  # bu müşterilerin bir faturayı çekle ödeme olasılığı, sentetik
    karsiliksiz_orani: float = 0.02  # sentetik varsayım: EVDS'de kaynak bulunamadı
    eslesmeyen_havale_orani: float = 0.004  # açıklamasız gelen havale, sentetik
    kismi_odeme_orani: float = 0.10  # sentetik
    pos_komisyon: Decimal = Decimal("0.02")  # sentetik
    alis_orani: Decimal = Decimal("0.72")  # mal alışı / satış, sentetik
    maas_orani: Decimal = Decimal("0.10")  # aylık maaş / o yılın ortalama aylık satışı, sentetik
    sgk_orani: Decimal = Decimal("0.375")  # SGK primi / maaş, sentetik
    vergi_orani: Decimal = Decimal("0.20")  # vergi / (satış - alış), sentetik
    kur_makasi: Decimal = Decimal("0.005")  # döviz bozdururken banka makası, sentetik
    acilis_tamponu: Decimal = Decimal("1.00")  # A_TRY en düşük bakiyesi ortalama aylık satışın bu katı, sentetik
    hesap_ucreti_kurus: int = 25_000  # aylık hesap işletim ücreti, sentetik


@dataclass
class Uretim:
    tablolar: dict[str, list[dict]]
    anahtar: dict
    kaydirma_gun: int
    veri_baslangic: dt.date
    bitisten_sonra_kalan_kalem: int  # kaydırılınca veri bitişinden sonraya düştüğü için kullanılmayan UCI kalemi


def kaydirma_gunu(ilk_gun: dt.date, yil: int) -> int:
    """`yil` yıl sonrasının gün farkını en yakın tam haftaya yuvarlar."""
    hedef = ilk_gun.replace(year=ilk_gun.year + yil, day=min(ilk_gun.day, 28) if ilk_gun.month == 2 else ilk_gun.day)
    return round((hedef - ilk_gun).days / 7) * 7


# ---------------------------------------------------------------- yardımcılar


def is_gunune_al(d: dt.date) -> dt.date:
    """Hafta sonuna düşen günü pazartesiye taşır (resmî tatiller dikkate alınmaz)."""
    while d.weekday() >= 5:
        d += GUN
    return d


def onceki_is_gunu(d: dt.date) -> dt.date:
    while d.weekday() >= 5:
        d -= GUN
    return d


def akis(ayarlar: Ayarlar, ad: str) -> random.Random:
    return random.Random(f"{ayarlar.tohum}:{ad}")


def secim(rng: random.Random, secenekler: tuple):
    """((değer, olasılık), ...) içinden olasılığa göre seçim."""
    r = rng.random()
    toplam = 0.0
    for deger, olasilik in secenekler:
        toplam += olasilik
        if r < toplam:
            return deger
    return secenekler[-1][0]


def tekil_ad(uret, kullanilan: set) -> str:
    for _ in range(50):
        ad = uret()
        if ad not in kullanilan:
            kullanilan.add(ad)
            return ad
    i = 2
    while f"{ad} {i}" in kullanilan:
        i += 1
    kullanilan.add(f"{ad} {i}")
    return f"{ad} {i}"


def ay_baslari(bas: dt.date, bit: dt.date) -> list[dt.date]:
    aylar, d = [], bas.replace(day=1)
    while d <= bit:
        aylar.append(d)
        d = (d + dt.timedelta(days=32)).replace(day=1)
    return aylar


def ay_sonu(ay: dt.date) -> dt.date:
    return (ay + dt.timedelta(days=32)).replace(day=1) - GUN


def _pb_tutari(gbp: Decimal, pb: str, tarih: dt.date, kurlar: Kurlar) -> tuple[int, Decimal, int]:
    """GBP tutarı fatura para birimine çevirir: (tutar_kurus, kur, tutar_try_kurus)."""
    gbp_try = kurlar.kur("GBP", tarih)
    if pb == "TRY":
        tl = kurusa(gbp * gbp_try)
        return tl, Decimal(1), tl
    kur = kurlar.kur(pb, tarih)
    tutar = kurusa(gbp * gbp_try / kur)
    return tutar, kur, kurusa(kurustan(tutar) * kur)


def yeni_id(onek: str, rng: random.Random, kullanilan: set) -> str:
    """Sırası bilgi taşımayan rastgele kimlik (ör. T3F9A01C2). Olayların eklediği ya da tarihini
    değiştirdiği kayıtlar kimliğinden ayırt edilemesin diye sıralı numara kullanılmaz."""
    while True:
        kimlik = f"{onek}{rng.getrandbits(32):08X}"
        if kimlik not in kullanilan:
            kullanilan.add(kimlik)
            return kimlik


def kimlik_ver(satirlar: list[dict], alan: str, onek: str, rng: random.Random, sirala) -> list[dict]:
    kullanilan: set = set()
    for s in sorted(satirlar, key=sirala):
        s[alan] = yeni_id(onek, rng, kullanilan)
    return satirlar


def sirali_kimlik_ver(satirlar: list[dict], alan: str, onek: str, genislik: int, sirala) -> list[dict]:
    for i, s in enumerate(sorted(satirlar, key=sirala), start=1):
        s[alan] = f"{onek}{i:0{genislik}d}"
    return satirlar


# ---------------------------------------------------------------- üretici


class Uretici:
    def __init__(self, kalemler: list[Kalem], kurlar: Kurlar, ayarlar: Ayarlar):
        if not kalemler:
            raise ValueError("kalem listesi boş")
        self.kurlar = kurlar
        self.a = ayarlar
        kaynak_ilk = min(k.tarih.date() for k in kalemler)
        self.kaydirma = kaydirma_gunu(kaynak_ilk, ayarlar.kaydirma_yil)  # tam hafta: haftanın günleri korunur
        self.baslangic = kaynak_ilk + dt.timedelta(days=self.kaydirma)
        self.bitis = ayarlar.veri_bitis
        if not self.baslangic < ayarlar.simdi <= self.bitis:
            raise ValueError("simdi, veri başlangıcı ile veri bitişi arasında olmalı")
        self.anahtar: dict = {"musteri_profilleri": {}, "eslesmeyen_tahsilatlar": {}, "olaylar": []}
        self._kalemler = [k for k in kalemler if self.kaydir(k.tarih) <= self.bitis]
        self.bitisten_sonra_kalan = len(kalemler) - len(self._kalemler)

    def kaydir(self, t: dt.datetime) -> dt.date:
        return t.date() + dt.timedelta(days=self.kaydirma)

    # -- müşteriler ve faturalar -------------------------------------------------------------

    def faturalari_topla(self) -> list[dict]:
        faturalar: dict[tuple, dict] = {}
        for k in self._kalemler:
            anahtar = (k.fatura_no, k.musteri_no or "")
            f = faturalar.get(anahtar)
            if f is None:
                f = faturalar[anahtar] = {
                    "no": k.fatura_no, "tarih": self.kaydir(k.tarih), "musteri": k.musteri_no, "ulke": k.ulke,
                    "kategori": k.kategori, "gbp": Decimal(0), "kalem": 0, "stoklar": set(),
                }
            f["gbp"] += k.tutar
            f["kalem"] += 1
            f["stoklar"].add(k.stok_kodu)
        return sorted(faturalar.values(), key=lambda f: (f["tarih"], f["no"], f["musteri"] or ""))

    def musterileri_uret(self, ham: list[dict]) -> None:
        rng = akis(self.a, "musteri")
        ulke_sayaci: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        ilk: dict[str, dt.date] = {}
        for f in ham:
            if f["musteri"]:
                ulke_sayaci[f["musteri"]][f["ulke"]] += 1
                ilk.setdefault(f["musteri"], f["tarih"])

        def sira(no: str):
            return (0, int(no), "") if no.isdigit() else (1, 0, no)

        kullanilan: set = set()
        self.musteriler, self.musteri_harita = [], {}
        for i, no in enumerate(sorted(ulke_sayaci, key=sira), start=1):
            ulke = max(sorted(ulke_sayaci[no]), key=lambda u: ulke_sayaci[no][u])
            yurtici = ulke == "United Kingdom"
            pb = "TRY" if yurtici else ("EUR" if ulke in AVRO_ULKELERI else "USD")
            if yurtici:
                ad = tekil_ad(lambda: " ".join(
                    [rng.choice(AD_ILK), rng.choice(AD_ILK), rng.choice(AD_ALAN), rng.choice(AD_TUR)]), kullanilan)
                vade = secim(rng, self.a.vade_yurtici)
            else:
                tur = YABANCI_TUR.get(ulke, "Ltd")
                ad = tekil_ad(lambda: " ".join(
                    [rng.choice(YABANCI_ILK), rng.choice(YABANCI_ALAN), rng.choice(YABANCI_SON), tur]), kullanilan)
                vade = secim(rng, self.a.vade_ihracat)
            profil = secim(rng, tuple((p[0], p[1]) for p in self.a.profiller))
            gec = {p[0]: p[2] for p in self.a.profiller}[profil]
            batik = rng.random() < self.a.batik_musteri_payi
            # İleri tarihli çek kredinin kendisidir: peşin (0 gün vadeli) müşteri çek kullanmaz.
            cekci = yurtici and rng.random() < self.a.cekle_odeyen_musteri and vade > 0
            mid = f"M{i:05d}"
            self.musteri_harita[no] = mid
            self.musteriler.append({
                "musteri_id": mid, "ad": ad, "ulke": ULKE_TR.get(ulke, ulke), "yurtici": yurtici,
                "para_birimi": pb, "vade_gun": vade, "ilk_islem_tarihi": ilk[no],
                "_profil": profil, "_gec": gec, "_batik": batik, "_cekci": cekci,
            })
        self.musteri_ada = {m["musteri_id"]: m for m in self.musteriler}

    def faturalari_uret(self, ham: list[dict]) -> None:
        self.faturalar, self.pos = [], []
        satislar: dict[str, list] = defaultdict(list)
        fid = pid = 0
        for f in ham:
            if f["kategori"] in ("perakende", "perakende_iade"):
                pid += 1
                tl = kurusa(f["gbp"] * self.kurlar.kur("GBP", f["tarih"]))
                komisyon = oran_uygula(abs(tl), self.a.pos_komisyon) * (1 if tl >= 0 else -1)
                self.pos.append({
                    "pos_id": f"P{pid:06d}", "kaynak_fatura_no": f["no"], "islem_tarihi": f["tarih"],
                    "tutar_kurus": tl, "komisyon_kurus": komisyon, "valor_tarihi": is_gunune_al(f["tarih"] + GUN),
                })
                continue
            fid += 1
            m = self.musteri_ada[self.musteri_harita[f["musteri"]]]
            tutar, kur, tl = _pb_tutari(f["gbp"], m["para_birimi"], f["tarih"], self.kurlar)
            satir = {
                "fatura_id": f"F{fid:06d}", "kaynak_fatura_no": f["no"],
                "tur": "iade" if f["kategori"] == "iade" else "satis", "musteri_id": m["musteri_id"],
                "fatura_tarihi": f["tarih"], "vade_tarihi": f["tarih"], "para_birimi": m["para_birimi"],
                "tutar_kurus": tutar, "kur": str(kur), "tutar_try_kurus": tl, "iliskili_fatura_id": None,
                "iade_eslesme": None, "uygulanan_kurus": None, "kalem_sayisi": f["kalem"],
            }
            if satir["tur"] == "satis":
                satir["vade_tarihi"] = f["tarih"] + dt.timedelta(days=m["vade_gun"])
                satislar[m["musteri_id"]].append((f["tarih"], satir["fatura_id"], f["stoklar"]))
            else:
                # İade, aynı müşterinin önceki bir satışına bağlanır: önce ortak stok koduna, yoksa son faturaya.
                hedef, yol = None, "yok"
                for tarih, aday, stoklar in reversed(satislar[m["musteri_id"]]):
                    if tarih <= f["tarih"] and stoklar & f["stoklar"]:
                        hedef, yol = aday, "stok_kodu"
                        break
                if hedef is None and satislar[m["musteri_id"]]:
                    hedef, yol = satislar[m["musteri_id"]][-1][1], "son_fatura"
                satir["iliskili_fatura_id"], satir["iade_eslesme"] = hedef, yol
            self.faturalar.append(satir)
        self.fatura_ada = {f["fatura_id"]: f for f in self.faturalar}
        self.musteri_toplam = defaultdict(int)
        for f in self.faturalar:
            self.musteri_toplam[f["musteri_id"]] += f["tutar_try_kurus"]

    # -- tahsilat planı ------------------------------------------------------------------------

    def _odeme_gunu(self, hedef: dt.date, fatura: dict, gec: bool) -> dt.date:
        """Geç ödemede hafta sonu ileri, zamanında ödemede geri alınır (vadeyi aşmasın)."""
        en_erken = fatura["fatura_tarihi"]  # peşin (0 gün vadeli) satış aynı gün ödenebilir
        hedef = max(hedef, en_erken)
        if gec:
            return is_gunune_al(hedef)
        d = onceki_is_gunu(hedef)
        return d if d >= en_erken else is_gunune_al(hedef)

    def tahsilat_plani(self) -> None:
        rng = akis(self.a, "tahsilat")
        rng_batik = akis(self.a, "batik")
        rng_cek = akis(self.a, "cek")
        satislar = defaultdict(list)
        for f in self.faturalar:
            if f["tur"] == "satis":
                satislar[f["musteri_id"]].append(f)
        batik_baslangic = {}
        for m in self.musteriler:
            liste = satislar[m["musteri_id"]]
            if m["_batik"] and liste:
                i = int(len(liste) * (0.3 + 0.6 * rng_batik.random()))
                batik_baslangic[m["musteri_id"]] = liste[min(i, len(liste) - 1)]["fatura_tarihi"]
            bb = batik_baslangic.get(m["musteri_id"])
            self.anahtar["musteri_profilleri"][m["musteri_id"]] = {
                "profil": m["_profil"], "cekle_oder": m["_cekci"],
                "batik_baslangic": bb.isoformat() if bb else None,
            }
        self.plan = {}
        for f in self.faturalar:
            if f["tur"] != "satis":
                continue
            m = self.musteri_ada[f["musteri_id"]]
            bb = batik_baslangic.get(m["musteri_id"])
            gec = rng.random() < m["_gec"]
            if gec:
                dilim_r, toplam = rng.random(), 0.0
                for alt, ust, p in self.a.gecikme_dilimleri:
                    toplam += p
                    if dilim_r < toplam:
                        break
                gun = rng.randint(alt, ust)
            else:
                gun = -rng.randint(0, 10)
            son = self._odeme_gunu(f["vade_tarihi"] + dt.timedelta(days=gun), f, gec)
            kismi = rng.random() < self.a.kismi_odeme_orani
            once_gun = rng.randint(7, 30)
            oran = Decimal(rng.randint(40, 70)) / 100
            eslesmeyen_r = rng.random()
            cek_r, alis_gun, karsiliksiz_r, yeniden_gun = (
                rng_cek.random(), rng_cek.randint(30, 90), rng_cek.random(), rng_cek.randint(15, 60))
            banka, cek_no = rng_cek.choice(CEK_BANKALARI), str(rng_cek.randint(1_000_000, 9_999_999))

            p = {"odenmeyecek": bb is not None and f["fatura_tarihi"] >= bb, "taksitler": [], "cek": None,
                 "yontem": "havale" if m["para_birimi"] == "TRY" else "swift", "eslesmeyen": False}
            if m["_cekci"] and not kismi and cek_r < self.a.cekle_odeme_olasiligi:
                p["yontem"] = "cek"
                p["cek"] = {
                    "alis": max(son - dt.timedelta(days=alis_gun), f["fatura_tarihi"]), "vade": son,
                    "karsiliksiz": karsiliksiz_r < self.a.karsiliksiz_orani,
                    "yeniden": is_gunune_al(son + dt.timedelta(days=yeniden_gun)), "banka": banka, "no": cek_no,
                }
            elif kismi:
                once = self._odeme_gunu(son - dt.timedelta(days=once_gun), f, False)
                p["taksitler"] = [(min(once, son), oran), (son, None)]
            else:
                p["taksitler"] = [(son, None)]
            p["eslesmeyen"] = p["yontem"] == "havale" and eslesmeyen_r < self.a.eslesmeyen_havale_orani
            self.plan[f["fatura_id"]] = p

    def plani_uygula(self) -> dict[str, list[dict]]:
        """Plandan tahsilat, çek, çek hareketi ve müşteri iadesi ödemelerini üretir.

        Her satış faturası için olaylar tarih sırasıyla işlenir: iade faturası (açık tutardan düşer,
        artanı müşteriye geri ödenir), taksit, çek alımı, çek vadesi, karşılıksız çekin yeniden ödenmesi.
        Veri bitişinden sonraki olay işlenmez.
        """
        tahsilatlar, cekler, cek_hareketleri, iade_odemeleri = [], [], [], []
        self.uygulanan: dict[str, int] = {}
        iadeler = defaultdict(list)
        for f in self.faturalar:
            if f["tur"] == "iade":
                if f["iliskili_fatura_id"]:
                    iadeler[f["iliskili_fatura_id"]].append(f)
                else:
                    self.uygulanan[f["fatura_id"]] = 0
                    self._iade_odemesi(f, -f["tutar_kurus"], iade_odemeleri)

        for f in self.faturalar:
            if f["tur"] != "satis":
                continue
            p = self.plan[f["fatura_id"]]
            m = self.musteri_ada[f["musteri_id"]]
            kuyruk, sira = [], 0

            def ekle(tarih, oncelik, tur, veri):
                nonlocal sira
                sira += 1
                heapq.heappush(kuyruk, (tarih, oncelik, sira, tur, veri))

            for iade in iadeler[f["fatura_id"]]:
                ekle(iade["fatura_tarihi"], 0, "iade", iade)
            if not p["odenmeyecek"]:
                if p["cek"]:
                    ekle(p["cek"]["alis"], 1, "cek_alis", None)
                else:
                    for t, oran in p["taksitler"]:
                        ekle(t, 2, "taksit", oran)
            kalan, bekleyen = f["tutar_kurus"], 0  # bekleyen: portföydeki çekle karşılanacak kısım
            while kuyruk:
                t, _, _, tur, veri = heapq.heappop(kuyruk)
                if t > self.bitis:
                    break
                if tur == "iade":
                    tutar = -veri["tutar_kurus"]
                    uyg = max(0, min(tutar, kalan - bekleyen))
                    self.uygulanan[veri["fatura_id"]] = uyg
                    kalan -= uyg
                    if tutar - uyg > 0:
                        self._iade_odemesi(veri, tutar - uyg, iade_odemeleri)
                elif tur == "taksit":
                    odenecek = kalan if veri is None else min(kalan, kurusa(kurustan(kalan) * veri))
                    if odenecek > 0:
                        eslesmeyen = p["eslesmeyen"] and veri is None
                        tahsilatlar.append(self._tahsilat(m, f, t, odenecek, p["yontem"], None, eslesmeyen))
                        kalan -= odenecek
                elif tur == "cek_alis":
                    if kalan <= 0:
                        continue
                    cek = p["cek"]
                    cek_id = "C" + f["fatura_id"][1:]
                    cekler.append({
                        "cek_id": cek_id, "musteri_id": m["musteri_id"], "cek_no": cek["no"],
                        "kesideci_banka": cek["banka"], "alis_tarihi": t, "vade_tarihi": cek["vade"],
                        "tutar_kurus": kalan,
                    })
                    cek_hareketleri.append({"cek_id": cek_id, "tarih": t, "durum": "portfoyde"})
                    verilis = onceki_is_gunu(cek["vade"] - dt.timedelta(days=3))
                    if t < verilis <= self.bitis:
                        cek_hareketleri.append({"cek_id": cek_id, "tarih": verilis, "durum": "tahsilde"})
                    bekleyen = kalan
                    ekle(cek["vade"], 1, "cek_vade", (cek_id, kalan))
                elif tur == "cek_vade":
                    cek_id, tutar = veri
                    bekleyen -= tutar
                    if p["cek"]["karsiliksiz"]:
                        cek_hareketleri.append({"cek_id": cek_id, "tarih": t, "durum": "karsiliksiz"})
                        ekle(p["cek"]["yeniden"], 2, "yeniden", None)
                    else:
                        cek_hareketleri.append({"cek_id": cek_id, "tarih": t, "durum": "tahsil_edildi"})
                        tahsilatlar.append(self._tahsilat(m, f, t, tutar, "cek", cek_id, False))
                        kalan -= tutar
                elif tur == "yeniden" and kalan > 0:
                    tahsilatlar.append(self._tahsilat(m, f, t, kalan, "havale", None, False))
                    kalan = 0
        for f in self.faturalar:
            if f["tur"] == "iade":
                f["uygulanan_kurus"] = self.uygulanan.get(f["fatura_id"], 0)
        return {"tahsilatlar": tahsilatlar, "cekler": cekler, "cek_hareketleri": cek_hareketleri,
                "iade_odemeleri": iade_odemeleri}

    def _iade_odemesi(self, iade: dict, tutar: int, liste: list) -> None:
        tarih = is_gunune_al(iade["fatura_tarihi"] + dt.timedelta(days=5))
        if tarih > self.bitis:
            return
        m = self.musteri_ada[iade["musteri_id"]]
        liste.append({
            "tur": "musteri_iadesi", "tedarikci_id": None, "alis_fatura_id": None, "musteri_id": m["musteri_id"],
            "iade_fatura_id": iade["fatura_id"], "tarih": tarih, "tutar_kurus": tutar,
            "yontem": "havale" if m["para_birimi"] == "TRY" else "swift",
            "banka_hesap_id": DOVIZ_HESABI[m["para_birimi"]], "aciklama": f"İADE {m['ad']}",
        })

    def _tahsilat(self, m, f, tarih, tutar, yontem, cek_id, eslesmeyen) -> dict:
        return {
            "musteri_id": m["musteri_id"], "fatura_id": None if eslesmeyen else f["fatura_id"], "tarih": tarih,
            "para_birimi": m["para_birimi"], "tutar_kurus": tutar, "yontem": yontem, "cek_id": cek_id,
            "banka_hesap_id": DOVIZ_HESABI[m["para_birimi"]],
            "eslesme": "bilerek_eslesmemis" if eslesmeyen else "eslesti", "_gercek_fatura": f["fatura_id"],
        }

    # -- tedarikçiler ve ödemeler -------------------------------------------------------------

    def aylik_satislar(self) -> dict[dt.date, int]:
        aylik = defaultdict(int)
        for f in self.faturalar:
            aylik[f["fatura_tarihi"].replace(day=1)] += f["tutar_try_kurus"]
        for p in self.pos:
            aylik[p["islem_tarihi"].replace(day=1)] += p["tutar_kurus"]
        return aylik

    def tedarik_uret(self) -> None:
        rng = akis(self.a, "tedarikci")
        kullanilan: set = set()
        self.tedarikciler = []
        for i in range(1, 41):
            ad = tekil_ad(lambda: f"{rng.choice(AD_ILK)} {rng.choice(TEDARIK_ALAN)} {rng.choice(AD_TUR)}", kullanilan)
            self.tedarikciler.append({"tedarikci_id": f"S{i:03d}", "ad": ad, "kategori": "mal",
                                      "vade_gun": secim(rng, ((30, 0.3), (60, 0.5), (90, 0.2)))})
        for j, (kategori, alan, _) in enumerate(HIZMETLER, start=41):
            ad = tekil_ad(lambda: f"{rng.choice(AD_ILK)} {alan} {rng.choice(AD_TUR)}", kullanilan)
            self.tedarikciler.append({"tedarikci_id": f"S{j:03d}", "ad": ad, "kategori": kategori, "vade_gun": 30})
        mal = [t for t in self.tedarikciler if t["kategori"] == "mal"]
        hizmet = {t["kategori"]: t for t in self.tedarikciler if t["kategori"] != "mal"}
        agirlik = [1 / (i + 1) for i in range(len(mal))]

        aylik = self.aylik_satislar()
        aylar = ay_baslari(self.baslangic, self.bitis)
        tam = [a for a in aylar if a >= self.baslangic and ay_sonu(a) <= self.bitis]
        self.ortalama_aylik_satis = sum(aylik[a] for a in tam) // max(1, len(tam))
        yillik = defaultdict(list)
        for a in tam:
            yillik[a.year].append(aylik[a])
        yil_ort = {y: sum(v) // len(v) for y, v in yillik.items()}

        def yil_ortalamasi(ay: dt.date) -> int:
            return yil_ort.get(ay.year, self.ortalama_aylik_satis)

        def is_gunleri(ay: dt.date) -> list[dt.date]:
            d, gunler = ay, []
            while d <= ay_sonu(ay):
                if d.weekday() < 5 and self.baslangic <= d <= self.bitis:
                    gunler.append(d)
                d += GUN
            return gunler

        self.alislar, aylik_alis = [], defaultdict(int)
        aid = 0
        for ay in aylar:
            gunler = is_gunleri(ay)
            if not gunler:
                continue
            toplam = kurusa(kurustan(max(aylik[ay], 0)) * self.a.alis_orani)
            n = rng.randint(8, 15)
            paylar = [rng.randint(20, 120) for _ in range(n)]
            kalan = toplam
            for k in range(n):
                tutar = kalan if k == n - 1 else toplam * paylar[k] // sum(paylar)
                kalan -= tutar
                t = rng.choices(mal, weights=agirlik)[0]
                tarih = rng.choice(gunler)
                if tutar <= 0:
                    continue
                aid += 1
                self.alislar.append({
                    "alis_fatura_id": f"A{aid:06d}", "tedarikci_id": t["tedarikci_id"], "tarih": tarih,
                    "vade_tarihi": tarih + dt.timedelta(days=t["vade_gun"]), "tutar_kurus": tutar, "kategori": "mal"})
                aylik_alis[ay] += tutar
            gun_sec = {"kira": gunler[0], "enerji": gunler[min(14, len(gunler) - 1)], "lojistik": gunler[-1],
                       "yazilim": gunler[0], "telekom": gunler[min(9, len(gunler) - 1)]}
            for kategori, _, oran in HIZMETLER:
                taban = aylik[ay] if kategori == "lojistik" else yil_ortalamasi(ay)
                tutar = kurusa(kurustan(max(taban, 0)) * oran * (100 + rng.randint(-5, 5)) / 100)
                if tutar <= 0:
                    continue
                t = hizmet[kategori]
                aid += 1
                self.alislar.append({
                    "alis_fatura_id": f"A{aid:06d}", "tedarikci_id": t["tedarikci_id"], "tarih": gun_sec[kategori],
                    "vade_tarihi": gun_sec[kategori] + dt.timedelta(days=t["vade_gun"]), "tutar_kurus": tutar,
                    "kategori": kategori})
                aylik_alis[ay] += tutar

        ad = {t["tedarikci_id"]: t["ad"] for t in self.tedarikciler}
        odemeler = []
        for a in self.alislar:
            gec = rng.random() < 0.10
            kayma = rng.randint(1, 10) if gec else -rng.randint(0, 2)
            cekle = a["kategori"] == "mal" and rng.random() < 0.15
            hedef = a["vade_tarihi"] + dt.timedelta(days=kayma)
            tarih = is_gunune_al(hedef) if gec else onceki_is_gunu(hedef)
            if tarih > self.bitis:
                continue
            odemeler.append({
                "tur": "tedarikci", "tedarikci_id": a["tedarikci_id"], "alis_fatura_id": a["alis_fatura_id"],
                "musteri_id": None, "iade_fatura_id": None, "tarih": tarih, "tutar_kurus": a["tutar_kurus"],
                "yontem": "cek" if cekle else "havale", "banka_hesap_id": "A_TRY",
                "aciklama": ("VERİLEN ÇEK ÖDEMESİ " if cekle else "EFT GİDEN ") + ad[a["tedarikci_id"]]})

        def sabit_odeme(tur, tarih, tutar, aciklama):
            if self.baslangic <= tarih <= self.bitis and tutar > 0:
                odemeler.append({"tur": tur, "tedarikci_id": None, "alis_fatura_id": None, "musteri_id": None,
                                 "iade_fatura_id": None, "tarih": tarih, "tutar_kurus": tutar, "yontem": "havale",
                                 "banka_hesap_id": "A_TRY", "aciklama": aciklama})

        karsi = {neden: f"{rng.choice(AD_ILK)} {parca}" for neden, parca, _, _ in DIGER_GIDERLER}
        for i, ay in enumerate(aylar):
            gunler = is_gunleri(ay)
            for neden, _, oran, siklik in DIGER_GIDERLER:
                if i % siklik == 0 and gunler:
                    tutar = kurusa(kurustan(yil_ortalamasi(ay)) * oran * (100 + rng.randint(-10, 10)) / 100)
                    odemeler.append({"tur": "diger", "tedarikci_id": None, "alis_fatura_id": None, "musteri_id": None,
                                     "iade_fatura_id": None, "tarih": rng.choice(gunler), "tutar_kurus": tutar,
                                     "yontem": "havale", "banka_hesap_id": "A_TRY",
                                     "aciklama": f"EFT GİDEN {karsi[neden]} {neden}"})
        for ay in aylar:
            maas = kurusa(kurustan(yil_ortalamasi(ay)) * self.a.maas_orani)
            sabit_odeme("maas", onceki_is_gunu(ay_sonu(ay)), maas, f"MAAŞ ÖDEMESİ {ay.strftime('%m/%Y')}")
            gun = is_gunune_al((ay + dt.timedelta(days=32)).replace(day=26))
            sabit_odeme("sgk", gun, kurusa(kurustan(maas) * self.a.sgk_orani), f"SGK PRİM ÖDEMESİ {ay.strftime('%m/%Y')}")
            matrah = max(aylik[ay] - aylik_alis[ay], 0)
            sabit_odeme("vergi", gun, kurusa(kurustan(matrah) * self.a.vergi_orani), f"VERGİ ÖDEMESİ {ay.strftime('%m/%Y')}")
        self.odemeler_temel = odemeler

    # -- banka ---------------------------------------------------------------------------------

    def defter(self, tahsilatlar: list[dict], odemeler: list[dict], acilis: dict[str, int]) -> list[dict]:
        """Tahsilat, POS valörleri ve ödemelerden banka hareketlerini ve satır satır bakiyeyi türetir.
        Ayrıca ayın ilk iş günü döviz bozdurma ve hesap ücreti, her cuma B_TRY'den A_TRY'ye virman yapılır."""
        gunluk = defaultdict(list)
        for t in tahsilatlar:
            ad = self.musteri_ada[t["musteri_id"]]["ad"]
            if t["yontem"] == "cek":
                aciklama = f"ÇEK TAHSİLATI {t['cek_id']} {ad}"
            elif t["yontem"] == "swift":
                aciklama = f"SWIFT GELEN {t['para_birimi']} {ad}"
            else:
                aciklama = f"EFT GELEN {ad}"
            gunluk[t["tarih"]].append((0, t["tahsilat_id"], t["banka_hesap_id"], t["tutar_kurus"], aciklama, "tahsilat"))
        pos_gunluk = defaultdict(int)
        for p in self.pos:
            if p["valor_tarihi"] <= self.bitis:
                pos_gunluk[p["valor_tarihi"]] += p["tutar_kurus"] - p["komisyon_kurus"]
        for gun, net in pos_gunluk.items():
            gunluk[gun].append((1, gun.isoformat(), "B_TRY", net, "POS ÜYE İŞYERİ ALACAK", "pos"))
        for o in odemeler:
            gunluk[o["tarih"]].append((2, o["odeme_id"], o["banka_hesap_id"], -o["tutar_kurus"], o["aciklama"], "odeme"))

        bakiye = dict(acilis)
        satirlar = []
        para_birimi = {h: pb for h, _, pb in HESAPLAR}

        def karsila(hesap, gun, eksik):
            """Yan hesaptaki açık aynı gün A_TRY'den kapatılır: TL hesaba virman, döviz hesabına döviz alışı."""
            pb = para_birimi[hesap]
            kid = f"{gun.isoformat()}-{hesap}-karsilama"
            if pb == "TRY":
                yaz("A_TRY", gun, -eksik, "HESAPLAR ARASI VİRMAN", "virman", kid)
                yaz(hesap, gun, eksik, "HESAPLAR ARASI VİRMAN", "virman", kid)
            else:
                kur = self.kurlar.kur(pb, gun)
                aciklama = f"DÖVİZ ALIŞ {pb}/TRY KUR {kur}"
                yaz("A_TRY", gun, -kurusa(kurustan(eksik) * kur * (1 + self.a.kur_makasi)), aciklama, "kur_donusum", kid)
                yaz(hesap, gun, eksik, aciklama, "kur_donusum", kid)

        def yaz(hesap, gun, tutar, aciklama, tur, kid):
            if hesap != "A_TRY" and bakiye[hesap] + tutar < 0:
                karsila(hesap, gun, -(bakiye[hesap] + tutar))
            bakiye[hesap] += tutar
            satirlar.append({"hareket_id": f"H{len(satirlar) + 1:07d}", "hesap_id": hesap, "tarih": gun,
                             "tutar_kurus": tutar, "bakiye_kurus": bakiye[hesap], "aciklama": aciklama,
                             "kaynak_tur": tur, "kaynak_id": kid})

        gun = self.baslangic
        while gun <= self.bitis:
            for _, kid, hesap, tutar, aciklama, tur in sorted(gunluk.get(gun, []), key=lambda x: (x[0], x[1])):
                yaz(hesap, gun, tutar, aciklama, tur, kid)
            if gun.weekday() < 5 and gun == is_gunune_al(gun.replace(day=1)):
                for hesap, pb in (("B_EUR", "EUR"), ("C_USD", "USD")):
                    if bakiye[hesap] > 0:
                        doviz = kurusa(kurustan(bakiye[hesap]) * Decimal("0.8"))
                        kur = self.kurlar.kur(pb, gun)
                        tl = kurusa(kurustan(doviz) * kur * (1 - self.a.kur_makasi))
                        aciklama = f"DÖVİZ SATIŞ {pb}/TRY KUR {kur}"
                        yaz(hesap, gun, -doviz, aciklama, "kur_donusum", f"{gun.isoformat()}-{pb}")
                        yaz("A_TRY", gun, tl, aciklama, "kur_donusum", f"{gun.isoformat()}-{pb}")
                yaz("A_TRY", gun, -self.a.hesap_ucreti_kurus, "HESAP İŞLETİM ÜCRETİ", "masraf", f"{gun.isoformat()}-ucret")
            if gun.weekday() == 4 and bakiye["B_TRY"] > 0:
                tutar = kurusa(kurustan(bakiye["B_TRY"]) * Decimal("0.9"))
                yaz("B_TRY", gun, -tutar, "HESAPLAR ARASI VİRMAN", "virman", f"{gun.isoformat()}-virman")
                yaz("A_TRY", gun, tutar, "HESAPLAR ARASI VİRMAN", "virman", f"{gun.isoformat()}-virman")
            gun += GUN
        return satirlar

    def acilis_bakiyeleri(self, tahsilatlar: list[dict], odemeler: list[dict]) -> dict[str, int]:
        """Yalnızca A_TRY açılış bakiyesiyle başlar (yan hesapların açığı A_TRY'den kapatılır). Hiçbir kural
        A_TRY bakiyesine bakmadığı için A_TRY bakiyesi açılışla birebir kayar: tek turda kesin hesaplanır,
        en düşük A_TRY bakiyesi tampona eşit olur."""
        tampon = kurusa(kurustan(self.ortalama_aylik_satis) * self.a.acilis_tamponu)
        acilis = {h: 0 for h, _, _ in HESAPLAR}
        en_dusuk = min([0] + [s["bakiye_kurus"] for s in self.defter(tahsilatlar, odemeler, acilis)
                              if s["hesap_id"] == "A_TRY"])
        acilis["A_TRY"] = tampon - en_dusuk
        return acilis


def _temiz(satirlar: list[dict]) -> list[dict]:
    return [{k: v for k, v in s.items() if not k.startswith("_")} for s in satirlar]


def uret(kalemler: list[Kalem], kurlar: Kurlar, ayarlar: Ayarlar = Ayarlar()) -> Uretim:
    from sayman.data import events

    u = Uretici(kalemler, kurlar, ayarlar)
    ham = u.faturalari_topla()
    u.musterileri_uret(ham)
    u.faturalari_uret(ham)
    u.tahsilat_plani()
    u.tedarik_uret()
    sonuc = u.plani_uygula()

    rng_kimlik = akis(ayarlar, "kimlik")
    tahsilatlar = kimlik_ver(sonuc["tahsilatlar"], "tahsilat_id", "T", rng_kimlik,
                             lambda t: (t["tarih"], t["_gercek_fatura"], t["yontem"]))
    odemeler = kimlik_ver(u.odemeler_temel + sonuc["iade_odemeleri"], "odeme_id", "O", rng_kimlik,
                          lambda o: (o["tarih"], o["tur"], o["alis_fatura_id"] or "", o["iade_fatura_id"] or "",
                                     o["aciklama"], o["tutar_kurus"]))
    cek_hareketleri = sirali_kimlik_ver(sonuc["cek_hareketleri"], "cek_hareket_id", "K", 7,
                                        lambda h: (h["tarih"], h["cek_id"]))
    acilis = u.acilis_bakiyeleri(tahsilatlar, odemeler)

    kayitlar = {"tahsilatlar": tahsilatlar, "cekler": sonuc["cekler"], "cek_hareketleri": cek_hareketleri,
                "odemeler": odemeler, "alis_faturalari": u.alislar}
    if ayarlar.olaylar_acik:
        kayitlar = events.uygula(u, copy.deepcopy(kayitlar), acilis)

    for t in kayitlar["tahsilatlar"]:
        if t["eslesme"] == "bilerek_eslesmemis":
            u.anahtar["eslesmeyen_tahsilatlar"][t["tahsilat_id"]] = t["_gercek_fatura"]

    tablolar = {
        "musteriler": _temiz(u.musteriler),
        "faturalar": _temiz(u.faturalar),
        "pos_islemleri": _temiz(u.pos),
        "tahsilatlar": _temiz(kayitlar["tahsilatlar"]),
        "cekler": _temiz(kayitlar["cekler"]),
        "cek_hareketleri": _temiz(kayitlar["cek_hareketleri"]),
        "tedarikciler": _temiz(u.tedarikciler),
        "alis_faturalari": _temiz(kayitlar["alis_faturalari"]),
        "odemeler": _temiz(kayitlar["odemeler"]),
        "banka_hesaplari": [
            {"hesap_id": h, "banka": b, "para_birimi": pb, "acilis_tarihi": u.baslangic, "acilis_bakiye_kurus": acilis[h]}
            for h, b, pb in HESAPLAR
        ],
        "banka_hareketleri": u.defter(kayitlar["tahsilatlar"], kayitlar["odemeler"], acilis),
        "doviz_kurlari": [
            {"kur_id": f"{t.isoformat()}-{pb}", "tarih": t, "para_birimi": pb, "alis": str(k)}
            for t, pb, k in kurlar.satirlar(u.baslangic, u.bitis)
        ],
    }
    return Uretim(tablolar=tablolar, anahtar=u.anahtar, kaydirma_gun=u.kaydirma, veri_baslangic=u.baslangic,
                  bitisten_sonra_kalan_kalem=u.bitisten_sonra_kalan)
