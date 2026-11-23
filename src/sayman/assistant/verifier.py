"""Answer verifier: does every number in the model's answer appear in this turn's tool results, is every citation real?

Numbers:
- The answer is read in Turkish format: "75.826.754,65" (dot for thousands, comma for decimals). A spelling where
  exactly three digits follow the dot is always a thousands separator ("55.865" = 55865). The tools' own format
  ("48.1745", not three digits after the dot) is read as a decimal. "bin", "milyon", "milyar" are multipliers.
- Every number has a kind, determined by the word next to it: TL/USD/EUR/lira/dolar → amount, % or "yüzde" →
  percent, gün → day, ay → month, fatura/müşteri/çek/hareket... → count, a calendar day → date, a year like 2025 →
  year. A number of unknown kind can match a source of kind amount, rate, day, month, week or count; percent and
  date match only their own kind. So a made-up "%12" cannot match the 12 in "son 12 ay".
- Record numbers (the "_no" fields such as kaynak_fatura_no, cek_no) are a source of kind "numara": they match a
  number of unknown kind or of kind count ("564713'tür (fatura tarihi ...)", "1335451 no'lu çek"), not an amount.
- The only sources are: the values in the results of tools that returned successfully in this turn (typed by field
  name), the period patterns in field names (son_12_ay → 12 months, kur_yuzde_10 → 10%, gecikme_31_60 → 31 and 60
  days), the length of lists, the tool's day and row-count arguments, the article texts in document search results,
  the user's question and today's date. Refused calls and explanation and note texts are not sources. Numbers
  written in words in article texts and in the question are also sources ("son on iki ayın" → 12 months,
  "ikiyüzellibin Türk Lirası" → 250,000 TL); words with a suffix ("ikinci", "altına"), a lone "bir" and numbers in
  words with no kind word after them ("her iki borç", "binde üç") do not count.
- Matching: the source value, rounded or truncated to the precision shown in the answer, must give the number in
  the answer (75.826.754,65 → "75,8 milyon", "75,82 milyon", "75.826.755" pass; "75,81 milyon" does not). The sign
  is not compared (outflows may be written without a minus sign).
- Numbered-list markers ("1. ") and numbers attached to letters (F032047, KANUN-6102) do not count as numbers.
  Numbers inside quotes and chunk identifiers in square brackets ([KANUN-6102#m1535]) are not examined; the
  citation check audits quoted citations. In document results the article text, document name and article title
  are read as text; the law and article number in the chunk identifier are also a source ("6102 sayılı Kanun",
  "1535. madde").

Citations: the Phase 5 citation check, only against the article texts that document search returned in this turn.
If document search returned results in this turn, the answer must have at least one valid citation (Phase 7
decision); an answer that says "bulamadım" also quotes from the article it looked at.

Phase 8b rules (for errors found in the held-out sets of Phases 7 and 8; the old decisions stay in `legacy_valid`):
- Ownership: if the question asks about a specific customer (the question has a hint like "X müşterisinin" or a
  company type like "A.Ş.", "GmbH") and the answer has a number or date that comes from tool data, there must be a
  result belonging to that customer in this turn: a call made with the customer name that appears in the question
  (musteri_adi) or a record whose customer name appears in the question. Otherwise the answer counts as "ownerless"
  (e.g. the total of the whole portfolio when one customer's cheques are asked). The customer is recognised only
  from the question text, the call arguments and this turn's results; the database is not consulted. So a customer
  of the other region and a customer that does not exist get the same decision (Phase 3 guarantee).
- Identifier: the letter + digit identifiers in the answer (F035729, C560866) must appear verbatim in this turn's
  tool results or in the question. A made-up "C560878" is caught.
- Scope: if the number from a customer tool that gives a total for a sales role (receivables aging, overdue, DSO,
  cheque portfolio, invoice list) is used in the answer, the answer must state the scope ("yurtiçi", "yurt dışı").
  If it does not, the answer is not rejected; the orchestrator asks for one correction, and if that fails it
  appends the fixed scope note to the end of the answer.

Known limits:
- What a number belongs to is looked at only at customer level (ownership rule): it passes if the question does
  not name a customer explicitly, or if the answer uses the number of another result when a result belonging to
  that customer exists.
- Numbers in the user's question are sources: to "DSO 59 gün mü?" the answer "Evet, 59 gün" passes (the second
  blind spot).
- Only numbers written in digits in the answer are examined: "yirmi yıl" is not audited (the third blind spot;
  numbers in words are read only in source texts). Since a citation is required in document answers, the answer
  carries a real citation, but the claim next to the citation is not compared with it:
  'yirmi yıl saklanır: "on yıl süreyle saklamakla yükümlüdür" [KANUN-6102#m82]' passes.
- Double quotes are only for document citations: quoted text without a chunk identifier is rejected as
  "kimliksiz_alinti" (also a quoted customer name of three or more words or containing digits). This is
  deliberately strict; if it were loosened, numbers could hide inside quotes. A quoted phrase of one or two words
  without digits ('"kaç yıl" sorusuna') does not count as a citation.
- The result of the model's own operation (the sum of two amounts, a ratio) counts as unsourced and is caught.
- Small integers (3, 4) of unknown kind easily match many sources.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, localcontext

from sayman.documents.citation import MIN_WORDS, CitationResult, check_answer, quoted_spans

MONTHS = ["ocak", "şubat", "mart", "nisan", "mayıs", "haziran", "temmuz", "ağustos", "eylül", "ekim", "kasım",
         "aralık"]
SCALE_FACTORS = {"bin": Decimal(1000), "milyon": Decimal(10) ** 6, "milyar": Decimal(10) ** 9}
COMPATIBILITY = {
    "tutar": {"tutar", "kur", "bilinmeyen"},
    "kur": {"kur", "tutar", "bilinmeyen"},
    "yuzde": {"yuzde"},
    "gun": {"gun", "bilinmeyen"},
    "ay": {"ay", "bilinmeyen"},
    "hafta": {"hafta", "adet", "bilinmeyen"},
    "yil": {"yil"},
    "adet": {"adet", "numara", "bilinmeyen"},
    "bilinmeyen": {"tutar", "kur", "gun", "ay", "hafta", "adet", "numara", "bilinmeyen"},
}
# Record number fields (kaynak_fatura_no, cek_no): they arrive as text but are not amounts. A source of kind "numara"
# matches only a number of unknown kind or of kind count ("564713'tür (fatura tarihi ...)", "1335451 no'lu
# çek": the word "fatura"/"çek" after it makes the number a count). A record number written as an amount is caught.
_NUMBER_FIELD_PATTERN = re.compile(r"(?:^|_)no$")
# Numbers in words: read only in source texts (document article, the user's question); numbers in words in the
# answer are still not audited (a known limit). Words with a suffix ("ikinci", "altına", "onda") are not numbers; a
# lone "bir" is an indefinite article ("bir müşteri"), in a compound and with a scale word it is a number
# ("on bir", "bir milyon").
_ONES = {"bir": 1, "iki": 2, "üç": 3, "dört": 4, "beş": 5, "altı": 6, "yedi": 7, "sekiz": 8, "dokuz": 9}
_TENS = {"on": 10, "yirmi": 20, "otuz": 30, "kırk": 40, "elli": 50, "altmış": 60, "yetmiş": 70, "seksen": 80,
          "doksan": 90}
_SCALE_WORD_VALUES = {"bin": 1000, "milyon": 10 ** 6, "milyar": 10 ** 9}
_MORPHEMES = sorted([*_ONES, *_TENS, "yüz", *_SCALE_WORD_VALUES], key=len, reverse=True)
_LETTERS = re.compile(r"[^\W\d_]+")
CURRENCY_WORD = re.compile(r"^(tl|try|₺|lira\w*|usd|eur|gbp|dolar\w*|avro\w*|euro\w*|sterlin\w*|\$|€|£)$")
COUNT_NOUN = re.compile(r"^(fatura|müşteri|çek|hareket|işlem|adet|kalem|hesap|tahsilat|ödeme|tedarikçi|alacak|kayıt|"
                  r"sözleşme|madde|belge|satır)\w*$")
KIND_WORDS = [(CURRENCY_WORD, "tutar"), (re.compile(r"^gün\w*$"), "gun"), (re.compile(r"^hafta\w*$"), "hafta"),
                (re.compile(r"^(ay|aya|ayda|aydan|aydır|aylık|ayın|ayı|aylar\w*|aylık\w*)$"), "ay"),
                (re.compile(r"^(yıl|sene)\w*$"), "yil"), (COUNT_NOUN, "adet")]
PRECEDING_CURRENCY = re.compile(r"(?:[$€₺£]|\b(?:USD|EUR|GBP|TL|TRY))\s*$")

_ISO_DATE_PATTERN = re.compile(r"(?<!\d)(\d{4})-(\d{2})-(\d{2})(?!\d)")
_DOTTED_DATE_PATTERN = re.compile(r"(?<![\d.])(\d{1,2})\.(\d{1,2})\.(\d{4})(?!\d)")
_MONTH_NAME_DATE_PATTERN = re.compile(r"(?<!\d)(\d{1,2})\s+(" + "|".join(a.capitalize() for a in MONTHS) + "|" + "|".join(MONTHS)
                   + r")(?:'\w+)?(?:\s+(\d{4})(?!\d))?")
_NUMBER_PATTERN = re.compile(r"(?<![\w#.,])(?<![^\W\d_]-)(\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+,\d+|\d+\.\d+|\d+)(?!\w)")
_CHUNK_ID_PATTERN = re.compile(r"\[[A-Za-z0-9_\-]+#m\d+\]")
_CHUNK_NUMBER_PATTERN = re.compile(r"^(?:KANUN-(\d+))?.*#m(\d+)$")  # law number (if any) and article number
_SCALE_PATTERN = re.compile(r"\s*(bin|milyon|milyar)\b")
_WORD_PATTERN = re.compile(r"[^\s()\[\]:;,.!?\"“”«»]+")
_EDGE_PATTERN = re.compile(r"^[^\w₺$€£]+|[^\w₺$€£]+$")
_MONTH_NAMES = "|".join(a.capitalize() for a in MONTHS) + "|" + "|".join(MONTHS)
_DAY_RANGE_PATTERN = re.compile(r"(?<!\d)(\d{1,2})\s*[-–]\s*(\d{1,2})\s+(" + _MONTH_NAMES + r")(?:'\w+)?(?:\s+(\d{4})(?!\d))?")
_PERIOD_KEY_PATTERN = re.compile(r"(?:^|_)son_(\d+)_(gun|ay|yil|hafta)(?:_|$)")
_PERCENT_KEY_PATTERN = re.compile(r"(?:^|_)yuzde_(\d+)(?:_|$)")
_BUCKET_KEY_PATTERN = re.compile(r"(?:^|_)gecikme_(\d+)_(\d+|ustu)(?:_|$)")
_ISO_FULL_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NUMERIC_TEXT_PATTERN = re.compile(r"^-?\d+(?:\.\d+)?$")
_SNAKE_CASE_PATTERN = re.compile(r"^[a-z0-9_]+$")


@dataclass(frozen=True)
class Number:
    metin: str
    start: int
    end: int
    tur: str
    deger: Decimal | None = None      # absolute value, multiplier applied
    unit: Decimal | None = None      # the value of the last digit shown
    tarih: dt.date | None = None
    day_month: tuple[int, int] | None = None   # date without a year: (day, month)


@dataclass(frozen=True)
class Source:
    tur: str
    alan: str
    call_id: str
    deger: Decimal | None = None
    tarih: dt.date | None = None


@dataclass(frozen=True)
class NumberResult:
    metin: str
    tur: str
    gecerli: bool
    sources: tuple[tuple[str, str], ...] = ()   # (call_id, alan)


@dataclass
class Verification:
    numbers: list[NumberResult] = field(default_factory=list)
    citations: list[CitationResult] = field(default_factory=list)
    citation_required: bool = False     # document search returned results in this turn (Phase 7 decision: citation required)
    unknown_identifiers: list[str] = field(default_factory=list)   # Phase 8b: identifiers that are not in the tool results
    ownerless: bool = False           # Phase 8b: a customer question, but no result belongs to that customer
    missing_scope: str | None = None  # Phase 8b: the name of the restricted scope if the answer does not state it (does not reject the answer)

    @property
    def unsourced(self) -> list[NumberResult]:
        return [s for s in self.numbers if not s.gecerli]

    @property
    def invalid_citations(self) -> list[CitationResult]:
        return [a for a in self.citations if not a.gecerli]

    @property
    def citation_missing(self) -> bool:
        return self.citation_required and not any(a.gecerli for a in self.citations)

    @property
    def legacy_valid(self) -> bool:
        """The Phase 6-8 rules (numbers and citations)."""
        return not self.unsourced and not self.invalid_citations and not self.citation_missing

    @property
    def gecerli(self) -> bool:
        return self.legacy_valid and not self.unknown_identifiers and not self.ownerless


# ---------------------------------------------------------------- reading numbers from text


def _lower(text: str) -> str:
    return text.replace("I", "ı").replace("İ", "i").lower()


def _make_date(year: int, month: int, day: int) -> dt.date | None:
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def _find_dates(text: str) -> list[Number]:
    found: list[Number] = []
    for m in _ISO_DATE_PATTERN.finditer(text):
        found.append(Number(m.group(0), m.start(), m.end(), "tarih",
                            tarih=_make_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))))
    for m in _DOTTED_DATE_PATTERN.finditer(text):
        found.append(Number(m.group(0), m.start(), m.end(), "tarih",
                            tarih=_make_date(int(m.group(3)), int(m.group(2)), int(m.group(1)))))
    for m in _MONTH_NAME_DATE_PATTERN.finditer(text):
        month = MONTHS.index(_lower(m.group(2))) + 1
        if m.group(3):
            found.append(Number(m.group(0), m.start(), m.end(), "tarih",
                                tarih=_make_date(int(m.group(3)), month, int(m.group(1)))))
        else:
            found.append(Number(m.group(0), m.start(), m.end(), "tarih", day_month=(int(m.group(1)), month)))
    for m in _DAY_RANGE_PATTERN.finditer(text):  # "9-15 Eylül": two dates
        month = MONTHS.index(_lower(m.group(3))) + 1
        for group, end in ((1, m.end(1)), (2, m.end())):
            day = int(m.group(group))
            if m.group(4):
                found.append(Number(text[m.start(group):end], m.start(group), end, "tarih",
                                    tarih=_make_date(int(m.group(4)), month, day)))
            else:
                found.append(Number(text[m.start(group):end], m.start(group), end, "tarih", day_month=(day, month)))
    found.sort(key=lambda s: (s.start, -s.end))
    result, last = [], -1
    for s in found:  # of the overlapping ones, the first and longest
        if s.start >= last:
            result.append(s)
            last = s.end
    return result


def _parse_value(text: str) -> tuple[Decimal, Decimal]:
    """Turkish-first reading: (value, value of the last digit)."""
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?", text):
        integer_part, _, fraction = text.partition(",")
        integer_part = integer_part.replace(".", "")
    elif "," in text:
        integer_part, _, fraction = text.partition(",")
    elif "." in text:
        integer_part, _, fraction = text.partition(".")
    else:
        integer_part, fraction = text, ""
    return Decimal(f"{integer_part}.{fraction}" if fraction else integer_part), Decimal(1).scaleb(-len(fraction))


def _following_words(text: str, position: int, count: int = 2) -> list[str]:
    rest = text[position:]
    suffix = re.match(r"['’]\w+", rest)  # 3'ü, 30'u: a suffix attached to the number
    if suffix:
        rest = rest[suffix.end():]
    line = rest.split("\n", 1)[0]
    words = []
    for k in _WORD_PATTERN.findall(line):
        k = _EDGE_PATTERN.sub("", k)  # formatting marks like "TL**"
        if k:
            words.append(re.split(r"['’]", _lower(k), maxsplit=1)[0])
        if len(words) == count:
            break
    return words


def _kind(text: str, start: int, end: int, value_text: str, scaled: bool) -> str:
    before = text[max(0, start - 7):start]
    if before.rstrip().endswith("%") or _lower(before).rstrip().endswith("yüzde") or text[end:end + 1] == "%":
        return "yuzde"
    if PRECEDING_CURRENCY.search(before):
        return "tutar"
    for word in _following_words(text, end):
        for pattern, kind in KIND_WORDS:
            if pattern.match(word):
                return kind
    if not scaled and re.fullmatch(r"(19|20)\d\d", value_text):
        return "yil"
    return "bilinmeyen"


def numbers_from_text(text: str, skip: list[tuple[int, int]] = ()) -> list[Number]:
    """The numbers and dates in a Turkish text. skip: (start, end) ranges that are not examined."""
    def outside(start, end):
        return not any(b <= start and end <= s for b, s in skip)

    numbers = [t for t in _find_dates(text) if outside(t.start, t.end)]
    mask = list(text)
    for t in _find_dates(text):
        mask[t.start:t.end] = " " * (t.end - t.start)
    masked = "".join(mask)
    for m in _NUMBER_PATTERN.finditer(masked):
        start, end = m.start(), m.end()
        if not outside(start, end):
            continue
        line_start = masked.rfind("\n", 0, start) + 1
        if not masked[line_start:start].strip(" \t*#>-_") and masked[end:end + 2] in (". ", ") "):
            continue  # numbered-list marker (may be preceded by formatting marks like "*", "-", "**")
        value, unit = _parse_value(m.group(1))
        scale = _SCALE_PATTERN.match(masked, end)
        last = end
        if scale:
            multiplier = SCALE_FACTORS[scale.group(1)]
            value, unit, last = value * multiplier, unit * multiplier, scale.end()
        kind = _kind(masked, start, last, m.group(1), bool(scale))
        if kind == "adet" and value != value.to_integral_value():
            kind = "bilinmeyen"  # a fractional number cannot be a count ("43.510.100,56 TL ödeme")
        if kind == "tutar" and value == 1 and not scale and re.match(r"\s*\S+\s*=", masked[last:]):
            continue  # "1 USD = 48,17 TL": a unit, not a claim
        percent_start = start - 1 if masked[start - 1:start] == "%" else start
        numbers.append(Number(text[percent_start:last], percent_start, last, kind, value, unit))
    return sorted(numbers, key=lambda s: s.start)


# ---------------------------------------------------------------- sources


def _key_patterns(name: str, call_id: str) -> list[Source]:
    k = []
    for m in _PERIOD_KEY_PATTERN.finditer(name):
        k.append(Source(m.group(2), name, call_id, Decimal(m.group(1))))
    for m in _PERCENT_KEY_PATTERN.finditer(name):
        k.append(Source("yuzde", name, call_id, Decimal(m.group(1))))
    for m in _BUCKET_KEY_PATTERN.finditer(name):
        k.append(Source("gun", name, call_id, Decimal(m.group(1))))
        if m.group(2) != "ustu":
            k.append(Source("gun", name, call_id, Decimal(m.group(2))))
    return k


def _from_values(value, field_name: str, call_id: str, sources: list[Source]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            sources.extend(_key_patterns(k, call_id))
            _from_values(v, k, call_id, sources)
    elif isinstance(value, list):
        sources.append(Source("adet", f"{field_name}[]", call_id, Decimal(len(value))))
        for v in value:
            _from_values(v, field_name, call_id, sources)
    elif isinstance(value, bool) or value is None:
        return
    elif isinstance(value, int):
        kind = "gun" if "gun" in field_name else ("hafta" if field_name == "hafta" else "adet")
        sources.append(Source(kind, field_name, call_id, abs(Decimal(value))))
    elif isinstance(value, str):
        if _ISO_FULL_PATTERN.match(value):
            t = dt.date.fromisoformat(value)
            sources.extend([Source("tarih", field_name, call_id, tarih=t), Source("yil", field_name, call_id, Decimal(t.year))])
        elif _NUMERIC_TEXT_PATTERN.match(value):
            if _NUMBER_FIELD_PATTERN.search(field_name):
                kind = "numara"
            else:
                kind = "kur" if field_name in ("kur", "alis") else ("gun" if "gun" in field_name else "tutar")
            sources.append(Source(kind, field_name, call_id, abs(Decimal(value))))
        elif _SNAKE_CASE_PATTERN.match(value):  # option values like "son_90_gun"
            sources.extend(_key_patterns(value, call_id))


def _split_morphemes(word: str) -> list[str] | None:
    """Splits a number word without a suffix into its parts ("yirmibeş" → yirmi, beş); None if it is not a number."""
    if not word:
        return []
    for m in _MORPHEMES:
        if word.startswith(m):
            rest = _split_morphemes(word[len(m):])
            if rest is not None:
                return [m, *rest]
    return None


def _numbers_in_words(text: str) -> list[tuple[int, int, Decimal, bool]]:
    """The numbers written in words in the text: (start, end, value, scaled). "son on iki ayın" → 12; "iki yüz elli
    bin" → 250000. A badly ordered sequence ("iki üç", "on yirmi") is split there."""
    lowered = _lower(text)
    result: list[tuple[int, int, Decimal, bool]] = []
    state: dict = {}

    def finish():
        if state.get("parca"):
            # a lone "bir" is an indefinite article
            if state["parca"] != ["bir"]:
                value = state["toplam"] + state["blok"]
                result.append((state["bas"], state["bit"], Decimal(value), state["olcekli"]))
        state.clear()

    def add_part(m: str) -> bool:
        last, block = state.get("son"), state.get("blok", 0)
        if m in _ONES:
            if last not in (None, "onlar", "yuz", "olcek"):
                return False
            state["blok"] = block + _ONES[m]
            state["son"] = "birler"
        elif m in _TENS:
            if last not in (None, "yuz", "olcek"):
                return False
            state["blok"] = block + _TENS[m]
            state["son"] = "onlar"
        elif m == "yüz":
            if last == "birler" and block < 10:
                state["blok"] = block * 100
            elif last in (None, "olcek"):
                state["blok"] = 100
            else:
                return False
            state["son"] = "yuz"
        else:
            v = _SCALE_WORD_VALUES[m]
            if last == "olcek" or (state.get("son_olcek") or v + 1) <= v:
                return False
            state["toplam"] = state.get("toplam", 0) + (block or 1) * v
            state.update(blok=0, son="olcek", son_olcek=v, olcekli=True)
        return True

    previous_end = None
    for w in _LETTERS.finditer(lowered):
        parts = _split_morphemes(w.group(0))
        adjacent = previous_end is not None and not lowered[previous_end:w.start()].strip(" \t")
        if not adjacent:
            finish()
        if parts is None:
            finish()
            previous_end = None
            continue
        for attempt in range(2):
            backup = dict(state)
            if all(add_part(m) for m in parts):
                break
            state.clear()
            state.update(backup)
            finish()  # the order broke: the previous number ends, a new one starts with this word
        else:
            previous_end = None
            continue
        if "bas" not in state:
            state.update(bas=w.start(), toplam=state.get("toplam", 0), olcekli=state.get("olcekli", False),
                         parca=[])
        state["bit"] = w.end()
        state["parca"] = state["parca"] + parts
        previous_end = w.end()
    finish()
    return result


