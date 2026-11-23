"""Synthetic contracts and company policies.

Framework contracts are generated for the company's suppliers and its largest customers, and also the company's
finance policies. Every document is split into articles; every article is a search chunk and its identifier is fixed
("<document_id>#mN").

- The company data (data/uretilen) is only read, never changed. Values that are already in the data are taken from
  there: the due days of suppliers and customers, the customer's currency, whether the customer pays by cheque. So
  contracts and records do not contradict each other.
- The remaining values (interest, term, court...) are drawn from the module's own random stream ("{seed}:belge"); it
  does not touch the streams of the company data.
- The safety threshold in the cash policy is the ratio used by the monitoring rule (Phase 4).

Every article has an `alan` (field, e.g. "gecikme_faizi") and a `deger` (value, e.g. "aylık %2,25"); the question sets
are generated from them. Articles with no field (parties, purpose) get no questions but can be searched.
"""

from __future__ import annotations

import datetime as dt
import random
from collections import defaultdict
from dataclasses import asdict, dataclass

from sayman.monitoring.rules import RuleSettings as IzlemeAyarlari

DAY = dt.timedelta(days=1)
COMPANY = "ŞİRKET"
LEGAL_SUFFIXES = ("San. ve Tic. Ltd. Şti.", "Ltd. Şti.", "A.Ş.", "Pty Ltd", "GmbH", "SARL", "BV", "SL", "Lda", "AG",
               "SRL", "Inc", "Ltd")
PRODUCTS = ("kırtasiye ve ofis malzemesi", "ambalaj malzemesi", "ev dekorasyon ürünleri", "mutfak gereçleri",
           "hediyelik eşya", "tekstil ürünleri", "oyuncak ve parti malzemesi", "aydınlatma ürünleri")
COURTS = ("İstanbul (Çağlayan)", "İstanbul Anadolu", "Ankara", "İzmir", "Bursa", "Kocaeli")
MONTHS = ("Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık")
DOMESTIC_CONTRACT_CUSTOMERS, EXPORT_CONTRACT_CUSTOMERS = 20, 10  # number of largest customers that have a contract


@dataclass(frozen=True)
class Article:
    parca_id: str
    belge_id: str
    no: int
    baslik: str
    metin: str
    alan: str | None
    deger: str | None


@dataclass(frozen=True)
class Document:
    belge_id: str
    tur: str  # tedarik_sozlesmesi, hizmet_sozlesmesi, satis_sozlesmesi, politika
    baslik: str
    taraf_id: str | None
    taraf_ad: str | None
    kisa_ad: str | None
    roller: tuple[str, ...]
    tarih: str
    maddeler: tuple[Article, ...]

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- formatting helpers


def format_percent(x: float) -> str:
    """2.25 -> "%2,25", 2.0 -> "%2"."""
    text = f"{x:.2f}".rstrip("0").rstrip(".")
    return "%" + text.replace(".", ",")


def format_thousands(x: int) -> str:
    return f"{x:,}".replace(",", ".")


def format_date(g: dt.date) -> str:
    return f"{g.day} {MONTHS[g.month - 1]} {g.year}"


def short_name(name: str) -> str:
    for suffix in LEGAL_SUFFIXES:
        if name.endswith(" " + suffix):
            return name[: -len(suffix) - 1]
    return name


