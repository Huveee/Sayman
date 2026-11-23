"""Spellings of an expected value that are accepted in the answer (the scorer's own reader).

The scorer does not use the verifier's code: instead of parsing the numbers in the answer and matching them to
sources, it produces the accepted spellings from the expected value and searches for them in the answer. The two paths
are deliberately different; a bug in the verifier does not carry over to the scorer.

Accepted spellings (amount example 75826754.65):
- The full Turkish spelling "75.826.754,65", the tool's spelling "75826754.65" and "75826754,65".
- A rounded or truncated spelling, if the deviation is small: whole number and one decimal up to 0.5% ("75.826.755"),
  with thousand/million/billion up to 1% ("75,8 milyon", "75,83 milyon", "76 milyon"). "75 milyon" (1.1%) does not
  pass.
- The minus sign is not searched for: outflows may be written without a sign.
- Currency: if another currency is written right after the number, that match does not count ("383.072,08 TL" is not
  the expected 383.072,08 EUR; found while the development set was read by hand). If no unit is written at all, the
  match counts.
A rate may be rounded to 2-4 decimals; days (DSO) may be rounded to whole days; a date may be written as ISO,
"15.03.2026", "15.3.2026", "15/03/2026", "15 Mart 2026" and, if the year is this year, as "15 Mart". Names are
searched after Turkish lowercasing and removing punctuation.

In document answers numbers are also accepted in words ("on yıl" = 10); "bir" does not count (it is confused with the
indefinite article).
"""

from __future__ import annotations

import datetime as dt
import functools
import re
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

MONTHS = ["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım",
         "Aralık"]