def _sources_from_text(text: str, field_name: str, call_id: str) -> list[Source]:
    k = []
    for s in numbers_from_text(text):
        if s.tur == "tarih":
            if s.tarih:
                k.extend([Source("tarih", field_name, call_id, tarih=s.tarih),
                          Source("yil", field_name, call_id, Decimal(s.tarih.year))])
        else:
            k.append(Source(s.tur, field_name, call_id, s.deger))
    for start, end, value, scaled in _numbers_in_words(text):
        kind = _kind(text, start, end, str(value), scaled)
        if kind != "bilinmeyen":  # "her iki borç", "binde üç": a source without a kind would match every kind and loosen the check
            k.append(Source(kind, field_name, call_id, value))
    return k


def _from_arguments(arguments: dict, call_id: str) -> list[Source]:
    k = []
    for name, v in arguments.items():
        if isinstance(v, int) and not isinstance(v, bool):
            k.append(Source("gun" if "gun" in name else "adet", f"arguman:{name}", call_id, abs(Decimal(v))))
        elif isinstance(v, str) and _ISO_FULL_PATTERN.match(v):
            t = dt.date.fromisoformat(v)
            k.extend([Source("tarih", f"arguman:{name}", call_id, tarih=t),
                      Source("yil", f"arguman:{name}", call_id, Decimal(t.year))])
        elif isinstance(v, str) and _SNAKE_CASE_PATTERN.match(v):
            k.extend(_key_patterns(v, call_id))
    return k