def round_up_two_digits(x: float) -> int:
    """Rounds up to two significant digits: 1.234.567 -> 1.300.000."""
    if x <= 0:
        return 0
    unit = 10 ** max(0, len(str(int(x))) - 2)
    return int(-(-x // unit) * unit)


def business_day(rng: random.Random, bas: dt.date, bit: dt.date) -> dt.date:
    g = bas + rng.randint(0, (bit - bas).days) * DAY
    while g.weekday() >= 5:
        g -= DAY
    return g


# ---------------------------------------------------------------- document builder


class _Builder:
    def __init__(self, document_id: str):
        self.belge_id, self.maddeler = document_id, []

    def add(self, title: str, text: str, alan: str | None = None, value: str | None = None) -> None:
        no = len(self.maddeler) + 1
        self.maddeler.append(Article(f"{self.belge_id}#m{no}", self.belge_id, no, title, text, alan, value))


def _term(k: _Builder, rng: random.Random, signed: dt.date, counterparty: str) -> None:
    years = rng.choice((1, 2, 3))
    if rng.random() < 0.7:
        k.add("Süre", f"Sözleşme {format_date(signed)} tarihinde yürürlüğe girer ve {years} yıl süreyle geçerlidir. "
                       f"Taraflardan biri süre bitiminden en az 30 gün önce yazılı olarak bildirmedikçe sözleşme aynı "
                       f"koşullarla birer yıl uzar.",
               "sure", f"{format_date(signed)} başlangıç, {years} yıl, kendiliğinden yıllık uzar")
    else:
        k.add("Süre", f"Sözleşme {format_date(signed)} tarihinde imzalanmış olup {years} yıl geçerlidir. Süre sonunda "
                       f"sözleşme kendiliğinden sona erer; {COMPANY} ile {counterparty} ancak yazılı bir ek protokolle "
                       f"süreyi uzatabilir.",
               "sure", f"{format_date(signed)} başlangıç, {years} yıl, kendiliğinden uzamaz")


def _late_interest(k: _Builder, rng: random.Random, debtor: str) -> None:
    rate = rng.choice((1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0, 3.5, 4.0))
    if rng.random() < 0.5:
        text = (f"{debtor}, vadesinde ödemediği tutarlara vade tarihinden fiili ödeme tarihine kadar aylık "
                 f"{format_percent(rate)} oranında temerrüt faizi ödemeyi kabul eder.")
    else:
        text = (f"Süresinde ödenmeyen fatura bedelleri için, gecikilen her ay ve kıst gün hesabıyla aylık "
                 f"{format_percent(rate)} faiz işletilir ve {debtor} adına ayrıca faturalandırılır.")
    k.add("Temerrüt", text, "gecikme_faizi", f"aylık {format_percent(rate)}")


def _termination(k: _Builder, rng: random.Random) -> None:
    days = rng.choice((30, 45, 60, 90))
    k.add("Fesih", f"Taraflardan her biri, {days} gün önceden yazılı bildirimde bulunmak kaydıyla sözleşmeyi "
                    f"feshedebilir. Bildirim süresi içinde verilmiş siparişler bu sözleşme hükümlerine göre "
                    f"tamamlanır.", "fesih", f"{days} gün önceden yazılı bildirim")


def _jurisdiction(k: _Builder, rng: random.Random) -> None:
    venue = rng.choice(COURTS)
    k.add("Uyuşmazlıkların Çözümü", f"Bu sözleşmenin uygulanmasından doğacak uyuşmazlıklarda {venue} mahkemeleri ve "
                                     f"icra daireleri yetkilidir.", "uyusmazlik", f"{venue} mahkemeleri")


# ---------------------------------------------------------------- supplier contracts


def supplier_contract(t: dict, rng: random.Random) -> Document:
    doc_id = f"SOZ-{t['tedarikci_id']}"
    k = _Builder(doc_id)
    signed = business_day(rng, dt.date(2022, 1, 3), dt.date(2024, 11, 29))
    product = rng.choice(PRODUCTS)
    k.add("Taraflar ve Konu", f"Bu sözleşme {COMPANY} ile {t['ad']} (TEDARİKÇİ) arasında, TEDARİKÇİ'nin {COMPANY}'e "
                               f"{product} tedarik etmesine ilişkin esasları düzenler.")
    _term(k, rng, signed, "TEDARİKÇİ")
    k.add("Ödeme Koşulları", f"{COMPANY}, TEDARİKÇİ'nin düzenlediği faturaları fatura tarihinden itibaren "
                              f"{t['vade_gun']} gün içinde banka havalesiyle öder. Ödeme günü hafta sonuna denk gelirse "
                              f"izleyen ilk iş günü ödeme yapılır.", "odeme_vadesi", f"{t['vade_gun']} gün")
    if rng.random() < 0.7:
        days = rng.choice((10, 15, 20))
        rate = rng.choice((1.0, 1.5, 2.0, 2.5, 3.0))
        k.add("Erken Ödeme İskontosu", f"{COMPANY}'in fatura bedelini fatura tarihinden itibaren {days} gün içinde "
                                        f"ödemesi hâlinde TEDARİKÇİ fatura tutarı üzerinden {format_percent(rate)} iskonto "
                                        f"uygular.", "erken_odeme", f"{days} gün içinde {format_percent(rate)}")
    else:
        k.add("Erken Ödeme İskontosu", "Bu sözleşmede vadeden önce yapılan ödemeler için iskonto uygulanmaz; fiyatlar "
                                        "net fiyattır.", "erken_odeme", "yok")
    _late_interest(k, rng, COMPANY)
    business_days = rng.randint(3, 20)
    k.add("Teslimat", f"TEDARİKÇİ, {COMPANY}'in yazılı sipariş onayından itibaren en geç {business_days} iş günü içinde "
                       f"malları {COMPANY}'in deposuna teslim eder. Nakliye ve sigorta giderleri TEDARİKÇİ'ye aittir.",
           "teslim", f"{business_days} iş günü")
    if rng.random() < 0.5:
        month = rng.choice(MONTHS)
        k.add("Fiyatların Güncellenmesi", f"Birim fiyatlar yılda bir kez, her yıl {month} ayında, bir önceki on iki aylık "
                                           f"TÜFE değişim oranını geçmemek üzere güncellenebilir.",
               "fiyat", f"yılda bir, {month} ayında, en çok TÜFE kadar")
    else:
        threshold = rng.choice((5, 7, 10))
        k.add("Fiyatların Güncellenmesi", f"Döviz kurundaki değişim sipariş tarihine göre %{threshold} oranını aşarsa "
                                           f"TEDARİKÇİ yeni fiyat teklifi verebilir; {COMPANY} teklifi kabul etmezse "
                                           f"henüz teslim edilmemiş siparişleri bedelsiz iptal edebilir.",
               "fiyat", f"kur değişimi %{threshold} oranını aşarsa yeni teklif")
    notice_days, replacement_days = rng.randint(2, 10), rng.randint(5, 15)
    k.add("Ayıplı Mal", f"{COMPANY}, teslim aldığı mallardaki ayıpları teslim tarihinden itibaren {notice_days} gün içinde "
                         f"TEDARİKÇİ'ye yazılı olarak bildirir. Ayıplı mallar TEDARİKÇİ tarafından {replacement_days} gün içinde "
                         f"bedelsiz değiştirilir.", "ayip", f"{notice_days} gün içinde bildirim, {replacement_days} gün içinde değişim")
    daily, upper = rng.choice((0.1, 0.2, 0.3, 0.5)), rng.choice((10, 15, 20))
    k.add("Cezai Şart", f"Teslimatın gecikmesi hâlinde TEDARİKÇİ, geciken her gün için gecikmeli sipariş bedelinin "
                         f"{format_percent(daily)} oranında cezai şart öder; cezai şartın toplamı sipariş bedelinin %{upper} "
                         f"oranını geçemez.", "cezai_sart", f"günlük {format_percent(daily)}, en çok %{upper}")
    _termination(k, rng)
    years = rng.choice((2, 3, 5))
    k.add("Gizlilik", f"Taraflar, sözleşme kapsamında öğrendikleri fiyat, müşteri ve ürün bilgilerini sözleşmenin sona "
                       f"ermesinden sonra {years} yıl boyunca gizli tutar.", "gizlilik", f"{years} yıl")
    _jurisdiction(k, rng)
    return Document(doc_id, "tedarik_sozlesmesi", "Mal Alım Çerçeve Sözleşmesi", t["tedarikci_id"], t["ad"], short_name(t["ad"]),
                 ("cfo",), signed.isoformat(), tuple(k.maddeler))


SERVICE_TITLES = {"kira": "Kira Sözleşmesi", "enerji": "Elektrik Enerjisi Satış Sözleşmesi",
                 "lojistik": "Taşımacılık Hizmet Sözleşmesi", "yazilim": "Yazılım Hizmet Sözleşmesi",
                 "telekom": "Telekomünikasyon Hizmet Sözleşmesi"}


def service_contract(t: dict, rng: random.Random) -> Document:
    doc_id = f"SOZ-{t['tedarikci_id']}"
    k = _Builder(doc_id)
    signed = business_day(rng, dt.date(2022, 1, 3), dt.date(2024, 11, 29))
    category = t["kategori"]
    subject = {"kira": "merkez ofis ve depo olarak kullanılan taşınmazın kiralanmasını",
            "enerji": "merkez ofis ve depoya elektrik enerjisi sağlanmasını",
            "lojistik": "müşteri gönderilerinin taşınmasını ve teslimini",
            "yazilim": "muhasebe ve stok yazılımının bulut üzerinden sunulmasını ve desteğini",
            "telekom": "sabit internet, telefon ve veri hattı hizmetlerini"}[category]
    k.add("Taraflar ve Konu", f"Bu sözleşme {COMPANY} ile {t['ad']} (HİZMET VEREN) arasında, {subject} düzenler.")
    _term(k, rng, signed, "HİZMET VEREN")
    k.add("Ödeme Koşulları", f"Hizmet bedeli aylık olarak faturalandırılır; {COMPANY} faturaları fatura tarihinden "
                              f"itibaren {t['vade_gun']} gün içinde öder.", "odeme_vadesi", f"{t['vade_gun']} gün")
    _late_interest(k, rng, COMPANY)
    if category == "kira":
        month = rng.choice(MONTHS)
        k.add("Kira Artışı", f"Aylık kira bedeli her yıl {month} ayında, bir önceki yılın on iki aylık TÜFE ortalaması "
                              f"oranında artırılır.", "kira_artisi", f"her yıl {month} ayında, TÜFE ortalaması kadar")
        month_count = rng.choice((2, 3))
        k.add("Depozito", f"{COMPANY}, sözleşmenin imzalanmasıyla birlikte {month_count} aylık kira bedeli tutarında "
                           f"depozito öder; depozito tahliyede, hasar tespiti yapıldıktan sonra faizsiz iade edilir.",
               "depozito", f"{month_count} aylık kira")
    elif category == "enerji":
        reading_day = rng.choice((1, 5, 10, 15))
        k.add("Okuma ve Faturalama", f"Sayaçlar her ayın {reading_day}. günü okunur; fatura okumadan sonraki 10 gün içinde "
                                      f"düzenlenir.", "okuma", f"her ayın {reading_day}. günü")
        hours = rng.choice((24, 48, 72))
        k.add("Planlı Kesintiler", f"Bakım nedeniyle yapılacak planlı kesintiler {COMPANY}'e en az {hours} saat önceden "
                                    f"yazılı olarak bildirilir.", "kesinti", f"en az {hours} saat önceden")
    elif category == "lojistik":
        days = rng.randint(1, 5)
        k.add("Teslim Süresi", f"Gönderiler, HİZMET VEREN'in teslim aldığı tarihten itibaren yurt içinde en geç {days} "
                                f"iş günü içinde alıcısına ulaştırılır.", "teslim", f"{days} iş günü")
        limit = rng.choice((5_000, 10_000, 25_000, 50_000))
        k.add("Hasar ve Kayıp", f"Taşıma sırasında oluşan hasar ve kayıplarda HİZMET VEREN'in sorumluluğu gönderi başına "
                                 f"{format_thousands(limit)} TL ile sınırlıdır.", "hasar_limiti", f"gönderi başına {format_thousands(limit)} TL")
    elif category == "yazilim":
        rate, discount = rng.choice((99.0, 99.5, 99.9)), rng.choice((5, 10, 15))
        k.add("Hizmet Seviyesi", f"Hizmet aylık {format_percent(rate)} erişilebilirlik taahhüdüyle sunulur. Taahhüdün altına "
                                  f"düşülen her ay için o ayın hizmet bedelinden %{discount} indirim yapılır.",
               "sla", f"aylık {format_percent(rate)} erişilebilirlik, altında %{discount} indirim")
        hours = rng.choice((2, 4, 8))
        k.add("Destek", f"Kritik arızalara bildirimden itibaren en geç {hours} saat içinde müdahale edilir; diğer "
                         f"talepler iki iş günü içinde yanıtlanır.", "destek", f"kritik arızada {hours} saat")
    elif category == "telekom":
        month_count, rate = rng.choice((12, 24, 36)), rng.choice((25, 50, 100))
        k.add("Taahhüt", f"{COMPANY} {month_count} ay süreyle hizmeti kullanmayı taahhüt eder. Taahhüt süresi dolmadan "
                          f"hizmetin sonlandırılması hâlinde kalan aylara ait bedelin %{rate} kadarı cayma bedeli olarak "
                          f"ödenir.", "taahhut", f"{month_count} ay, cayma bedeli kalan bedelin %{rate} kadarı")
    _termination(k, rng)
    _jurisdiction(k, rng)
    return Document(doc_id, "hizmet_sozlesmesi", SERVICE_TITLES[category], t["tedarikci_id"], t["ad"], short_name(t["ad"]), ("cfo",),
                 signed.isoformat(), tuple(k.maddeler))


# ---------------------------------------------------------------- customer contracts


def sales_contract(m: dict, monthly_sales: float, pays_by_cheque: bool, rng: random.Random) -> Document:
    doc_id = f"SOZ-{m['musteri_id']}"
    k = _Builder(doc_id)
    signed = business_day(rng, dt.date(2022, 1, 3), dt.date(2024, 11, 29))
    currency = "TL" if m["para_birimi"] == "TRY" else m["para_birimi"]
    k.add("Taraflar ve Konu", f"Bu sözleşme {COMPANY} ile {m['ad']} (MÜŞTERİ) arasında, {COMPANY}'in MÜŞTERİ'ye ürün "
                               f"satışına ilişkin çerçeve koşulları düzenler. Her sipariş bu koşullara tabidir.")
    _term(k, rng, signed, "MÜŞTERİ")
    if m["vade_gun"] == 0:
        k.add("Ödeme Koşulları", "Siparişler peşin ödenir; ödeme hesaba geçmeden sevkiyat yapılmaz.",
               "odeme_vadesi", "peşin")
    else:
        k.add("Ödeme Koşulları", f"MÜŞTERİ, faturaları fatura tarihinden itibaren {m['vade_gun']} gün içinde öder.",
               "odeme_vadesi", f"{m['vade_gun']} gün")
    if not m["yurtici"]:
        delivery = rng.choice(("EXW İstanbul", "FCA İstanbul", "CIF", "DAP"))
        k.add("Teslim Şekli ve Para Birimi", f"Teslim şekli {delivery} (Incoterms 2020) olup fiyatlar ve faturalar {currency} "
                                              f"cinsindendir.", "teslim_sekli", f"{delivery}, {currency}")
    if m["vade_gun"] > 0:
        limit = round_up_two_digits(monthly_sales * (m["vade_gun"] / 30 + 1))
        k.add("Kredi Limiti", f"MÜŞTERİ'nin açık hesap bakiyesi {format_thousands(limit)} {currency} tutarını aşamaz. Limit aşımı "
                               f"hâlinde yeni sevkiyat {COMPANY}'in yazılı onayına bağlıdır.",
               "kredi_limiti", f"{format_thousands(limit)} {currency}")
    if m["yurtici"]:
        if pays_by_cheque:
            days = rng.choice((60, 90, 120))
            k.add("Çekle Ödeme", f"MÜŞTERİ ödemelerini vadesi en fazla {days} gün olan kendi keşide ettiği çeklerle "
                                  f"yapabilir; çek bedeli tahsil edilene kadar borç ödenmiş sayılmaz.",
                   "cek", f"kabul, en fazla {days} gün vadeli")
        else:
            k.add("Çekle Ödeme", "Ödemeler yalnızca banka havalesiyle yapılır; çek ve senet kabul edilmez.",
                   "cek", "kabul edilmez")
    _late_interest(k, rng, "MÜŞTERİ")
    days = rng.choice((7, 14, 15, 30))
    k.add("İade", f"MÜŞTERİ, teslim aldığı ürünleri teslim tarihinden itibaren {days} gün içinde, kullanılmamış ve "
                   f"ambalajı açılmamış olmak kaydıyla iade edebilir.", "iade", f"{days} gün")
    if m["yurtici"]:
        if rng.random() < 0.5 and m["vade_gun"] > 0:
            guarantee = round_up_two_digits(monthly_sales * rng.choice((0.5, 1.0)))
            k.add("Teminat", f"MÜŞTERİ, sözleşme süresince {format_thousands(guarantee)} {currency} tutarında süresiz banka teminat "
                              f"mektubu verir.", "teminat", f"{format_thousands(guarantee)} {currency} teminat mektubu")
        else:
            k.add("Teminat", "Bu sözleşme kapsamında MÜŞTERİ'den teminat alınmaz.", "teminat", "yok")
    _termination(k, rng)
    if m["yurtici"]:
        _jurisdiction(k, rng)
    else:
        arbitration_body = rng.choice(("İstanbul Tahkim Merkezi (ISTAC)", "ICC Milletlerarası Tahkim Divanı"))
        language = rng.choice(("İngilizce", "Türkçe"))
        k.add("Uygulanacak Hukuk ve Tahkim", f"Sözleşmeye Türk hukuku uygulanır. Uyuşmazlıklar {arbitration_body} kurallarına göre, "
                                              f"İstanbul'da ve {language} olarak tahkim yoluyla çözülür.",
               "uyusmazlik", f"{arbitration_body}, İstanbul, {language}")
    roles = ("cfo", "satis_yurtici") if m["yurtici"] else ("cfo", "satis_ihracat")
    return Document(doc_id, "satis_sozlesmesi", "Satış Çerçeve Sözleşmesi", m["musteri_id"], m["ad"], short_name(m["ad"]), roles,
                 signed.isoformat(), tuple(k.maddeler))


# ---------------------------------------------------------------- policies


ALL_ROLES = ("cfo", "satis_yurtici", "satis_ihracat")


def policies(rng: random.Random) -> list[Document]:
    result = []

    def add_document(doc_id, title, roles, effective, fx_policy):
        k = _Builder(doc_id)
        fx_policy(k)
        result.append(Document(doc_id, "politika", title, None, None, None, roles, effective.isoformat(), tuple(k.maddeler)))

    def cash(k):
        rate = IzlemeAyarlari().asgari_nakit_orani
        k.add("Amaç", "Bu politika, şirketin günlük ödemelerini aksatmadan nakdini nasıl tutacağını ve fazla nakdi nasıl "
                       "değerlendireceğini belirler.")
        k.add("Güvenlik Eşiği", f"Ana TL hesabının gün içindeki en düşük bakiyesi, son on iki ayın ortalama aylık net "
                                 f"satışının {format_percent(rate * 100)} oranından az olmamalıdır. Eşiğin altına inilmesi "
                                 f"hâlinde CFO aynı gün bilgilendirilir.", "guvenlik_esigi",
               f"son 12 ayın ortalama aylık net satışının {format_percent(rate * 100)}")
        upper = rng.choice((40, 50, 60))
        k.add("Banka Dağılımı", f"Toplam nakdin %{upper} oranından fazlası tek bir bankada tutulmaz.", "banka_dagilimi",
               f"tek bankada en çok %{upper}")
        days = rng.choice((32, 45, 92))
        k.add("Fazla Nakdin Değerlendirilmesi", f"Fazla nakit yalnızca vadesi {days} günü aşmayan TL ya da döviz vadeli "
                                                 f"mevduatta değerlendirilir; hisse senedi ve yatırım fonuna yatırım "
                                                 f"yapılmaz.", "fazla_nakit", f"en çok {days} gün vadeli mevduat")
        hours, suffix = rng.choice((("10.00", "a"), ("11.00", "e"), ("12.00", "ye")))
        k.add("Raporlama", f"Hazine birimi her iş günü saat {hours}'{suffix} kadar banka bakiyelerini ve günün ödemelerini "
                            f"gösteren nakit raporunu CFO'ya iletir.", "nakit_raporu", f"her iş günü saat {hours}")

    def approval(k):
        a, b, c = rng.choice(((50_000, 250_000, 1_000_000), (100_000, 500_000, 2_000_000), (75_000, 400_000, 1_500_000)))
        k.add("Amaç", "Bu yönetmelik, şirketten yapılan ödemelerin kimin onayıyla yapılabileceğini belirler.")
        k.add("Onay Limitleri", f"{format_thousands(a)} TL'ye kadar olan ödemeleri finans uzmanı, {format_thousands(b)} TL'ye kadar olanları "
                                 f"finans müdürü, {format_thousands(c)} TL'ye kadar olanları CFO onaylar.", "onay_limitleri",
               f"uzman {format_thousands(a)} TL, müdür {format_thousands(b)} TL, CFO {format_thousands(c)} TL")
        k.add("Çift İmza", f"{format_thousands(c)} TL'yi aşan ödemeler CFO ile genel müdürün birlikte onayını gerektirir.",
               "cift_imza", f"{format_thousands(c)} TL üzeri CFO ve genel müdür")
        k.add("Yeni Karşı Tarafa İlk Ödeme", "Daha önce ödeme yapılmamış bir alıcıya yapılacak ilk ödemede banka "
                                              "hesabı, karşı tarafın yetkilisi telefonla aranarak teyit edilir ve tutar "
                                              "ne olursa olsun CFO onayı alınır.", "ilk_odeme",
               "telefonla hesap teyidi ve her tutarda CFO onayı")
        k.add("Mükerrer Ödeme Kontrolü", "Aynı tedarikçi faturasına ikinci ödeme sistemde engellenir; engelin "
                                          "kaldırılması yalnızca CFO'nun yazılı onayıyla mümkündür.", "mukerrer",
               "sistemde engellenir, istisna yalnızca CFO yazılı onayıyla")

    def receivables(k):
        h, warning_days, halt_days, legal_days = rng.choice(((7, 30, 60, 90), (15, 30, 60, 120), (10, 45, 75, 120)))
        k.add("Amaç", "Bu politika, vadesi geçen müşteri alacaklarının hangi adımlarla takip edileceğini belirler.")
        k.add("Takip Adımları", f"Vadesi {h} günü geçen alacak için müşteriye hatırlatma e-postası gönderilir; {warning_days} günü "
                                 f"geçince müşteri aranır ve yazılı ihtar çekilir.", "takip_adimlari",
               f"{h}. gün hatırlatma, {warning_days}. gün ihtar")
        k.add("Sevkiyatın Durdurulması", f"Vadesi {halt_days} günü geçen alacağı olan müşteriye, borç kapanana kadar yeni "
                                          f"sevkiyat yapılmaz.", "sevkiyat_durdurma", f"{halt_days}. günden sonra")
        k.add("Hukuki Takip", f"Vadesi {legal_days} günü geçen alacaklar hukuk birimine devredilir ve icra takibi başlatılır.",
               "hukuki_takip", f"{legal_days}. günden sonra")
        excess = rng.choice((5, 10, 15))
        k.add("Kredi Limiti Aşımı", f"Kredi limitinin %{excess} oranına kadar aşılmasını satış müdürü, daha fazlasını CFO "
                                     f"onaylar.", "limit_asimi", f"%{excess} oranına kadar satış müdürü, üstü CFO")

    def cheque(k):
        k.add("Amaç", "Bu politika, müşterilerden çek kabulünün koşullarını ve karşılıksız çıkan çeklerde izlenecek "
                       "yolu belirler.")
        max_term_days = 120
        k.add("En Uzun Vade", f"Vadesi düzenleme tarihinden itibaren {max_term_days} günü aşan çek kabul edilmez.",
               "cek_vadesi", f"en çok {max_term_days} gün")
        endorsement = rng.choice((0, 1))
        k.add("Ciro", "Yalnızca müşterinin kendi keşide ettiği çekler kabul edilir; ciro edilmiş çek alınmaz." if endorsement == 0
               else "Müşterinin kendi çeki dışında en fazla bir kez ciro edilmiş çek kabul edilebilir.",
               "ciro", "ciro edilmiş çek alınmaz" if endorsement == 0 else "en fazla bir ciro")
        k.add("Tahsile Verme", "Portföydeki çekler vadesinden üç iş günü önce tahsil edilmek üzere bankaya verilir.",
               "tahsile_verme", "vadeden üç iş günü önce")
        month = rng.choice((3, 6, 12))
        k.add("Karşılıksız Çek", f"Çeki karşılıksız çıkan müşteriyle {month} ay boyunca yalnızca peşin çalışılır ve müşterinin "
                                  f"kredi limiti sıfırlanır.", "karsiliksiz", f"{month} ay yalnızca peşin")

    def fx_policy(k):
        k.add("Amaç", "Bu politika, döviz cinsinden alacak ve borçlardan doğan kur riskinin sınırlarını belirler.")
        rate = rng.choice((15, 20, 25))
        k.add("Açık Pozisyon Sınırı", f"Net açık döviz pozisyonu, son on iki ayın ortalama aylık satışının %{rate} "
                                       f"oranını geçemez.", "pozisyon_siniri", f"aylık satışın %{rate}")
        hedge_ratio = rng.choice((30, 50, 70))
        k.add("Korunma Oranı", f"İhracat alacaklarının en az %{hedge_ratio} kadarı vadeli döviz satışıyla korunur.",
               "korunma_orani", f"en az %{hedge_ratio}")
        k.add("İzin Verilen Araçlar", "Korunma için yalnızca vadeli işlem (forward) ve swap kullanılabilir; opsiyon ve "
                                       "kaldıraçlı ürünler yasaktır.", "araclar", "yalnızca forward ve swap")

    def supplier(k):
        k.add("Amaç", "Bu prosedür, yeni tedarikçi kaydı ve tedarikçi bilgilerinin değiştirilmesi adımlarını belirler.")
        k.add("Kayıt Belgeleri", "Yeni tedarikçi kaydı için vergi levhası, imza sirküleri ve banka hesap teyit yazısı "
                                  "istenir.", "kayit_belgeleri", "vergi levhası, imza sirküleri, banka teyit yazısı")
        days = rng.choice((2, 3, 5))
        k.add("Banka Hesabı Değişikliği", f"Tedarikçinin banka hesabı değişikliği yazılı talep ve telefonla teyitten "
                                           f"sonra sisteme girilir; yeni hesaba ilk ödeme en erken {days} iş günü sonra "
                                           f"yapılır.", "iban_degisikligi", f"teyitten sonra {days} iş günü bekleme")
        share = rng.choice((25, 30, 40))
        k.add("Yoğunlaşma Sınırı", f"Tek bir tedarikçiden yapılan alımlar yıllık mal alımlarının %{share} oranını geçmez.",
               "yogunlasma", f"en çok %{share}")

    effective = dt.date(2024, 1, 2)
    add_document("POL-NAKIT", "Nakit Yönetimi Politikası", ("cfo",), effective, cash)
    add_document("POL-ONAY", "Ödeme Onay Yetkileri Yönetmeliği", ("cfo",), effective, approval)
    add_document("POL-ALACAK", "Alacak Takip ve Tahsilat Politikası", ALL_ROLES, effective, receivables)
    add_document("POL-CEK", "Çek Kabul Politikası", ALL_ROLES, effective, cheque)
    add_document("POL-KUR", "Kur Riski Politikası", ("cfo",), effective, fx_policy)
    add_document("POL-TEDARIKCI", "Tedarikçi Kayıt ve Değişiklik Prosedürü", ("cfo",), effective, supplier)
    return result


# ---------------------------------------------------------------- everything


def generate(tables: dict[str, list[dict]], seed: int = 42) -> list[Document]:
    """All synthetic documents from the company data. The same data and seed give the same documents."""
    rng = random.Random(f"{seed}:belge")
    documents = []
    for t in sorted(tables["tedarikciler"], key=lambda t: t["tedarikci_id"]):
        documents.append(supplier_contract(t, rng) if t["kategori"] == "mal" else service_contract(t, rng))

    sales_amount: dict[str, int] = defaultdict(int)  # in the invoice currency
    dates = [f["fatura_tarihi"] for f in tables["faturalar"]]
    month_count = ((max(dates) - min(dates)).days + 1) / 30.4375
    for f in tables["faturalar"]:
        if f["tur"] == "satis":
            sales_amount[f["musteri_id"]] += f["tutar_kurus"]
    try_sales: dict[str, int] = defaultdict(int)
    for f in tables["faturalar"]:
        if f["tur"] == "satis":
            try_sales[f["musteri_id"]] += f["tutar_try_kurus"]
    pays_by_cheque = {c["musteri_id"] for c in tables["cekler"]}
    customers_by_id = {m["musteri_id"]: m for m in tables["musteriler"]}
    ranked = sorted(try_sales, key=lambda customer_id: (-try_sales[customer_id], customer_id))
    selected = ([customer_id for customer_id in ranked if customers_by_id[customer_id]["yurtici"]][:DOMESTIC_CONTRACT_CUSTOMERS]
               + [customer_id for customer_id in ranked if not customers_by_id[customer_id]["yurtici"]][:EXPORT_CONTRACT_CUSTOMERS])
    for customer_id in sorted(selected):
        monthly = sales_amount[customer_id] / 100 / month_count
        documents.append(sales_contract(customers_by_id[customer_id], monthly, customer_id in pays_by_cheque, rng))
    documents.extend(policies(rng))
    return documents


def search_text(b: Document, m: Article) -> str:
    """The text used in search: the document's title and party + the article's title and text. The citation check
    looks only at `m.metin`."""
    bas = f"{b.baslik} — {b.taraf_ad}" if b.taraf_ad else b.baslik
    return f"{bas}\nMadde {m.no}. {m.baslik}\n{m.metin}"