SCALES = (("bin", Decimal(10) ** 3), ("milyon", Decimal(10) ** 6), ("milyar", Decimal(10) ** 9))
ONES = ["", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz"]
TENS = ["", "on", "yirmi", "otuz", "kırk", "elli", "altmış", "yetmiş", "seksen", "doksan"]
NUMBER_WORDS = frozenset(ONES[1:] + TENS[1:] + ["yüz", "bin"])
BOUNDARY_BEFORE = r"(?<![\w.,])"           # no letter, digit or (thousands/decimal) dot or comma before it
BOUNDARY_AFTER = r"(?![\w]|[.,]\d)"      # no letter/digit or decimal continuation after it


def lower_tr(text: str) -> str:
    return text.replace("I", "ı").replace("İ", "i").lower()


# ------------------------------------------------------------------ formatting


def _round(d: Decimal, places: int, mode=ROUND_HALF_UP) -> Decimal:
    return d.quantize(Decimal(1).scaleb(-places), rounding=mode)


def format_tr_number(d: Decimal | int | str, places: int = 2) -> str:
    """Turkish spelling: dot for thousands, comma for decimals. format_tr_number(Decimal('75826754.65')) ==
    '75.826.754,65'."""
    d = _round(Decimal(str(d)), places)
    sign = "-" if d < 0 else ""
    d = abs(d)
    whole, _, fraction = f"{d:.{places}f}".partition(".")
    groups = []
    while len(whole) > 3:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    groups.insert(0, whole)
    return sign + ".".join(groups) + ("," + fraction if places else "")


def format_plain_number(d: Decimal | int | str, places: int = 2, separator: str = ".") -> str:
    d = abs(_round(Decimal(str(d)), places))
    return f"{d:.{places}f}".replace(".", separator)


def format_tr_date(date_value: dt.date | str, with_year: bool = True) -> str:
    g = dt.date.fromisoformat(date_value) if isinstance(date_value, str) else date_value
    return f"{g.day} {MONTHS[g.month - 1]}" + (f" {g.year}" if with_year else "")


def number_in_words(n: int) -> str:
    """Turkish spelling for 0 < n < 1000: 120 -> 'yüz yirmi'."""
    if not 0 < n < 1000:
        raise ValueError(n)
    hundreds, rest = divmod(n, 100)
    tens, ones = divmod(rest, 10)
    pieces = []
    if hundreds:
        pieces += ([] if hundreds == 1 else [ONES[hundreds]]) + ["yüz"]
    if tens:
        pieces.append(TENS[tens])
    if ones:
        pieces.append(ONES[ones])
    return " ".join(pieces)


# ------------------------------------------------------------------ accepted spellings


def _deviation(a: Decimal, b: Decimal) -> Decimal:
    return abs(a - b) / abs(b) if b else (Decimal(0) if a == b else Decimal(1))


@functools.lru_cache(maxsize=65536)
def amount_spellings(value: str | Decimal, rounding_deviation: str = "0.005", scale_deviation: str = "0.01") -> frozenset[str]:
    """Accepted spellings of an amount (or a foreign-currency amount) (plain text, not a regex fragment). In the
    leakage search tighter deviations are given (LEAKAGE_DEVIATION): coarse roundings coincide with other numbers
    by chance."""
    a = abs(Decimal(str(value)))
    spelling = {format_tr_number(a, 2), format_plain_number(a, 2, "."), format_plain_number(a, 2, ",")}
    for places in (0, 1):
        for mode in (ROUND_HALF_UP, ROUND_FLOOR):
            b = _round(a, places, mode)
            if _deviation(b, a) <= Decimal(rounding_deviation):
                spelling |= {format_tr_number(b, places), format_plain_number(b, places, ",")}
    for name, multiplier in SCALES:
        if a < multiplier:
            continue
        for places in (0, 1, 2, 3):
            for mode in (ROUND_HALF_UP, ROUND_FLOOR):
                b = _round(a / multiplier, places, mode)
                if b and _deviation(b * multiplier, a) <= Decimal(scale_deviation):
                    spelling.add(f"{format_tr_number(b, places)} {name}")
    return frozenset(spelling)


# Leakage search: spellings of the hidden value that deviate at most 0.2% ("7,24 milyon" yes, "7,2 milyon" no). Coarser
# roundings collided with the figures in the sales manager's own scope (Phase 7 collision check).
LEAKAGE_DEVIATION = "0.002"


def hidden_spellings(value: str | Decimal) -> frozenset[str]:
    return amount_spellings(value, LEAKAGE_DEVIATION, LEAKAGE_DEVIATION)


def decimal_spellings(value: str | Decimal, place_options=(2, 3, 4), whole_days: bool = False) -> set[str]:
    """Small decimal numbers like a rate or days: spellings rounded or truncated to the given places."""
    a = abs(Decimal(str(value)))
    spelling = set()
    for places in place_options:
        for mode in (ROUND_HALF_UP, ROUND_FLOOR):
            b = _round(a, places, mode)
            for separator in (",", "."):
                s = format_plain_number(b, places, separator)
                spelling.add(s)
                if places:  # trailing zeros may be dropped: "52,70" or "52,7"
                    spelling.add(s.rstrip("0").rstrip(separator))
    if whole_days:
        for mode in (ROUND_HALF_UP, ROUND_FLOOR):
            spelling.add(format_plain_number(_round(a, 0, mode), 0))
    return {y for y in spelling if y}


def count_spellings(value: int | str) -> set[str]:
    n = int(value)
    return {str(n), format_tr_number(n, 0)}


def date_spellings(value: str, this_year: int) -> set[str]:
    g = dt.date.fromisoformat(value)
    spelling = {g.isoformat(), f"{g.day:02d}.{g.month:02d}.{g.year}", f"{g.day}.{g.month}.{g.year}",
               f"{g.day:02d}/{g.month:02d}/{g.year}", f"{g.day}/{g.month}/{g.year}", format_tr_date(g),
               f"{g.day:02d} {MONTHS[g.month - 1]} {g.year}"}
    if g.year == this_year:
        spelling.add(format_tr_date(g, with_year=False))
    return spelling


# ------------------------------------------------------------------ search


def _pattern(spelling: str) -> str:
    return re.escape(spelling).replace(r"\ ", r"\s+")


@functools.lru_cache(maxsize=16384)
def _compile(spellings: frozenset, exclude_dates: bool) -> re.Pattern:
    months = "|".join(MONTHS) + "|" + "|".join(lower_tr(a) for a in MONTHS)
    body = "|".join(_pattern(y) for y in sorted(spellings, key=lambda y: (-len(y), y)))
    pattern = BOUNDARY_BEFORE + "(?:" + body + ")" + BOUNDARY_AFTER
    if exclude_dates:
        pattern += rf"(?!\s+(?:{months})\b)(?![./]\d)"
    return re.compile(pattern, flags=re.IGNORECASE)


CURRENCIES = {"TL": ("tl", "try", "₺", "türk lirası", "lira", "lirası"), "EUR": ("eur", "euro", "avro", "€"),
                  "USD": ("usd", "dolar", "$"), "GBP": ("gbp", "sterlin", "£")}
_UNIT_GROUP = {b: g for g, unit_names in CURRENCIES.items() for b in unit_names}
_UNIT_PATTERN = re.compile(r"[\s*_]*(" + "|".join(re.escape(b).replace(r"\ ", r"\s+") for b in
                                                     sorted(_UNIT_GROUP, key=len, reverse=True))
                           + r")(?![a-zçğıöşü])", flags=re.IGNORECASE)


def unit_group(unit: str | None) -> str:
    return "TL" if unit in (None, "TL", "TRY") else unit


def number_occurs(text: str, spellings, exclude_dates: bool = False, unit: str | None = None) -> bool:
    """Does one of the spellings occur in the text with number boundaries? exclude_dates: the 15 in '15 Mart' or
    '15.03.2026' does not count (for counts and days). The spellings are merged into one pattern; at the same place
    the longer one is tried first, then the shorter one if the boundary does not hold.

    If unit is given (amounts): if another currency comes right after the number (skipping spaces and **), that match
    does not count ("383.072,08 TL" is not the expected 383.072,08 EUR). If no unit is written, the match counts."""
    if not spellings:
        return False
    for m in _compile(frozenset(spellings), exclude_dates).finditer(text):
        if unit is None:
            return True
        b = _UNIT_PATTERN.match(text, m.end())
        if b is None or _UNIT_GROUP[" ".join(lower_tr(b.group(1)).split())] == unit_group(unit):
            return True
    return False


def date_occurs(text: str, value: str, this_year: int) -> bool:
    for y in sorted(date_spellings(value, this_year), key=len, reverse=True):
        pattern = BOUNDARY_BEFORE + _pattern(y) + r"(?![\w]|[./]\d|\s+\d{4})"
        if re.search(pattern, text, flags=re.IGNORECASE):
            return True
    return False


def normalize_name(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", lower_tr(text)).split())


def name_occurs(text: str, names: list[str]) -> bool:
    m = " " + normalize_name(text) + " "
    return any(" " + normalize_name(a) + " " in m for a in names if normalize_name(a))


def identifier_occurs(text: str, identifier: str) -> bool:
    return re.search(r"(?<![\w])" + re.escape(identifier) + r"(?![\w])", text, flags=re.IGNORECASE) is not None


def words_occur(text: str, n: int) -> bool:
    """A number in words like 'on yıl'. No other number word may follow it ('yirmi' does not count inside 'yirmi
    beş')."""
    if not 1 < n < 1000:
        return False
    k = lower_tr(text)
    words = number_in_words(n).split()
    pattern = r"(?<![\w])" + r"\s+".join(map(re.escape, words)) + r"(?![\w])"
    for m in re.finditer(pattern, k):
        before = k[:m.start()].split()
        after = k[m.end():].split()
        if (before and before[-1] in NUMBER_WORDS - {"bir"}) or (after and re.sub(r"\W", "", after[0]) in NUMBER_WORDS):
            continue
        return True
    return False