def _document_sources(result: dict, call_id: str) -> list[Source]:
    """Document search: the article text, document name and article title are read as Turkish text; the law and
    article numbers come from the chunk identifier (KANUN-6102#m1535 → 6102 and 1535)."""
    k = []
    for p in result.get("sonuclar", []):
        for field_name in ("metin", "belge", "madde"):
            if isinstance(p.get(field_name), str):
                k += _sources_from_text(p[field_name], field_name, call_id)
        m = _CHUNK_NUMBER_PATTERN.search(p.get("parca_id", ""))
        if m:
            k += [Source("adet", "parca_id", call_id, Decimal(n)) for n in m.groups() if n]
    return k


def collect_sources(question: str, calls: list[dict], today: dt.date) -> list[Source]:
    """calls: dicts of {"arac", "argumanlar", "hata_mi", "yanit"}; only the successful ones become sources."""
    k = _sources_from_text(question, "soru", "soru")
    k += [Source("tarih", "bugun", "bugun", tarih=today), Source("yil", "bugun", "bugun", Decimal(today.year))]
    for c in calls:
        if c.get("hata_mi"):
            continue
        y = c["yanit"]
        cid = y["cagri_id"]
        if c["arac"] == "belge_ara":
            k += _document_sources(y.get("sonuc") or {}, cid)
        else:
            _from_values(y.get("sonuc"), "sonuc", cid, k)
        if y.get("tarih") and _ISO_FULL_PATTERN.match(y["tarih"]):  # the day the tool ran (not data)
            t = dt.date.fromisoformat(y["tarih"])
            k += [Source("tarih", "cagri_tarihi", cid, tarih=t), Source("yil", "cagri_tarihi", cid, Decimal(t.year))]
        k += _from_arguments(c.get("argumanlar") or {}, cid)
    return k


