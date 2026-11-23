"""Sentetik sözleşmeler ve şirket politikaları.

Şirketin tedarikçileri ve en büyük müşterileri için çerçeve sözleşmeler, ayrıca şirketin finans politikaları üretilir.
Her belge maddelere bölünür; her madde bir arama parçasıdır ve kimliği sabittir ("BELGE#mN").

- Şirket verisi (data/uretilen) yalnızca okunur, değişmez. Veride zaten olan değerler oradan alınır: tedarikçi ve müşteri
  vadesi, müşterinin para birimi, çekle ödeyip ödemediği. Böylece sözleşme ile kayıtlar çelişmez.
- Geri kalan değerler (faiz, süre, mahkeme...) kendi rastgele sayı akışından seçilir ("{tohum}:belge"); şirket verisinin
  akışlarına dokunmaz.
- Nakit politikasındaki güvenlik eşiği, izleme kuralının (Faz 4) kullandığı orandır.

Her maddenin `alan`ı (ör. "gecikme_faizi") ve `deger`i (ör. "aylık %2,25") vardır; soru setleri bunlardan üretilir.
Alanı olmayan maddeler (taraflar, amaç) soru almaz ama aranabilir.
"""

from __future__ import annotations

import datetime as dt
import random
from collections import defaultdict
from dataclasses import asdict, dataclass

from sayman.monitoring.rules import Ayarlar as IzlemeAyarlari

GUN = dt.timedelta(days=1)
SIRKET = "ŞİRKET"
YASAL_EKLER = ("San. ve Tic. Ltd. Şti.", "Ltd. Şti.", "A.Ş.", "Pty Ltd", "GmbH", "SARL", "BV", "SL", "Lda", "AG",
               "SRL", "Inc", "Ltd")
URUNLER = ("kırtasiye ve ofis malzemesi", "ambalaj malzemesi", "ev dekorasyon ürünleri", "mutfak gereçleri",
           "hediyelik eşya", "tekstil ürünleri", "oyuncak ve parti malzemesi", "aydınlatma ürünleri")
MAHKEMELER = ("İstanbul (Çağlayan)", "İstanbul Anadolu", "Ankara", "İzmir", "Bursa", "Kocaeli")
AYLAR = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")
SATIS_YURTICI, SATIS_IHRACAT = 20, 10  # sözleşmesi olan en büyük müşteri sayısı


@dataclass(frozen=True)
class Madde:
    parca_id: str
    belge_id: str
    no: int
    baslik: str
    metin: str
    alan: str | None
    deger: str | None


