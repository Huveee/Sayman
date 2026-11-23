"""Synthetic company generator: a Turkish wholesaler built on real UCI invoices, reproducible from a seed.

Real: invoice line items and dates (shifted forward by a whole number of weeks), the customer split,
cancellation/return invoices (UCI) and exchange rates (TCMB). Synthetic: customer and supplier names, payment terms,
collection and payment behaviour, cheques, POS, bank accounts and their movements.

Rules:
- Money is integer cents; computation uses Decimal.
- Every section uses its own random stream (seed + section name); the system clock is never read.
- Bank movements are derived last, from the collection and payment records. Events are applied to a copy of these
  records; with events off the base data stays exactly the same.
"""

from __future__ import annotations

import copy
import datetime as dt
import heapq
import random
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from sayman.data.exchange_rates import ExchangeRates
from sayman.data.money import to_cents, from_cents, apply_rate
from sayman.data.uci import LineItem

DAY = dt.timedelta(days=1)

EURO_COUNTRIES = {
    "EIRE", "Germany", "France", "Netherlands", "Spain", "Belgium", "Portugal", "Italy", "Finland",
    "Cyprus", "Austria", "Greece", "Malta", "Lithuania", "European Community",
}
COUNTRY_NAMES_TR = {
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

NAME_WORDS = [
    "Akdeniz", "Anadolu", "Ege", "Marmara", "Karadeniz", "Toros", "Yıldız", "Güneş", "Doğa", "Atlas",
    "Kuzey", "Mavi", "Defne", "Çınar", "Lale", "Pınar", "Umut", "Başak", "Ufuk", "Kartal", "Ada", "Kale",
    "Sedef", "Nehir", "Yakut", "Zümrüt", "Akın", "Bereket", "Tuna", "Gökçe", "Ilgaz", "Kaya", "Koru",
    "Liman", "Meltem", "Nar", "Orkide", "Palmiye", "Rüzgar", "Tan", "Vadi", "Yaprak", "Zirve", "Ekin",
    "Fidan", "Gelincik", "Hilal", "İnci", "Ladin", "Sardunya",
]
NAME_FIELDS = [
    "Ticaret", "Hediyelik", "Dekorasyon", "Ev Gereçleri", "Kırtasiye", "Tekstil", "Züccaciye",
    "Pazarlama", "Dış Ticaret", "Mağazacılık", "Tasarım", "Oyuncak", "Ambalaj", "Gıda", "Market",
]
NAME_SUFFIXES = ["Ltd. Şti.", "A.Ş.", "San. ve Tic. Ltd. Şti."]
FOREIGN_WORDS = [
    "Nordic", "Euro", "Atlantic", "Alpine", "Baltic", "Pacific", "Coastal", "Royal", "Green", "Bright",
    "Golden", "Silver", "Union", "Global", "Prime",
]
FOREIGN_FIELDS = ["Home", "Gifts", "Living", "Trading", "Design", "Retail", "Supplies", "Decor"]
FOREIGN_ENDINGS = ["Co", "Group", "House", "Partners", "Store"]
FOREIGN_SUFFIXES = {
    "Germany": "GmbH", "Austria": "GmbH", "Switzerland": "AG", "France": "SARL", "Netherlands": "BV",
    "Belgium": "BV", "Spain": "SL", "Italy": "SRL", "Portugal": "Lda", "USA": "Inc", "Australia": "Pty Ltd",
}
SUPPLIER_FIELDS = ["Üretim", "İmalat", "Tedarik", "Toptan", "Plastik", "Cam", "Kağıt", "Metal", "Tekstil", "Seramik"]
SERVICES = [  # (category, name part, ratio to average monthly sales)
    ("kira", "Gayrimenkul Kiralama", Decimal("0.010")),
    ("enerji", "Enerji Dağıtım", Decimal("0.004")),
    ("lojistik", "Kargo ve Lojistik", Decimal("0.015")),
    ("yazilim", "Yazılım Hizmetleri", Decimal("0.002")),
    ("telekom", "Telekom Hizmetleri", Decimal("0.001")),
]
# Other expenses without an invoice: (reason, counterparty name part, ratio to that year's average monthly sales, every how many months)
OTHER_EXPENSES = [
    ("SİGORTA PRİMİ", "Sigorta A.Ş.", Decimal("0.003"), 1),
    ("MALİ MÜŞAVİRLİK", "Mali Müşavirlik", Decimal("0.001"), 1),
    ("BAKIM ONARIM", "Teknik Servis Ltd. Şti.", Decimal("0.002"), 3),
    ("DEMİRBAŞ ALIMI", "Ofis Mobilyaları Ltd. Şti.", Decimal("0.006"), 6),
]
CHEQUE_BANKS = ["Banka D", "Banka E", "Banka F", "Banka G", "Banka H"]

ACCOUNTS = [
    ("A_TRY", "Banka A", "TRY"),
    ("B_TRY", "Banka B", "TRY"),
    ("B_EUR", "Banka B", "EUR"),
    ("C_USD", "Banka C", "USD"),
]
FX_ACCOUNTS = {"TRY": "A_TRY", "EUR": "B_EUR", "USD": "C_USD"}

EVENT_COUNTS = (
    ("buyuk_musteri_gecikmesi", 2),
    ("karsiliksiz_cek", 3),
    ("mukerrer_odeme", 2),
    ("olagandisi_buyuk_cikis", 2),
    ("eslesmeyen_tahsilat_birikimi", 1),
    ("nakit_sikismasi", 2),
)


@dataclass(frozen=True)
class GeneratorSettings:
    """Generator settings. Values marked "synthetic" are design decisions with no source behind them.
    Collection behaviour is tuned against the bands in data/kaynak/kalibrasyon.json; the bands were written before the tuning."""

    tohum: int = 42
    # UCI dates are moved forward by this many years, rounded to the nearest whole week: the months (year-end peak,
    # Christmas closure) stay in place, weekdays are preserved. 15 years: 5481 days (783 weeks).
    kaydirma_yil: int = 15
    # Last day for which TCMB rates were downloaded. UCI items that fall after it once shifted are not used
    # (there is no rate for future days). With the 15-year shift this is the last four weeks of UCI.
    veri_bitis: dt.date = dt.date(2026, 11, 12)
    simdi: dt.date = dt.date(2026, 9, 1)
    olaylar_acik: bool = True
    olay_sayilari: tuple = EVENT_COUNTS
    # The last event of each kind falls after "now": it must not be visible in the as-of view.
    simdiden_sonra_olay: tuple = ("buyuk_musteri_gecikmesi", "karsiliksiz_cek", "olagandisi_buyuk_cikis")
    # Four settings chosen by calibration: profiller, batik_musteri_payi, vade_yurtici, vade_ihracat.
    # Choice: reports/faz1_ayar_denemeleri.json trial 4; after the last code change, in trial 5
    # seeds 42, 43 and 44 are all inside every band.
    # Payment profiles: (name, customer share, probability of paying an invoice late). Synthetic.
    profiller: tuple = (("zamaninda", 0.45, 0.10), ("ara_sira", 0.33, 0.40), ("sik", 0.17, 0.75), ("kronik", 0.05, 0.95))
    # Delay buckets of late payments (min days, max days, probability): Atradius Türkiye 2026 distribution.
    gecikme_dilimleri: tuple = ((1, 30, 0.53), (31, 60, 0.24), (61, 90, 0.12), (91, 180, 0.11))
    batik_musteri_payi: float = 0.03  # share of customers who stop paying altogether from some point on, synthetic
    # Terms (days, customer share). 0: cash. Because the year-end receivable coincides with the UCI October-December peak,
    # the DSO band is reached only with an average term of ~31 days (this does not fully match Atradius's "56% give terms of up to
    # two months"; the band comes from the TCMB DSO, so that was taken as the basis). Synthetic.
    vade_yurtici: tuple = ((0, 0.25), (30, 0.45), (45, 0.15), (60, 0.10), (90, 0.05))
    vade_ihracat: tuple = ((30, 0.8), (60, 0.2))  # synthetic
    cekle_odeyen_musteri: float = 0.35  # customers among the domestic ones who habitually pay by cheque, synthetic
    cekle_odeme_olasiligi: float = 0.80  # probability that these customers pay an invoice by cheque, synthetic
    karsiliksiz_orani: float = 0.02  # synthetic assumption: no source found in EVDS
    eslesmeyen_havale_orani: float = 0.004  # transfers arriving without a description, synthetic
    kismi_odeme_orani: float = 0.10  # synthetic
    pos_komisyon: Decimal = Decimal("0.02")  # synthetic
    alis_orani: Decimal = Decimal("0.72")  # goods purchases / sales, synthetic
    maas_orani: Decimal = Decimal("0.10")  # monthly salary / average monthly sales of that year, synthetic
    sgk_orani: Decimal = Decimal("0.375")  # social security (SGK) premium / salary, synthetic
    vergi_orani: Decimal = Decimal("0.20")  # tax / (sales - purchases), synthetic
    kur_makasi: Decimal = Decimal("0.005")  # bank spread when selling FX, synthetic
    acilis_tamponu: Decimal = Decimal("1.00")  # lowest A_TRY balance is this multiple of the average monthly sales, synthetic
    hesap_ucreti_kurus: int = 25_000  # monthly account maintenance fee, synthetic


@dataclass
class GenerationResult:
    tables: dict[str, list[dict]]
    key: dict
    applied_shift_days: int
    data_start: dt.date
    unused_items_after_end: int  # UCI items not used because they fall after the data end once shifted


def compute_shift_days(first_day: dt.date, years: int) -> int:
    """Rounds the day difference of `years` years to the nearest whole week."""
    target = first_day.replace(year=first_day.year + years, day=min(first_day.day, 28) if first_day.month == 2 else first_day.day)
    return round((target - first_day).days / 7) * 7


# ---------------------------------------------------------------- helpers


def roll_to_business_day(d: dt.date) -> dt.date:
    """Moves a day that falls on a weekend to the Monday (public holidays are not taken into account)."""
    while d.weekday() >= 5:
        d += DAY
    return d


def previous_business_day(d: dt.date) -> dt.date:
    while d.weekday() >= 5:
        d -= DAY
    return d


def rng_stream(settings: GeneratorSettings, name: str) -> random.Random:
    return random.Random(f"{settings.tohum}:{name}")


def weighted_choice(rng: random.Random, options: tuple):
    """Picks a value by probability from ((value, probability), ...)."""
    r = rng.random()
    total = 0.0
    for value, probability in options:
        total += probability
        if r < total:
            return value
    return options[-1][0]


def unique_name(make, used: set) -> str:
    for _ in range(50):
        name = make()
        if name not in used:
            used.add(name)
            return name
    i = 2
    while f"{name} {i}" in used:
        i += 1
    used.add(f"{name} {i}")
    return f"{name} {i}"


def month_starts(start: dt.date, end: dt.date) -> list[dt.date]:
    months, d = [], start.replace(day=1)
    while d <= end:
        months.append(d)
        d = (d + dt.timedelta(days=32)).replace(day=1)
    return months


def month_end(month: dt.date) -> dt.date:
    return (month + dt.timedelta(days=32)).replace(day=1) - DAY


def _currency_amount(gbp: Decimal, currency: str, date: dt.date, rates: ExchangeRates) -> tuple[int, Decimal, int]:
    """Converts a GBP amount to the invoice currency: (`tutar_kurus`, `kur`, `tutar_try_kurus`)."""
    gbp_try = rates.rate("GBP", date)
    if currency == "TRY":
        tl = to_cents(gbp * gbp_try)
        return tl, Decimal(1), tl
    rate = rates.rate(currency, date)
    amount = to_cents(gbp * gbp_try / rate)
    return amount, rate, to_cents(from_cents(amount) * rate)


def new_id(prefix: str, rng: random.Random, used: set) -> str:
    """Random identifier whose order carries no information (e.g. T3F9A01C2). Sequential numbers are not used, so that records
    added or re-dated by events cannot be told apart by their identifier."""
    while True:
        identifier = f"{prefix}{rng.getrandbits(32):08X}"
        if identifier not in used:
            used.add(identifier)
            return identifier


def assign_identifiers(rows: list[dict], field_name: str, prefix: str, rng: random.Random, sort_key) -> list[dict]:
    used: set = set()
    for s in sorted(rows, key=sort_key):
        s[field_name] = new_id(prefix, rng, used)
    return rows


def assign_sequential_identifiers(rows: list[dict], field_name: str, prefix: str, width: int, sort_key) -> list[dict]:
    for i, s in enumerate(sorted(rows, key=sort_key), start=1):
        s[field_name] = f"{prefix}{i:0{width}d}"
    return rows


# ---------------------------------------------------------------- generator


class Generator:
    def __init__(self, line_items: list[LineItem], rates: ExchangeRates, settings: GeneratorSettings):
        if not line_items:
            raise ValueError("kalem listesi boş")
        self.rates = rates
        self.settings = settings
        source_first = min(k.tarih.date() for k in line_items)
        self.shift_days = compute_shift_days(source_first, settings.kaydirma_yil)  # whole weeks: weekdays are preserved
        self.start = source_first + dt.timedelta(days=self.shift_days)
        self.end = settings.veri_bitis
        if not self.start < settings.simdi <= self.end:
            raise ValueError("simdi, veri başlangıcı ile veri bitişi arasında olmalı")
        self.key: dict = {"musteri_profilleri": {}, "eslesmeyen_tahsilatlar": {}, "olaylar": []}
        self._line_items = [k for k in line_items if self.kaydir(k.tarih) <= self.end]
        self.items_after_end = len(line_items) - len(self._line_items)

    def kaydir(self, t: dt.datetime) -> dt.date:
        return t.date() + dt.timedelta(days=self.shift_days)

    # -- customers and invoices -------------------------------------------------------------

    def collect_invoices(self) -> list[dict]:
        invoices: dict[tuple, dict] = {}
        for k in self._line_items:
            key = (k.invoice_no, k.customer_no or "")
            f = invoices.get(key)
            if f is None:
                f = invoices[key] = {
                    "no": k.invoice_no, "tarih": self.kaydir(k.tarih), "musteri": k.customer_no, "ulke": k.country,
                    "kategori": k.category, "gbp": Decimal(0), "kalem": 0, "stoklar": set(),
                }
            f["gbp"] += k.amount
            f["kalem"] += 1
            f["stoklar"].add(k.stock_code)
        return sorted(invoices.values(), key=lambda f: (f["tarih"], f["no"], f["musteri"] or ""))

    def generate_customers(self, raw_invoices: list[dict]) -> None:
        rng = rng_stream(self.settings, "musteri")
        country_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        first: dict[str, dt.date] = {}
        for f in raw_invoices:
            if f["musteri"]:
                country_counts[f["musteri"]][f["ulke"]] += 1
                first.setdefault(f["musteri"], f["tarih"])

        def order(number: str):
            return (0, int(number), "") if number.isdigit() else (1, 0, number)

        used: set = set()
        self.customers, self.customer_map = [], {}
        for i, number in enumerate(sorted(country_counts, key=order), start=1):
            country = max(sorted(country_counts[number]), key=lambda u: country_counts[number][u])
            domestic = country == "United Kingdom"
            currency = "TRY" if domestic else ("EUR" if country in EURO_COUNTRIES else "USD")
            if domestic:
                name = unique_name(lambda: " ".join(
                    [rng.choice(NAME_WORDS), rng.choice(NAME_WORDS), rng.choice(NAME_FIELDS), rng.choice(NAME_SUFFIXES)]), used)
                term_days = weighted_choice(rng, self.settings.vade_yurtici)
            else:
                tur = FOREIGN_SUFFIXES.get(country, "Ltd")
                name = unique_name(lambda: " ".join(
                    [rng.choice(FOREIGN_WORDS), rng.choice(FOREIGN_FIELDS), rng.choice(FOREIGN_ENDINGS), tur]), used)
                term_days = weighted_choice(rng, self.settings.vade_ihracat)
            profile = weighted_choice(rng, tuple((p[0], p[1]) for p in self.settings.profiller))
            late = {p[0]: p[2] for p in self.settings.profiller}[profile]
            defaulter = rng.random() < self.settings.batik_musteri_payi
            # A post-dated cheque is credit itself: a cash (0-day term) customer does not use cheques.
            cheque_user = domestic and rng.random() < self.settings.cekle_odeyen_musteri and term_days > 0
            customer_id = f"M{i:05d}"
            self.customer_map[number] = customer_id
            self.customers.append({
                "musteri_id": customer_id, "ad": name, "ulke": COUNTRY_NAMES_TR.get(country, country), "yurtici": domestic,
                "para_birimi": currency, "vade_gun": term_days, "ilk_islem_tarihi": first[number],
                "_profil": profile, "_gec": late, "_batik": defaulter, "_cekci": cheque_user,
            })
        self.customers_by_id = {m["musteri_id"]: m for m in self.customers}

    def generate_invoices(self, raw_invoices: list[dict]) -> None:
        self.invoices, self.pos = [], []
        sales: dict[str, list] = defaultdict(list)
        invoice_seq = pos_seq = 0
        for f in raw_invoices:
            if f["kategori"] in ("perakende", "perakende_iade"):
                pos_seq += 1
                tl = to_cents(f["gbp"] * self.rates.rate("GBP", f["tarih"]))
                commission = apply_rate(abs(tl), self.settings.pos_komisyon) * (1 if tl >= 0 else -1)
                self.pos.append({
                    "pos_id": f"P{pos_seq:06d}", "kaynak_fatura_no": f["no"], "islem_tarihi": f["tarih"],
                    "tutar_kurus": tl, "komisyon_kurus": commission, "valor_tarihi": roll_to_business_day(f["tarih"] + DAY),
                })
                continue
            invoice_seq += 1
            m = self.customers_by_id[self.customer_map[f["musteri"]]]
            amount, rate, tl = _currency_amount(f["gbp"], m["para_birimi"], f["tarih"], self.rates)
            row = {
                "fatura_id": f"F{invoice_seq:06d}", "kaynak_fatura_no": f["no"],
                "tur": "iade" if f["kategori"] == "iade" else "satis", "musteri_id": m["musteri_id"],
                "fatura_tarihi": f["tarih"], "vade_tarihi": f["tarih"], "para_birimi": m["para_birimi"],
                "tutar_kurus": amount, "kur": str(rate), "tutar_try_kurus": tl, "iliskili_fatura_id": None,
                "iade_eslesme": None, "uygulanan_kurus": None, "kalem_sayisi": f["kalem"],
            }
            if row["tur"] == "satis":
                row["vade_tarihi"] = f["tarih"] + dt.timedelta(days=m["vade_gun"])
                sales[m["musteri_id"]].append((f["tarih"], row["fatura_id"], f["stoklar"]))
            else:
                # A return is linked to an earlier sale of the same customer: first by a shared stock code, otherwise the last invoice.
                target, match_method = None, "yok"
                for date, candidate, stock_codes in reversed(sales[m["musteri_id"]]):
                    if date <= f["tarih"] and stock_codes & f["stoklar"]:
                        target, match_method = candidate, "stok_kodu"
                        break
                if target is None and sales[m["musteri_id"]]:
                    target, match_method = sales[m["musteri_id"]][-1][1], "son_fatura"
                row["iliskili_fatura_id"], row["iade_eslesme"] = target, match_method
            self.invoices.append(row)
        self.invoices_by_id = {f["fatura_id"]: f for f in self.invoices}
        self.customer_totals = defaultdict(int)
        for f in self.invoices:
            self.customer_totals[f["musteri_id"]] += f["tutar_try_kurus"]

    # -- collection plan ------------------------------------------------------------------------

    def _payment_day(self, target: dt.date, invoice: dict, late: bool) -> dt.date:
        """On a late payment a weekend rolls forward, on an on-time payment it rolls back (so it does not pass the due date)."""
        earliest = invoice["fatura_tarihi"]  # a cash (0-day term) sale can be paid the same day
        target = max(target, earliest)
        if late:
            return roll_to_business_day(target)
        d = previous_business_day(target)
        return d if d >= earliest else roll_to_business_day(target)

    def plan_collections(self) -> None:
        rng = rng_stream(self.settings, "tahsilat")
        rng_default = rng_stream(self.settings, "batik")
        rng_cheque = rng_stream(self.settings, "cek")
        sales = defaultdict(list)
        for f in self.invoices:
            if f["tur"] == "satis":
                sales[f["musteri_id"]].append(f)
        default_start = {}
        for m in self.customers:
            entries = sales[m["musteri_id"]]
            if m["_batik"] and entries:
                i = int(len(entries) * (0.3 + 0.6 * rng_default.random()))
                default_start[m["musteri_id"]] = entries[min(i, len(entries) - 1)]["fatura_tarihi"]
            default_from = default_start.get(m["musteri_id"])
            self.key["musteri_profilleri"][m["musteri_id"]] = {
                "profil": m["_profil"], "cekle_oder": m["_cekci"],
                "batik_baslangic": default_from.isoformat() if default_from else None,
            }
        self.plan = {}
        for f in self.invoices:
            if f["tur"] != "satis":
                continue
            m = self.customers_by_id[f["musteri_id"]]
            default_from = default_start.get(m["musteri_id"])
            late = rng.random() < m["_gec"]
            if late:
                bucket_roll, total = rng.random(), 0.0
                for lower, upper, p in self.settings.gecikme_dilimleri:
                    total += p
                    if bucket_roll < total:
                        break
                day = rng.randint(lower, upper)
            else:
                day = -rng.randint(0, 10)
            last = self._payment_day(f["vade_tarihi"] + dt.timedelta(days=day), f, late)
            partial = rng.random() < self.settings.kismi_odeme_orani
            earlier_days = rng.randint(7, 30)
            ratio = Decimal(rng.randint(40, 70)) / 100
            unmatched_roll = rng.random()
            cheque_roll, purchase_lead_days, bounce_roll, retry_days = (
                rng_cheque.random(), rng_cheque.randint(30, 90), rng_cheque.random(), rng_cheque.randint(15, 60))
            bank, cheque_no = rng_cheque.choice(CHEQUE_BANKS), str(rng_cheque.randint(1_000_000, 9_999_999))

            p = {"odenmeyecek": default_from is not None and f["fatura_tarihi"] >= default_from, "taksitler": [], "cek": None,
                 "yontem": "havale" if m["para_birimi"] == "TRY" else "swift", "eslesmeyen": False}
            if m["_cekci"] and not partial and cheque_roll < self.settings.cekle_odeme_olasiligi:
                p["yontem"] = "cek"
                p["cek"] = {
                    "alis": max(last - dt.timedelta(days=purchase_lead_days), f["fatura_tarihi"]), "vade": last,
                    "karsiliksiz": bounce_roll < self.settings.karsiliksiz_orani,
                    "yeniden": roll_to_business_day(last + dt.timedelta(days=retry_days)), "banka": bank, "no": cheque_no,
                }
            elif partial:
                earlier = self._payment_day(last - dt.timedelta(days=earlier_days), f, False)
                p["taksitler"] = [(min(earlier, last), ratio), (last, None)]
            else:
                p["taksitler"] = [(last, None)]
            p["eslesmeyen"] = p["yontem"] == "havale" and unmatched_roll < self.settings.eslesmeyen_havale_orani
            self.plan[f["fatura_id"]] = p

    def apply_plan(self) -> dict[str, list[dict]]:
        """Produces collections, cheques, cheque movements and customer refund payments from the plan.

        For each sales invoice the events are processed in date order: return invoice (deducted from the open amount,
        the remainder is refunded to the customer), instalment, cheque receipt, cheque due date, re-payment of a bounced cheque.
        Events after the data end are not processed.
        """
        customer_collections, cheques, cheque_movements, refund_payments = [], [], [], []
        self.applied: dict[str, int] = {}
        returns = defaultdict(list)
        for f in self.invoices:
            if f["tur"] == "iade":
                if f["iliskili_fatura_id"]:
                    returns[f["iliskili_fatura_id"]].append(f)
                else:
                    self.applied[f["fatura_id"]] = 0
                    self._refund_payment(f, -f["tutar_kurus"], refund_payments)

        for f in self.invoices:
            if f["tur"] != "satis":
                continue
            p = self.plan[f["fatura_id"]]
            m = self.customers_by_id[f["musteri_id"]]
            queue, order = [], 0

            def push_event(date, priority, tur, payload):
                nonlocal order
                order += 1
                heapq.heappush(queue, (date, priority, order, tur, payload))

            for return_invoice in returns[f["fatura_id"]]:
                push_event(return_invoice["fatura_tarihi"], 0, "iade", return_invoice)
            if not p["odenmeyecek"]:
                if p["cek"]:
                    push_event(p["cek"]["alis"], 1, "cek_alis", None)
                else:
                    for t, ratio in p["taksitler"]:
                        push_event(t, 2, "taksit", ratio)
            remaining, pending = f["tutar_kurus"], 0  # pending: the part to be covered by the cheque in the portfolio
            while queue:
                t, _, _, tur, payload = heapq.heappop(queue)
                if t > self.end:
                    break
                if tur == "iade":
                    amount = -payload["tutar_kurus"]
                    applied_amount = max(0, min(amount, remaining - pending))
                    self.applied[payload["fatura_id"]] = applied_amount
                    remaining -= applied_amount
                    if amount - applied_amount > 0:
                        self._refund_payment(payload, amount - applied_amount, refund_payments)
                elif tur == "taksit":
                    to_pay = remaining if payload is None else min(remaining, to_cents(from_cents(remaining) * payload))
                    if to_pay > 0:
                        unmatched = p["eslesmeyen"] and payload is None
                        customer_collections.append(self._collection(m, f, t, to_pay, p["yontem"], None, unmatched))
                        remaining -= to_pay
                elif tur == "cek_alis":
                    if remaining <= 0:
                        continue
                    cheque = p["cek"]
                    cheque_id = "C" + f["fatura_id"][1:]
                    cheques.append({
                        "cek_id": cheque_id, "musteri_id": m["musteri_id"], "cek_no": cheque["no"],
                        "kesideci_banka": cheque["banka"], "alis_tarihi": t, "vade_tarihi": cheque["vade"],
                        "tutar_kurus": remaining,
                    })
                    cheque_movements.append({"cek_id": cheque_id, "tarih": t, "durum": "portfoyde"})
                    presentation_date = previous_business_day(cheque["vade"] - dt.timedelta(days=3))
                    if t < presentation_date <= self.end:
                        cheque_movements.append({"cek_id": cheque_id, "tarih": presentation_date, "durum": "tahsilde"})
                    pending = remaining
                    push_event(cheque["vade"], 1, "cek_vade", (cheque_id, remaining))
                elif tur == "cek_vade":
                    cheque_id, amount = payload
                    pending -= amount
                    if p["cek"]["karsiliksiz"]:
                        cheque_movements.append({"cek_id": cheque_id, "tarih": t, "durum": "karsiliksiz"})
                        push_event(p["cek"]["yeniden"], 2, "yeniden", None)
                    else:
                        cheque_movements.append({"cek_id": cheque_id, "tarih": t, "durum": "tahsil_edildi"})
                        customer_collections.append(self._collection(m, f, t, amount, "cek", cheque_id, False))
                        remaining -= amount
                elif tur == "yeniden" and remaining > 0:
                    customer_collections.append(self._collection(m, f, t, remaining, "havale", None, False))
                    remaining = 0
        for f in self.invoices:
            if f["tur"] == "iade":
                f["uygulanan_kurus"] = self.applied.get(f["fatura_id"], 0)
        return {"tahsilatlar": customer_collections, "cekler": cheques, "cek_hareketleri": cheque_movements,
                "iade_odemeleri": refund_payments}

    def _refund_payment(self, return_invoice: dict, amount: int, entries: list) -> None:
        date = roll_to_business_day(return_invoice["fatura_tarihi"] + dt.timedelta(days=5))
        if date > self.end:
            return
        m = self.customers_by_id[return_invoice["musteri_id"]]
        entries.append({
            "tur": "musteri_iadesi", "tedarikci_id": None, "alis_fatura_id": None, "musteri_id": m["musteri_id"],
            "iade_fatura_id": return_invoice["fatura_id"], "tarih": date, "tutar_kurus": amount,
            "yontem": "havale" if m["para_birimi"] == "TRY" else "swift",
            "banka_hesap_id": FX_ACCOUNTS[m["para_birimi"]], "aciklama": f"İADE {m['ad']}",
        })

    def _collection(self, m, f, date, amount, method, cheque_id, unmatched) -> dict:
        return {
            "musteri_id": m["musteri_id"], "fatura_id": None if unmatched else f["fatura_id"], "tarih": date,
            "para_birimi": m["para_birimi"], "tutar_kurus": amount, "yontem": method, "cek_id": cheque_id,
            "banka_hesap_id": FX_ACCOUNTS[m["para_birimi"]],
            "eslesme": "bilerek_eslesmemis" if unmatched else "eslesti", "_gercek_fatura": f["fatura_id"],
        }

    # -- suppliers and payments -------------------------------------------------------------

    def monthly_sales(self) -> dict[dt.date, int]:
        monthly = defaultdict(int)
        for f in self.invoices:
            monthly[f["fatura_tarihi"].replace(day=1)] += f["tutar_try_kurus"]
        for p in self.pos:
            monthly[p["islem_tarihi"].replace(day=1)] += p["tutar_kurus"]
        return monthly

    def generate_purchasing(self) -> None:
        rng = rng_stream(self.settings, "tedarikci")
        used: set = set()
        self.suppliers = []
        for i in range(1, 41):
            name = unique_name(lambda: f"{rng.choice(NAME_WORDS)} {rng.choice(SUPPLIER_FIELDS)} {rng.choice(NAME_SUFFIXES)}", used)
            self.suppliers.append({"tedarikci_id": f"S{i:03d}", "ad": name, "kategori": "mal",
                                      "vade_gun": weighted_choice(rng, ((30, 0.3), (60, 0.5), (90, 0.2)))})
        for j, (category, field_name, _) in enumerate(SERVICES, start=41):
            name = unique_name(lambda: f"{rng.choice(NAME_WORDS)} {field_name} {rng.choice(NAME_SUFFIXES)}", used)
            self.suppliers.append({"tedarikci_id": f"S{j:03d}", "ad": name, "kategori": category, "vade_gun": 30})
        goods_suppliers = [t for t in self.suppliers if t["kategori"] == "mal"]
        service = {t["kategori"]: t for t in self.suppliers if t["kategori"] != "mal"}
        weights = [1 / (i + 1) for i in range(len(goods_suppliers))]

        monthly = self.monthly_sales()
        months = month_starts(self.start, self.end)
        full_months = [a for a in months if a >= self.start and month_end(a) <= self.end]
        self.average_monthly_sales = sum(monthly[a] for a in full_months) // max(1, len(full_months))
        yearly = defaultdict(list)
        for a in full_months:
            yearly[a.year].append(monthly[a])
        year_average = {y: sum(v) // len(v) for y, v in yearly.items()}

        def year_average_for(month: dt.date) -> int:
            return year_average.get(month.year, self.average_monthly_sales)

        def business_days(month: dt.date) -> list[dt.date]:
            d, days = month, []
            while d <= month_end(month):
                if d.weekday() < 5 and self.start <= d <= self.end:
                    days.append(d)
                d += DAY
            return days

        self.purchase_invoices, monthly_purchases = [], defaultdict(int)
        purchase_seq = 0
        for month in months:
            days = business_days(month)
            if not days:
                continue
            total = to_cents(from_cents(max(monthly[month], 0)) * self.settings.alis_orani)
            n = rng.randint(8, 15)
            shares = [rng.randint(20, 120) for _ in range(n)]
            remaining = total
            for k in range(n):
                amount = remaining if k == n - 1 else total * shares[k] // sum(shares)
                remaining -= amount
                t = rng.choices(goods_suppliers, weights=weights)[0]
                date = rng.choice(days)
                if amount <= 0:
                    continue
                purchase_seq += 1
                self.purchase_invoices.append({
                    "alis_fatura_id": f"A{purchase_seq:06d}", "tedarikci_id": t["tedarikci_id"], "tarih": date,
                    "vade_tarihi": date + dt.timedelta(days=t["vade_gun"]), "tutar_kurus": amount, "kategori": "mal"})
                monthly_purchases[month] += amount
            day_for = {"kira": days[0], "enerji": days[min(14, len(days) - 1)], "lojistik": days[-1],
                       "yazilim": days[0], "telekom": days[min(9, len(days) - 1)]}
            for category, _, ratio in SERVICES:
                base = monthly[month] if category == "lojistik" else year_average_for(month)
                amount = to_cents(from_cents(max(base, 0)) * ratio * (100 + rng.randint(-5, 5)) / 100)
                if amount <= 0:
                    continue
                t = service[category]
                purchase_seq += 1
                self.purchase_invoices.append({
                    "alis_fatura_id": f"A{purchase_seq:06d}", "tedarikci_id": t["tedarikci_id"], "tarih": day_for[category],
                    "vade_tarihi": day_for[category] + dt.timedelta(days=t["vade_gun"]), "tutar_kurus": amount,
                    "kategori": category})
                monthly_purchases[month] += amount

        name = {t["tedarikci_id"]: t["ad"] for t in self.suppliers}
        payments = []
        for a in self.purchase_invoices:
            late = rng.random() < 0.10
            drift = rng.randint(1, 10) if late else -rng.randint(0, 2)
            by_cheque = a["kategori"] == "mal" and rng.random() < 0.15
            target = a["vade_tarihi"] + dt.timedelta(days=drift)
            date = roll_to_business_day(target) if late else previous_business_day(target)
            if date > self.end:
                continue
            payments.append({
                "tur": "tedarikci", "tedarikci_id": a["tedarikci_id"], "alis_fatura_id": a["alis_fatura_id"],
                "musteri_id": None, "iade_fatura_id": None, "tarih": date, "tutar_kurus": a["tutar_kurus"],
                "yontem": "cek" if by_cheque else "havale", "banka_hesap_id": "A_TRY",
                "aciklama": ("VERİLEN ÇEK ÖDEMESİ " if by_cheque else "EFT GİDEN ") + name[a["tedarikci_id"]]})

        def fixed_payment(tur, date, amount, description):
            if self.start <= date <= self.end and amount > 0:
                payments.append({"tur": tur, "tedarikci_id": None, "alis_fatura_id": None, "musteri_id": None,
                                 "iade_fatura_id": None, "tarih": date, "tutar_kurus": amount, "yontem": "havale",
                                 "banka_hesap_id": "A_TRY", "aciklama": description})

        counterparty = {reason: f"{rng.choice(NAME_WORDS)} {part}" for reason, part, _, _ in OTHER_EXPENSES}
        for i, month in enumerate(months):
            days = business_days(month)
            for reason, _, ratio, frequency in OTHER_EXPENSES:
                if i % frequency == 0 and days:
                    amount = to_cents(from_cents(year_average_for(month)) * ratio * (100 + rng.randint(-10, 10)) / 100)
                    payments.append({"tur": "diger", "tedarikci_id": None, "alis_fatura_id": None, "musteri_id": None,
                                     "iade_fatura_id": None, "tarih": rng.choice(days), "tutar_kurus": amount,
                                     "yontem": "havale", "banka_hesap_id": "A_TRY",
                                     "aciklama": f"EFT GİDEN {counterparty[reason]} {reason}"})
        for month in months:
            salary = to_cents(from_cents(year_average_for(month)) * self.settings.maas_orani)
            fixed_payment("maas", previous_business_day(month_end(month)), salary, f"MAAŞ ÖDEMESİ {month.strftime('%m/%Y')}")
            day = roll_to_business_day((month + dt.timedelta(days=32)).replace(day=26))
            fixed_payment("sgk", day, to_cents(from_cents(salary) * self.settings.sgk_orani), f"SGK PRİM ÖDEMESİ {month.strftime('%m/%Y')}")
            tax_base = max(monthly[month] - monthly_purchases[month], 0)
            fixed_payment("vergi", day, to_cents(from_cents(tax_base) * self.settings.vergi_orani), f"VERGİ ÖDEMESİ {month.strftime('%m/%Y')}")
        self.base_payments = payments

    # -- bank ---------------------------------------------------------------------------------

    def ledger(self, customer_collections: list[dict], payments: list[dict], opening: dict[str, int]) -> list[dict]:
        """Derives the bank movements and the running balance from collections, POS value dates and payments.
        On the first business day of the month FX is sold and the account fee is charged; every Friday B_TRY is transferred to A_TRY."""
        daily = defaultdict(list)
        for t in customer_collections:
            name = self.customers_by_id[t["musteri_id"]]["ad"]
            if t["yontem"] == "cek":
                description = f"ÇEK TAHSİLATI {t['cek_id']} {name}"
            elif t["yontem"] == "swift":
                description = f"SWIFT GELEN {t['para_birimi']} {name}"
            else:
                description = f"EFT GELEN {name}"
            daily[t["tarih"]].append((0, t["tahsilat_id"], t["banka_hesap_id"], t["tutar_kurus"], description, "tahsilat"))
        daily_pos = defaultdict(int)
        for p in self.pos:
            if p["valor_tarihi"] <= self.end:
                daily_pos[p["valor_tarihi"]] += p["tutar_kurus"] - p["komisyon_kurus"]
        for day, net in daily_pos.items():
            daily[day].append((1, day.isoformat(), "B_TRY", net, "POS ÜYE İŞYERİ ALACAK", "pos"))
        for o in payments:
            daily[o["tarih"]].append((2, o["odeme_id"], o["banka_hesap_id"], -o["tutar_kurus"], o["aciklama"], "odeme"))

        balance = dict(opening)
        rows = []
        account_currency = {h: currency for h, _, currency in ACCOUNTS}

        def cover_shortfall(account, day, shortfall):
            """A shortfall on a side account is covered from A_TRY the same day: a transfer to a TL account, an FX purchase for an FX account."""
            currency = account_currency[account]
            source_id = f"{day.isoformat()}-{account}-karsilama"
            if currency == "TRY":
                post_entry("A_TRY", day, -shortfall, "HESAPLAR ARASI VİRMAN", "virman", source_id)
                post_entry(account, day, shortfall, "HESAPLAR ARASI VİRMAN", "virman", source_id)
            else:
                rate = self.rates.rate(currency, day)
                description = f"DÖVİZ ALIŞ {currency}/TRY KUR {rate}"
                post_entry("A_TRY", day, -to_cents(from_cents(shortfall) * rate * (1 + self.settings.kur_makasi)), description, "kur_donusum", source_id)
                post_entry(account, day, shortfall, description, "kur_donusum", source_id)

        def post_entry(account, day, amount, description, tur, source_id):
            if account != "A_TRY" and balance[account] + amount < 0:
                cover_shortfall(account, day, -(balance[account] + amount))
            balance[account] += amount
            rows.append({"hareket_id": f"H{len(rows) + 1:07d}", "hesap_id": account, "tarih": day,
                             "tutar_kurus": amount, "bakiye_kurus": balance[account], "aciklama": description,
                             "kaynak_tur": tur, "kaynak_id": source_id})

        day = self.start
        while day <= self.end:
            for _, source_id, account, amount, description, tur in sorted(daily.get(day, []), key=lambda x: (x[0], x[1])):
                post_entry(account, day, amount, description, tur, source_id)
            if day.weekday() < 5 and day == roll_to_business_day(day.replace(day=1)):
                for account, currency in (("B_EUR", "EUR"), ("C_USD", "USD")):
                    if balance[account] > 0:
                        fx_amount = to_cents(from_cents(balance[account]) * Decimal("0.8"))
                        rate = self.rates.rate(currency, day)
                        tl = to_cents(from_cents(fx_amount) * rate * (1 - self.settings.kur_makasi))
                        description = f"DÖVİZ SATIŞ {currency}/TRY KUR {rate}"
                        post_entry(account, day, -fx_amount, description, "kur_donusum", f"{day.isoformat()}-{currency}")
                        post_entry("A_TRY", day, tl, description, "kur_donusum", f"{day.isoformat()}-{currency}")
                post_entry("A_TRY", day, -self.settings.hesap_ucreti_kurus, "HESAP İŞLETİM ÜCRETİ", "masraf", f"{day.isoformat()}-ucret")
            if day.weekday() == 4 and balance["B_TRY"] > 0:
                amount = to_cents(from_cents(balance["B_TRY"]) * Decimal("0.9"))
                post_entry("B_TRY", day, -amount, "HESAPLAR ARASI VİRMAN", "virman", f"{day.isoformat()}-virman")
                post_entry("A_TRY", day, amount, "HESAPLAR ARASI VİRMAN", "virman", f"{day.isoformat()}-virman")
            day += DAY
        return rows

    def opening_balances(self, customer_collections: list[dict], payments: list[dict]) -> dict[str, int]:
        """Starts with an opening balance on A_TRY only (shortfalls on the side accounts are covered from A_TRY). No rule looks
        at the A_TRY balance, so the A_TRY balance shifts one-to-one with the opening balance: it is computed exactly in one pass,
        and the lowest A_TRY balance equals the buffer."""
        buffer = to_cents(from_cents(self.average_monthly_sales) * self.settings.acilis_tamponu)
        opening = {h: 0 for h, _, _ in ACCOUNTS}
        lowest = min([0] + [s["bakiye_kurus"] for s in self.ledger(customer_collections, payments, opening)
                              if s["hesap_id"] == "A_TRY"])
        opening["A_TRY"] = buffer - lowest
        return opening


def _strip_private(rows: list[dict]) -> list[dict]:
    return [{k: v for k, v in s.items() if not k.startswith("_")} for s in rows]


def generate(line_items: list[LineItem], rates: ExchangeRates, settings: GeneratorSettings = GeneratorSettings()) -> GenerationResult:
    from sayman.data import events

    u = Generator(line_items, rates, settings)
    raw_invoices = u.collect_invoices()
    u.generate_customers(raw_invoices)
    u.generate_invoices(raw_invoices)
    u.plan_collections()
    u.generate_purchasing()
    result = u.apply_plan()

    rng_ids = rng_stream(settings, "kimlik")
    customer_collections = assign_identifiers(result["tahsilatlar"], "tahsilat_id", "T", rng_ids,
                             lambda t: (t["tarih"], t["_gercek_fatura"], t["yontem"]))
    payments = assign_identifiers(u.base_payments + result["iade_odemeleri"], "odeme_id", "O", rng_ids,
                          lambda o: (o["tarih"], o["tur"], o["alis_fatura_id"] or "", o["iade_fatura_id"] or "",
                                     o["aciklama"], o["tutar_kurus"]))
    cheque_movements = assign_sequential_identifiers(result["cek_hareketleri"], "cek_hareket_id", "K", 7,
                                        lambda h: (h["tarih"], h["cek_id"]))
    opening = u.opening_balances(customer_collections, payments)

    records = {"tahsilatlar": customer_collections, "cekler": result["cekler"], "cek_hareketleri": cheque_movements,
                "odemeler": payments, "alis_faturalari": u.purchase_invoices}
    if settings.olaylar_acik:
        records = events.apply_events(u, copy.deepcopy(records), opening)

    for t in records["tahsilatlar"]:
        if t["eslesme"] == "bilerek_eslesmemis":
            u.key["eslesmeyen_tahsilatlar"][t["tahsilat_id"]] = t["_gercek_fatura"]

    tables = {
        "musteriler": _strip_private(u.customers),
        "faturalar": _strip_private(u.invoices),
        "pos_islemleri": _strip_private(u.pos),
        "tahsilatlar": _strip_private(records["tahsilatlar"]),
        "cekler": _strip_private(records["cekler"]),
        "cek_hareketleri": _strip_private(records["cek_hareketleri"]),
        "tedarikciler": _strip_private(u.suppliers),
        "alis_faturalari": _strip_private(records["alis_faturalari"]),
        "odemeler": _strip_private(records["odemeler"]),
        "banka_hesaplari": [
            {"hesap_id": h, "banka": b, "para_birimi": currency, "acilis_tarihi": u.start, "acilis_bakiye_kurus": opening[h]}
            for h, b, currency in ACCOUNTS
        ],
        "banka_hareketleri": u.ledger(records["tahsilatlar"], records["odemeler"], opening),
        "doviz_kurlari": [
            {"kur_id": f"{t.isoformat()}-{currency}", "tarih": t, "para_birimi": currency, "alis": str(k)}
            for t, currency, k in rates.rows(u.start, u.end)
        ],
    }
    return GenerationResult(tables=tables, key=u.key, applied_shift_days=u.shift_days, data_start=u.start,
                  unused_items_after_end=u.items_after_end)