# ---------------------------------------------------------------- matching


def _matches(s: Number, k: Source) -> bool:
    if s.tur == "tarih":
        if k.tur != "tarih" or k.tarih is None:
            return False
        if s.tarih:
            return s.tarih == k.tarih
        return s.day_month == (k.tarih.day, k.tarih.month)
    if k.tur not in COMPATIBILITY[s.tur] or k.deger is None:
        return False
    with localcontext() as c:
        c.prec = 50
        ratio = k.deger / s.unit
        target = s.deger / s.unit
        return target in (ratio.quantize(Decimal(1), ROUND_HALF_UP), ratio.quantize(Decimal(1), ROUND_FLOOR))


# ---------------------------------------------------------------- Phase 8b: ownership, identifier and scope

WHOLE_COMPANY = "tüm şirket"
# Customer tools that give totals: for a sales role their results are only the totals of that role's customers.
SCOPED_TOOLS = frozenset({"alacak_yaslandirma", "geciken_alacaklar", "dso_hesapla", "cek_portfoyu", "fatura_ara"})
SCOPE_PHRASES = {"yalnızca yurtiçi müşteriler": re.compile(r"yurt\s?i[çc]i", re.IGNORECASE),
                  "yalnızca yurt dışı müşteriler": re.compile(r"yurt\s?d[ıi][şs][ıi]|ihracat", re.IGNORECASE)}