@dataclass(frozen=True)
class Belge:
    belge_id: str
    tur: str  # tedarik_sozlesmesi, hizmet_sozlesmesi, satis_sozlesmesi, politika
    baslik: str
    taraf_id: str | None
    taraf_ad: str | None
    kisa_ad: str | None
    roller: tuple[str, ...]
    tarih: str
    maddeler: tuple[Madde, ...]

    def sozluk(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- biçim yardımcıları


def yuzde(x: float) -> str:
    """2.25 -> "%2,25", 2.0 -> "%2"."""
    metin = f"{x:.2f}".rstrip("0").rstrip(".")
    return "%" + metin.replace(".", ",")


def tutar(x: int) -> str:
    return f"{x:,}".replace(",", ".")


def tarih_metin(g: dt.date) -> str:
    return f"{g.day} {AYLAR[g.month - 1]} {g.year}"


def kisa_ad(ad: str) -> str:
    for ek in YASAL_EKLER:
        if ad.endswith(" " + ek):
            return ad[: -len(ek) - 1]
    return ad


def yuvarla(x: float) -> int:
    """İki anlamlı basamağa yukarı yuvarlar: 1.234.567 -> 1.300.000."""
    if x <= 0:
        return 0
    basamak = 10 ** max(0, len(str(int(x))) - 2)
    return int(-(-x // basamak) * basamak)


def is_gunu(rng: random.Random, bas: dt.date, bit: dt.date) -> dt.date:
    g = bas + rng.randint(0, (bit - bas).days) * GUN
    while g.weekday() >= 5:
        g -= GUN
    return g


# ---------------------------------------------------------------- belge kurucu


class _Kurucu:
    def __init__(self, belge_id: str):
        self.belge_id, self.maddeler = belge_id, []

    def ekle(self, baslik: str, metin: str, alan: str | None = None, deger: str | None = None) -> None:
        no = len(self.maddeler) + 1
        self.maddeler.append(Madde(f"{self.belge_id}#m{no}", self.belge_id, no, baslik, metin, alan, deger))


def _sure(k: _Kurucu, rng: random.Random, imza: dt.date, karsi: str) -> None:
    yil = rng.choice((1, 2, 3))
    if rng.random() < 0.7:
        k.ekle("Süre", f"Sözleşme {tarih_metin(imza)} tarihinde yürürlüğe girer ve {yil} yıl süreyle geçerlidir. "
                       f"Taraflardan biri süre bitiminden en az 30 gün önce yazılı olarak bildirmedikçe sözleşme aynı "
                       f"koşullarla birer yıl uzar.",
               "sure", f"{tarih_metin(imza)} başlangıç, {yil} yıl, kendiliğinden yıllık uzar")
    else:
        k.ekle("Süre", f"Sözleşme {tarih_metin(imza)} tarihinde imzalanmış olup {yil} yıl geçerlidir. Süre sonunda "
                       f"sözleşme kendiliğinden sona erer; {SIRKET} ile {karsi} ancak yazılı bir ek protokolle "
                       f"süreyi uzatabilir.",
               "sure", f"{tarih_metin(imza)} başlangıç, {yil} yıl, kendiliğinden uzamaz")


def _gecikme_faizi(k: _Kurucu, rng: random.Random, borclu: str) -> None:
    oran = rng.choice((1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0, 3.5, 4.0))
    if rng.random() < 0.5:
        metin = (f"{borclu}, vadesinde ödemediği tutarlara vade tarihinden fiili ödeme tarihine kadar aylık "
                 f"{yuzde(oran)} oranında temerrüt faizi ödemeyi kabul eder.")
    else:
        metin = (f"Süresinde ödenmeyen fatura bedelleri için, gecikilen her ay ve kıst gün hesabıyla aylık "
                 f"{yuzde(oran)} faiz işletilir ve {borclu} adına ayrıca faturalandırılır.")
    k.ekle("Temerrüt", metin, "gecikme_faizi", f"aylık {yuzde(oran)}")


def _fesih(k: _Kurucu, rng: random.Random) -> None:
    gun = rng.choice((30, 45, 60, 90))
    k.ekle("Fesih", f"Taraflardan her biri, {gun} gün önceden yazılı bildirimde bulunmak kaydıyla sözleşmeyi "
                    f"feshedebilir. Bildirim süresi içinde verilmiş siparişler bu sözleşme hükümlerine göre "
                    f"tamamlanır.", "fesih", f"{gun} gün önceden yazılı bildirim")


def _mahkeme(k: _Kurucu, rng: random.Random) -> None:
    yer = rng.choice(MAHKEMELER)
    k.ekle("Uyuşmazlıkların Çözümü", f"Bu sözleşmenin uygulanmasından doğacak uyuşmazlıklarda {yer} mahkemeleri ve "
                                     f"icra daireleri yetkilidir.", "uyusmazlik", f"{yer} mahkemeleri")


# ---------------------------------------------------------------- tedarikçi sözleşmeleri


def tedarik_sozlesmesi(t: dict, rng: random.Random) -> Belge:
    bid = f"SOZ-{t['tedarikci_id']}"
    k = _Kurucu(bid)
    imza = is_gunu(rng, dt.date(2022, 1, 3), dt.date(2024, 11, 29))
    urun = rng.choice(URUNLER)
    k.ekle("Taraflar ve Konu", f"Bu sözleşme {SIRKET} ile {t['ad']} (TEDARİKÇİ) arasında, TEDARİKÇİ'nin {SIRKET}'e "
                               f"{urun} tedarik etmesine ilişkin esasları düzenler.")
    _sure(k, rng, imza, "TEDARİKÇİ")
    k.ekle("Ödeme Koşulları", f"{SIRKET}, TEDARİKÇİ'nin düzenlediği faturaları fatura tarihinden itibaren "
                              f"{t['vade_gun']} gün içinde banka havalesiyle öder. Ödeme günü hafta sonuna denk gelirse "
                              f"izleyen ilk iş günü ödeme yapılır.", "odeme_vadesi", f"{t['vade_gun']} gün")
    if rng.random() < 0.7:
        gun = rng.choice((10, 15, 20))
        oran = rng.choice((1.0, 1.5, 2.0, 2.5, 3.0))
        k.ekle("Erken Ödeme İskontosu", f"{SIRKET}'in fatura bedelini fatura tarihinden itibaren {gun} gün içinde "
                                        f"ödemesi hâlinde TEDARİKÇİ fatura tutarı üzerinden {yuzde(oran)} iskonto "
                                        f"uygular.", "erken_odeme", f"{gun} gün içinde {yuzde(oran)}")
    else:
        k.ekle("Erken Ödeme İskontosu", "Bu sözleşmede vadeden önce yapılan ödemeler için iskonto uygulanmaz; fiyatlar "
                                        "net fiyattır.", "erken_odeme", "yok")
    _gecikme_faizi(k, rng, SIRKET)
    is_gunu_sayisi = rng.randint(3, 20)
    k.ekle("Teslimat", f"TEDARİKÇİ, {SIRKET}'in yazılı sipariş onayından itibaren en geç {is_gunu_sayisi} iş günü içinde "
                       f"malları {SIRKET}'in deposuna teslim eder. Nakliye ve sigorta giderleri TEDARİKÇİ'ye aittir.",
           "teslim", f"{is_gunu_sayisi} iş günü")
    if rng.random() < 0.5:
        ay = rng.choice(AYLAR)
        k.ekle("Fiyatların Güncellenmesi", f"Birim fiyatlar yılda bir kez, her yıl {ay} ayında, bir önceki on iki aylık "
                                           f"TÜFE değişim oranını geçmemek üzere güncellenebilir.",
               "fiyat", f"yılda bir, {ay} ayında, en çok TÜFE kadar")
    else:
        esik = rng.choice((5, 7, 10))
        k.ekle("Fiyatların Güncellenmesi", f"Döviz kurundaki değişim sipariş tarihine göre %{esik} oranını aşarsa "
                                           f"TEDARİKÇİ yeni fiyat teklifi verebilir; {SIRKET} teklifi kabul etmezse "
                                           f"henüz teslim edilmemiş siparişleri bedelsiz iptal edebilir.",
               "fiyat", f"kur değişimi %{esik} oranını aşarsa yeni teklif")
    ihbar, degisim = rng.randint(2, 10), rng.randint(5, 15)
    k.ekle("Ayıplı Mal", f"{SIRKET}, teslim aldığı mallardaki ayıpları teslim tarihinden itibaren {ihbar} gün içinde "
                         f"TEDARİKÇİ'ye yazılı olarak bildirir. Ayıplı mallar TEDARİKÇİ tarafından {degisim} gün içinde "
                         f"bedelsiz değiştirilir.", "ayip", f"{ihbar} gün içinde bildirim, {degisim} gün içinde değişim")
    gunluk, ust = rng.choice((0.1, 0.2, 0.3, 0.5)), rng.choice((10, 15, 20))
    k.ekle("Cezai Şart", f"Teslimatın gecikmesi hâlinde TEDARİKÇİ, geciken her gün için gecikmeli sipariş bedelinin "
                         f"{yuzde(gunluk)} oranında cezai şart öder; cezai şartın toplamı sipariş bedelinin %{ust} "
                         f"oranını geçemez.", "cezai_sart", f"günlük {yuzde(gunluk)}, en çok %{ust}")
    _fesih(k, rng)
    yil = rng.choice((2, 3, 5))
    k.ekle("Gizlilik", f"Taraflar, sözleşme kapsamında öğrendikleri fiyat, müşteri ve ürün bilgilerini sözleşmenin sona "
                       f"ermesinden sonra {yil} yıl boyunca gizli tutar.", "gizlilik", f"{yil} yıl")
    _mahkeme(k, rng)
    return Belge(bid, "tedarik_sozlesmesi", "Mal Alım Çerçeve Sözleşmesi", t["tedarikci_id"], t["ad"], kisa_ad(t["ad"]),
                 ("cfo",), imza.isoformat(), tuple(k.maddeler))


HIZMET_BASLIK = {"kira": "Kira Sözleşmesi", "enerji": "Elektrik Enerjisi Satış Sözleşmesi",
                 "lojistik": "Taşımacılık Hizmet Sözleşmesi", "yazilim": "Yazılım Hizmet Sözleşmesi",
                 "telekom": "Telekomünikasyon Hizmet Sözleşmesi"}


def hizmet_sozlesmesi(t: dict, rng: random.Random) -> Belge:
    bid = f"SOZ-{t['tedarikci_id']}"
    k = _Kurucu(bid)
    imza = is_gunu(rng, dt.date(2022, 1, 3), dt.date(2024, 11, 29))
    kat = t["kategori"]
    konu = {"kira": "merkez ofis ve depo olarak kullanılan taşınmazın kiralanmasını",
            "enerji": "merkez ofis ve depoya elektrik enerjisi sağlanmasını",
            "lojistik": "müşteri gönderilerinin taşınmasını ve teslimini",
            "yazilim": "muhasebe ve stok yazılımının bulut üzerinden sunulmasını ve desteğini",
            "telekom": "sabit internet, telefon ve veri hattı hizmetlerini"}[kat]
    k.ekle("Taraflar ve Konu", f"Bu sözleşme {SIRKET} ile {t['ad']} (HİZMET VEREN) arasında, {konu} düzenler.")
    _sure(k, rng, imza, "HİZMET VEREN")
    k.ekle("Ödeme Koşulları", f"Hizmet bedeli aylık olarak faturalandırılır; {SIRKET} faturaları fatura tarihinden "
                              f"itibaren {t['vade_gun']} gün içinde öder.", "odeme_vadesi", f"{t['vade_gun']} gün")
    _gecikme_faizi(k, rng, SIRKET)
    if kat == "kira":
        ay = rng.choice(AYLAR)
        k.ekle("Kira Artışı", f"Aylık kira bedeli her yıl {ay} ayında, bir önceki yılın on iki aylık TÜFE ortalaması "
                              f"oranında artırılır.", "kira_artisi", f"her yıl {ay} ayında, TÜFE ortalaması kadar")
        ay_sayisi = rng.choice((2, 3))
        k.ekle("Depozito", f"{SIRKET}, sözleşmenin imzalanmasıyla birlikte {ay_sayisi} aylık kira bedeli tutarında "
                           f"depozito öder; depozito tahliyede, hasar tespiti yapıldıktan sonra faizsiz iade edilir.",
               "depozito", f"{ay_sayisi} aylık kira")
    elif kat == "enerji":
        okuma = rng.choice((1, 5, 10, 15))
        k.ekle("Okuma ve Faturalama", f"Sayaçlar her ayın {okuma}. günü okunur; fatura okumadan sonraki 10 gün içinde "
                                      f"düzenlenir.", "okuma", f"her ayın {okuma}. günü")
        saat = rng.choice((24, 48, 72))
        k.ekle("Planlı Kesintiler", f"Bakım nedeniyle yapılacak planlı kesintiler {SIRKET}'e en az {saat} saat önceden "
                                    f"yazılı olarak bildirilir.", "kesinti", f"en az {saat} saat önceden")
    elif kat == "lojistik":
        gun = rng.randint(1, 5)
        k.ekle("Teslim Süresi", f"Gönderiler, HİZMET VEREN'in teslim aldığı tarihten itibaren yurt içinde en geç {gun} "
                                f"iş günü içinde alıcısına ulaştırılır.", "teslim", f"{gun} iş günü")
        limit = rng.choice((5_000, 10_000, 25_000, 50_000))
        k.ekle("Hasar ve Kayıp", f"Taşıma sırasında oluşan hasar ve kayıplarda HİZMET VEREN'in sorumluluğu gönderi başına "
                                 f"{tutar(limit)} TL ile sınırlıdır.", "hasar_limiti", f"gönderi başına {tutar(limit)} TL")
    elif kat == "yazilim":
        oran, indirim = rng.choice((99.0, 99.5, 99.9)), rng.choice((5, 10, 15))
        k.ekle("Hizmet Seviyesi", f"Hizmet aylık {yuzde(oran)} erişilebilirlik taahhüdüyle sunulur. Taahhüdün altına "
                                  f"düşülen her ay için o ayın hizmet bedelinden %{indirim} indirim yapılır.",
               "sla", f"aylık {yuzde(oran)} erişilebilirlik, altında %{indirim} indirim")
        saat = rng.choice((2, 4, 8))
        k.ekle("Destek", f"Kritik arızalara bildirimden itibaren en geç {saat} saat içinde müdahale edilir; diğer "
                         f"talepler iki iş günü içinde yanıtlanır.", "destek", f"kritik arızada {saat} saat")
    elif kat == "telekom":
        ay_sayisi, oran = rng.choice((12, 24, 36)), rng.choice((25, 50, 100))
        k.ekle("Taahhüt", f"{SIRKET} {ay_sayisi} ay süreyle hizmeti kullanmayı taahhüt eder. Taahhüt süresi dolmadan "
                          f"hizmetin sonlandırılması hâlinde kalan aylara ait bedelin %{oran} kadarı cayma bedeli olarak "
                          f"ödenir.", "taahhut", f"{ay_sayisi} ay, cayma bedeli kalan bedelin %{oran} kadarı")
    _fesih(k, rng)
    _mahkeme(k, rng)
    return Belge(bid, "hizmet_sozlesmesi", HIZMET_BASLIK[kat], t["tedarikci_id"], t["ad"], kisa_ad(t["ad"]), ("cfo",),
                 imza.isoformat(), tuple(k.maddeler))


# ---------------------------------------------------------------- müşteri sözleşmeleri


def satis_sozlesmesi(m: dict, aylik_satis: float, cekli: bool, rng: random.Random) -> Belge:
    bid = f"SOZ-{m['musteri_id']}"
    k = _Kurucu(bid)
    imza = is_gunu(rng, dt.date(2022, 1, 3), dt.date(2024, 11, 29))
    pb = "TL" if m["para_birimi"] == "TRY" else m["para_birimi"]
    k.ekle("Taraflar ve Konu", f"Bu sözleşme {SIRKET} ile {m['ad']} (MÜŞTERİ) arasında, {SIRKET}'in MÜŞTERİ'ye ürün "
                               f"satışına ilişkin çerçeve koşulları düzenler. Her sipariş bu koşullara tabidir.")
    _sure(k, rng, imza, "MÜŞTERİ")
    if m["vade_gun"] == 0:
        k.ekle("Ödeme Koşulları", "Siparişler peşin ödenir; ödeme hesaba geçmeden sevkiyat yapılmaz.",
               "odeme_vadesi", "peşin")
    else:
        k.ekle("Ödeme Koşulları", f"MÜŞTERİ, faturaları fatura tarihinden itibaren {m['vade_gun']} gün içinde öder.",
               "odeme_vadesi", f"{m['vade_gun']} gün")
    if not m["yurtici"]:
        teslim = rng.choice(("EXW İstanbul", "FCA İstanbul", "CIF", "DAP"))
        k.ekle("Teslim Şekli ve Para Birimi", f"Teslim şekli {teslim} (Incoterms 2020) olup fiyatlar ve faturalar {pb} "
                                              f"cinsindendir.", "teslim_sekli", f"{teslim}, {pb}")
    if m["vade_gun"] > 0:
        limit = yuvarla(aylik_satis * (m["vade_gun"] / 30 + 1))
        k.ekle("Kredi Limiti", f"MÜŞTERİ'nin açık hesap bakiyesi {tutar(limit)} {pb} tutarını aşamaz. Limit aşımı "
                               f"hâlinde yeni sevkiyat {SIRKET}'in yazılı onayına bağlıdır.",
               "kredi_limiti", f"{tutar(limit)} {pb}")
    if m["yurtici"]:
        if cekli:
            gun = rng.choice((60, 90, 120))
            k.ekle("Çekle Ödeme", f"MÜŞTERİ ödemelerini vadesi en fazla {gun} gün olan kendi keşide ettiği çeklerle "
                                  f"yapabilir; çek bedeli tahsil edilene kadar borç ödenmiş sayılmaz.",
                   "cek", f"kabul, en fazla {gun} gün vadeli")
        else:
            k.ekle("Çekle Ödeme", "Ödemeler yalnızca banka havalesiyle yapılır; çek ve senet kabul edilmez.",
                   "cek", "kabul edilmez")
    _gecikme_faizi(k, rng, "MÜŞTERİ")
    gun = rng.choice((7, 14, 15, 30))
    k.ekle("İade", f"MÜŞTERİ, teslim aldığı ürünleri teslim tarihinden itibaren {gun} gün içinde, kullanılmamış ve "
                   f"ambalajı açılmamış olmak kaydıyla iade edebilir.", "iade", f"{gun} gün")
    if m["yurtici"]:
        if rng.random() < 0.5 and m["vade_gun"] > 0:
            teminat = yuvarla(aylik_satis * rng.choice((0.5, 1.0)))
            k.ekle("Teminat", f"MÜŞTERİ, sözleşme süresince {tutar(teminat)} {pb} tutarında süresiz banka teminat "
                              f"mektubu verir.", "teminat", f"{tutar(teminat)} {pb} teminat mektubu")
        else:
            k.ekle("Teminat", "Bu sözleşme kapsamında MÜŞTERİ'den teminat alınmaz.", "teminat", "yok")
    _fesih(k, rng)
    if m["yurtici"]:
        _mahkeme(k, rng)
    else:
        kurum = rng.choice(("İstanbul Tahkim Merkezi (ISTAC)", "ICC Milletlerarası Tahkim Divanı"))
        dil = rng.choice(("İngilizce", "Türkçe"))
        k.ekle("Uygulanacak Hukuk ve Tahkim", f"Sözleşmeye Türk hukuku uygulanır. Uyuşmazlıklar {kurum} kurallarına göre, "
                                              f"İstanbul'da ve {dil} olarak tahkim yoluyla çözülür.",
               "uyusmazlik", f"{kurum}, İstanbul, {dil}")
    roller = ("cfo", "satis_yurtici") if m["yurtici"] else ("cfo", "satis_ihracat")
    return Belge(bid, "satis_sozlesmesi", "Satış Çerçeve Sözleşmesi", m["musteri_id"], m["ad"], kisa_ad(m["ad"]), roller,
                 imza.isoformat(), tuple(k.maddeler))


# ---------------------------------------------------------------- politikalar


TUM_ROLLER = ("cfo", "satis_yurtici", "satis_ihracat")


def politikalar(rng: random.Random) -> list[Belge]:
    sonuc = []

    def belge(bid, baslik, roller, yururluk, kur):
        k = _Kurucu(bid)
        kur(k)
        sonuc.append(Belge(bid, "politika", baslik, None, None, None, roller, yururluk.isoformat(), tuple(k.maddeler)))

    def nakit(k):
        oran = IzlemeAyarlari().asgari_nakit_orani
        k.ekle("Amaç", "Bu politika, şirketin günlük ödemelerini aksatmadan nakdini nasıl tutacağını ve fazla nakdi nasıl "
                       "değerlendireceğini belirler.")
        k.ekle("Güvenlik Eşiği", f"Ana TL hesabının gün içindeki en düşük bakiyesi, son on iki ayın ortalama aylık net "
                                 f"satışının {yuzde(oran * 100)} oranından az olmamalıdır. Eşiğin altına inilmesi "
                                 f"hâlinde CFO aynı gün bilgilendirilir.", "guvenlik_esigi",
               f"son 12 ayın ortalama aylık net satışının {yuzde(oran * 100)}")
        ust = rng.choice((40, 50, 60))
        k.ekle("Banka Dağılımı", f"Toplam nakdin %{ust} oranından fazlası tek bir bankada tutulmaz.", "banka_dagilimi",
               f"tek bankada en çok %{ust}")
        gun = rng.choice((32, 45, 92))
        k.ekle("Fazla Nakdin Değerlendirilmesi", f"Fazla nakit yalnızca vadesi {gun} günü aşmayan TL ya da döviz vadeli "
                                                 f"mevduatta değerlendirilir; hisse senedi ve yatırım fonuna yatırım "
                                                 f"yapılmaz.", "fazla_nakit", f"en çok {gun} gün vadeli mevduat")
        saat, ek = rng.choice((("10.00", "a"), ("11.00", "e"), ("12.00", "ye")))
        k.ekle("Raporlama", f"Hazine birimi her iş günü saat {saat}'{ek} kadar banka bakiyelerini ve günün ödemelerini "
                            f"gösteren nakit raporunu CFO'ya iletir.", "nakit_raporu", f"her iş günü saat {saat}")

    def onay(k):
        a, b, c = rng.choice(((50_000, 250_000, 1_000_000), (100_000, 500_000, 2_000_000), (75_000, 400_000, 1_500_000)))
        k.ekle("Amaç", "Bu yönetmelik, şirketten yapılan ödemelerin kimin onayıyla yapılabileceğini belirler.")
        k.ekle("Onay Limitleri", f"{tutar(a)} TL'ye kadar olan ödemeleri finans uzmanı, {tutar(b)} TL'ye kadar olanları "
                                 f"finans müdürü, {tutar(c)} TL'ye kadar olanları CFO onaylar.", "onay_limitleri",
               f"uzman {tutar(a)} TL, müdür {tutar(b)} TL, CFO {tutar(c)} TL")
        k.ekle("Çift İmza", f"{tutar(c)} TL'yi aşan ödemeler CFO ile genel müdürün birlikte onayını gerektirir.",
               "cift_imza", f"{tutar(c)} TL üzeri CFO ve genel müdür")
        k.ekle("Yeni Karşı Tarafa İlk Ödeme", "Daha önce ödeme yapılmamış bir alıcıya yapılacak ilk ödemede banka "
                                              "hesabı, karşı tarafın yetkilisi telefonla aranarak teyit edilir ve tutar "
                                              "ne olursa olsun CFO onayı alınır.", "ilk_odeme",
               "telefonla hesap teyidi ve her tutarda CFO onayı")
        k.ekle("Mükerrer Ödeme Kontrolü", "Aynı tedarikçi faturasına ikinci ödeme sistemde engellenir; engelin "
                                          "kaldırılması yalnızca CFO'nun yazılı onayıyla mümkündür.", "mukerrer",
               "sistemde engellenir, istisna yalnızca CFO yazılı onayıyla")

    def alacak(k):
        h, ihtar, durdur, hukuk = rng.choice(((7, 30, 60, 90), (15, 30, 60, 120), (10, 45, 75, 120)))
        k.ekle("Amaç", "Bu politika, vadesi geçen müşteri alacaklarının hangi adımlarla takip edileceğini belirler.")
        k.ekle("Takip Adımları", f"Vadesi {h} günü geçen alacak için müşteriye hatırlatma e-postası gönderilir; {ihtar} günü "
                                 f"geçince müşteri aranır ve yazılı ihtar çekilir.", "takip_adimlari",
               f"{h}. gün hatırlatma, {ihtar}. gün ihtar")
        k.ekle("Sevkiyatın Durdurulması", f"Vadesi {durdur} günü geçen alacağı olan müşteriye, borç kapanana kadar yeni "
                                          f"sevkiyat yapılmaz.", "sevkiyat_durdurma", f"{durdur}. günden sonra")
        k.ekle("Hukuki Takip", f"Vadesi {hukuk} günü geçen alacaklar hukuk birimine devredilir ve icra takibi başlatılır.",
               "hukuki_takip", f"{hukuk}. günden sonra")
        asim = rng.choice((5, 10, 15))
        k.ekle("Kredi Limiti Aşımı", f"Kredi limitinin %{asim} oranına kadar aşılmasını satış müdürü, daha fazlasını CFO "
                                     f"onaylar.", "limit_asimi", f"%{asim} oranına kadar satış müdürü, üstü CFO")

    def cek(k):
        k.ekle("Amaç", "Bu politika, müşterilerden çek kabulünün koşullarını ve karşılıksız çıkan çeklerde izlenecek "
                       "yolu belirler.")
        en_uzun = 120
        k.ekle("En Uzun Vade", f"Vadesi düzenleme tarihinden itibaren {en_uzun} günü aşan çek kabul edilmez.",
               "cek_vadesi", f"en çok {en_uzun} gün")
        ciro = rng.choice((0, 1))
        k.ekle("Ciro", "Yalnızca müşterinin kendi keşide ettiği çekler kabul edilir; ciro edilmiş çek alınmaz." if ciro == 0
               else "Müşterinin kendi çeki dışında en fazla bir kez ciro edilmiş çek kabul edilebilir.",
               "ciro", "ciro edilmiş çek alınmaz" if ciro == 0 else "en fazla bir ciro")
        k.ekle("Tahsile Verme", "Portföydeki çekler vadesinden üç iş günü önce tahsil edilmek üzere bankaya verilir.",
               "tahsile_verme", "vadeden üç iş günü önce")
        ay = rng.choice((3, 6, 12))
        k.ekle("Karşılıksız Çek", f"Çeki karşılıksız çıkan müşteriyle {ay} ay boyunca yalnızca peşin çalışılır ve müşterinin "
                                  f"kredi limiti sıfırlanır.", "karsiliksiz", f"{ay} ay yalnızca peşin")

    def kur(k):
        k.ekle("Amaç", "Bu politika, döviz cinsinden alacak ve borçlardan doğan kur riskinin sınırlarını belirler.")
        oran = rng.choice((15, 20, 25))
        k.ekle("Açık Pozisyon Sınırı", f"Net açık döviz pozisyonu, son on iki ayın ortalama aylık satışının %{oran} "
                                       f"oranını geçemez.", "pozisyon_siniri", f"aylık satışın %{oran}")
        koruma = rng.choice((30, 50, 70))
        k.ekle("Korunma Oranı", f"İhracat alacaklarının en az %{koruma} kadarı vadeli döviz satışıyla korunur.",
               "korunma_orani", f"en az %{koruma}")
        k.ekle("İzin Verilen Araçlar", "Korunma için yalnızca vadeli işlem (forward) ve swap kullanılabilir; opsiyon ve "
                                       "kaldıraçlı ürünler yasaktır.", "araclar", "yalnızca forward ve swap")

    def tedarikci(k):
        k.ekle("Amaç", "Bu prosedür, yeni tedarikçi kaydı ve tedarikçi bilgilerinin değiştirilmesi adımlarını belirler.")
        k.ekle("Kayıt Belgeleri", "Yeni tedarikçi kaydı için vergi levhası, imza sirküleri ve banka hesap teyit yazısı "
                                  "istenir.", "kayit_belgeleri", "vergi levhası, imza sirküleri, banka teyit yazısı")
        gun = rng.choice((2, 3, 5))
        k.ekle("Banka Hesabı Değişikliği", f"Tedarikçinin banka hesabı değişikliği yazılı talep ve telefonla teyitten "
                                           f"sonra sisteme girilir; yeni hesaba ilk ödeme en erken {gun} iş günü sonra "
                                           f"yapılır.", "iban_degisikligi", f"teyitten sonra {gun} iş günü bekleme")
        pay = rng.choice((25, 30, 40))
        k.ekle("Yoğunlaşma Sınırı", f"Tek bir tedarikçiden yapılan alımlar yıllık mal alımlarının %{pay} oranını geçmez.",
               "yogunlasma", f"en çok %{pay}")

    yururluk = dt.date(2024, 1, 2)
    belge("POL-NAKIT", "Nakit Yönetimi Politikası", ("cfo",), yururluk, nakit)
    belge("POL-ONAY", "Ödeme Onay Yetkileri Yönetmeliği", ("cfo",), yururluk, onay)
    belge("POL-ALACAK", "Alacak Takip ve Tahsilat Politikası", TUM_ROLLER, yururluk, alacak)
    belge("POL-CEK", "Çek Kabul Politikası", TUM_ROLLER, yururluk, cek)
    belge("POL-KUR", "Kur Riski Politikası", ("cfo",), yururluk, kur)
    belge("POL-TEDARIKCI", "Tedarikçi Kayıt ve Değişiklik Prosedürü", ("cfo",), yururluk, tedarikci)
    return sonuc


# ---------------------------------------------------------------- hepsi


def uret(tablolar: dict[str, list[dict]], tohum: int = 42) -> list[Belge]:
    """Şirket verisinden bütün sentetik belgeler. Aynı veri ve tohum aynı belgeleri verir."""
    rng = random.Random(f"{tohum}:belge")
    belgeler = []
    for t in sorted(tablolar["tedarikciler"], key=lambda t: t["tedarikci_id"]):
        belgeler.append(tedarik_sozlesmesi(t, rng) if t["kategori"] == "mal" else hizmet_sozlesmesi(t, rng))

    satis_tutari: dict[str, int] = defaultdict(int)  # fatura para biriminde
    tarihler = [f["fatura_tarihi"] for f in tablolar["faturalar"]]
    ay_sayisi = ((max(tarihler) - min(tarihler)).days + 1) / 30.4375
    for f in tablolar["faturalar"]:
        if f["tur"] == "satis":
            satis_tutari[f["musteri_id"]] += f["tutar_kurus"]
    try_satis: dict[str, int] = defaultdict(int)
    for f in tablolar["faturalar"]:
        if f["tur"] == "satis":
            try_satis[f["musteri_id"]] += f["tutar_try_kurus"]
    cekli = {c["musteri_id"] for c in tablolar["cekler"]}
    musteri = {m["musteri_id"]: m for m in tablolar["musteriler"]}
    sirali = sorted(try_satis, key=lambda mid: (-try_satis[mid], mid))
    secilen = ([mid for mid in sirali if musteri[mid]["yurtici"]][:SATIS_YURTICI]
               + [mid for mid in sirali if not musteri[mid]["yurtici"]][:SATIS_IHRACAT])
    for mid in sorted(secilen):
        aylik = satis_tutari[mid] / 100 / ay_sayisi
        belgeler.append(satis_sozlesmesi(musteri[mid], aylik, mid in cekli, rng))
    belgeler.extend(politikalar(rng))
    return belgeler


def arama_metni(b: Belge, m: Madde) -> str:
    """Aramada kullanılan metin: belgenin adı ve tarafı + maddenin başlığı ve metni. Alıntı kontrolü yalnızca
    `m.metin`e bakar."""
    bas = f"{b.baslik} — {b.taraf_ad}" if b.taraf_ad else b.baslik
    return f"{bas}\nMadde {m.no}. {m.baslik}\n{m.metin}"