# Fixed note: deliberately does not contain the words "yetki" and "kapsam" (the scorer counts them as a refusal marker).
SCOPE_NOTES = {"yalnızca yurtiçi müşteriler": "Not: Bu rakamlar yalnızca yurtiçi müşterilerinizi kapsar.",
                  "yalnızca yurt dışı müşteriler": "Not: Bu rakamlar yalnızca yurt dışı müşterilerinizi kapsar."}
# "X müşterisinin": it must be immediately preceded by a name starting with a capital letter ("faturanın müşterisi kim?" does not name a customer).
_CUSTOMER_HINT_PATTERN = re.compile(r"(?<![\w.])[A-ZÇĞİÖŞÜ][\w.]*\s+(?:müşterisi|firması)\w*")
_COMPANY_TYPE_PATTERN = re.compile(r"(?<![\w.])(?:A\.Ş\.?|AŞ|BV|SL|AG|(?i:a\.ş\.|ltd\.?|şti\.?|gmbh|sarl|srl|lda|inc\.?|llc|plc))"
                          r"(?!\w)")
COMPANY_WORDS = frozenset("ltd şti san ve tic a ş aş gmbh sarl bv sl srl ag pty lda inc llc plc co".split())
_IDENTIFIER_PATTERN = re.compile(r"(?<![\w#\-\[/])([A-Za-z]{1,4}\d{3,})(?![\w#\-\]/])")
_IDENTIFIER_PART_PATTERN = re.compile(r"[A-Za-z0-9]+")


def _normalize_name(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", _lower(text)).split())


def name_forms(name: str) -> set[str]:
    """The spellings of a customer name to look for in the question: the full name, the forms with trailing
    company-type words dropped, and the first two words ("Vadi Mavi Dekorasyon Ltd. Şti." → "vadi mavi dekorasyon
    ltd şti", "vadi mavi dekorasyon", "vadi mavi")."""
    k = _normalize_name(name).split()
    forms = {" ".join(k)} if k else set()
    while len(k) > 2 and k[-1] in COMPANY_WORDS:
        k = k[:-1]
        forms.add(" ".join(k))
    if len(k) >= 2:
        forms.add(" ".join(k[:2]))
    return forms


def _in_question(name: str, question_normalized: str) -> bool:
    return any(f" {b} " in f" {question_normalized} " for b in name_forms(name))


def is_customer_question(question: str) -> bool:
    """Does the question name a specific customer? Only the question text is looked at."""
    return bool(_CUSTOMER_HINT_PATTERN.search(question) or _COMPANY_TYPE_PATTERN.search(question))


def _customer_records(value):
    """The customer names of the records in the result that have a "musteri" field."""
    if isinstance(value, dict):
        if isinstance(value.get("musteri"), str):
            yield value["musteri"]
        for v in value.values():
            yield from _customer_records(v)
    elif isinstance(value, list):
        for v in value:
            yield from _customer_records(v)


def _is_specific(c: dict) -> bool:
    """Is the call about a single customer or invoice (not a total)?"""
    a = c.get("argumanlar") or {}
    return any(isinstance(a.get(k), str) and a[k].strip() for k in ("musteri_adi", "fatura_no"))


def _has_owned_result(question: str, calls: list[dict]) -> bool:
    """Is there a result in this turn that belongs to the customer named in the question: a call made with a customer
    whose name appears in the question, or a record whose customer name appears in the question."""
    question_normalized = _normalize_name(question)
    for c in calls:
        if c.get("hata_mi") or c["arac"] == "belge_ara":
            continue
        name = (c.get("argumanlar") or {}).get("musteri_adi")
        if isinstance(name, str) and name.strip() and _in_question(name, question_normalized):
            return True
        if any(_in_question(m, question_normalized) for m in _customer_records((c.get("yanit") or {}).get("sonuc"))):
            return True
    return False


def _data_sources(s: NumberResult, calls_by_id: dict[str, dict]) -> list[dict]:
    """The tool calls that are the source of the number (other than the question, today, arguments, the day the tool ran and document search)."""
    return [calls_by_id[cid] for cid, field_name in s.sources
            if cid in calls_by_id and not field_name.startswith("arguman:") and field_name != "cagri_tarihi"
            and calls_by_id[cid]["arac"] != "belge_ara"]


def _known_identifiers(question: str, calls: list[dict], data_only: bool = False) -> set[str]:
    """The identifier parts that appear in the question and in this turn's tool results. data_only: only the results
    of tools other than document search (without the question and the arguments)."""
    known = set() if data_only else {p.upper() for p in _IDENTIFIER_PART_PATTERN.findall(question)}

    def collect(value):
        if isinstance(value, dict):
            for v in value.values():
                collect(v)
        elif isinstance(value, list):
            for v in value:
                collect(v)
        elif isinstance(value, str):
            known.update(p.upper() for p in _IDENTIFIER_PART_PATTERN.findall(value))

    for c in calls:
        if c.get("hata_mi") or (data_only and c["arac"] == "belge_ara"):
            continue
        collect((c.get("yanit") or {}).get("sonuc"))
        if not data_only:
            collect(c.get("argumanlar") or {})
    return known


def identifiers(text: str, skip: list[tuple[int, int]] = ()) -> list[str]:
    """The letter + digit identifiers in the text (F035729, C560866); except those in the skip ranges."""
    return [m.group(1) for m in _IDENTIFIER_PATTERN.finditer(text)
            if not any(b <= m.start() and m.end() <= s for b, s in skip)]


def _phase8b_rules(result: Verification, answer: str, question: str, calls: list[dict], skip: list[tuple[int, int]]) -> None:
    calls_by_id = {c["yanit"]["cagri_id"]: c for c in calls
             if not c.get("hata_mi") and isinstance(c.get("yanit"), dict) and c["yanit"].get("cagri_id")}
    known = _known_identifiers(question, calls)
    answer_identifiers = identifiers(answer, skip)
    result.unknown_identifiers = [k for k in answer_identifiers if k.upper() not in known]
    in_question = {p.upper() for p in _IDENTIFIER_PART_PATTERN.findall(question)}
    data_identifiers = _known_identifiers(question, calls, data_only=True) - in_question
    data_items = [(s, _data_sources(s, calls_by_id)) for s in result.numbers if s.gecerli]
    data_items = [(s, k) for s, k in data_items if k]
    identifier_from_data = any(k.upper() in data_identifiers for k in answer_identifiers)
    if (data_items or identifier_from_data) and is_customer_question(question) and not _has_owned_result(question, calls):
        result.ownerless = True
    for s, sources in data_items:
        if s.tur == "tarih":  # the scope applies to amounts, counts and days
            continue
        for c in sources:
            scope = (c.get("yanit") or {}).get("kapsam")
            if (c["arac"] in SCOPED_TOOLS and scope in SCOPE_PHRASES and not _is_specific(c)
                    and not SCOPE_PHRASES[scope].search(answer)):
                result.missing_scope = scope
                return


def verify(answer: str, question: str, calls: list[dict], today: dt.date) -> Verification:
    sources = collect_sources(question, calls, today)
    result = Verification()
    skip = quoted_spans(answer) + [(m.start(), m.end()) for m in _CHUNK_ID_PATTERN.finditer(answer)]
    for s in numbers_from_text(answer, skip):
        found = tuple(dict.fromkeys((k.call_id, k.alan) for k in sources if _matches(s, k)))
        result.numbers.append(NumberResult(s.metin, s.tur, bool(found), found))
    _phase8b_rules(result, answer, question, calls, skip)
    texts = {}
    for c in calls:
        if not c.get("hata_mi") and c["arac"] == "belge_ara":
            for p in c["yanit"]["sonuc"].get("sonuclar", []):
                texts[p["parca_id"]] = p["metin"]
    # A quoted phrase without digits and shorter than three words ('"kaç yıl" sorusuna') does not count as a citation:
    # no number can hide in it, and it would be invalid as a citation anyway (cok_kisa).
    result.citations = [a for a in check_answer(answer, texts)
                       if not (a.reason == "kimliksiz_alinti" and not any(ch.isdigit() for ch in a.quote)
                               and len(a.quote.split()) < MIN_WORDS)]
    result.citation_required = bool(texts)
    return result
