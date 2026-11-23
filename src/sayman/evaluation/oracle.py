"""Oracle model and negative controls: fake models for testing the setup itself.

The oracle knows the answer to every question: first it calls the tool in the question set with the expected
arguments, then it writes the expected values in the Turkish format. It goes through the real orchestrator, gate and
verifier; if the setup is correct it gets full marks on every metric. In document questions the oracle's searcher
returns the right article (OracleSearcher); the oracle quotes a passage from the article that contains the key values.

Negative controls are degraded versions of the oracle; each must lose on its own metric:
- yanlis_arac: calls an unrelated permitted tool and writes its number → tool selection and digit accuracy drop.
- bir_hane: changes one digit of the expected value → the verifier rejects, digit accuracy drops.
- hep_ret: says "veremem" without calling any tool → digit accuracy and document answer drop (there is no leakage).
- baska_musteri: in customer questions calls the same tool for another customer and writes that customer's value for
  the customer asked about → the verifier lets it pass (known blind spot), the scorer counts it wrong.
- yaziyla: writes a number different from the expected value in words ("yetmiş yedi milyon") → the verifier lets it
  pass (it does not look at numbers in words), the scorer counts it wrong.
- yanlis_birim: writes amounts with the right number but the wrong currency ("TL" for a EUR amount, "EUR" for a TL
  amount) → the verifier lets it pass (it does not look at the unit), the scorer counts it wrong in amount questions.
  Found in one answer of the real model on the development set ("383.072,08 TL", expected EUR).
- uydurma_kimlik (Phase 8b identifier measurement): calls the tool correctly but writes in the answer a record number
  that is not in the tool result. The format varies with the question identifier: the system format (F09xxxx),
  hyphenated (F-2026-xxx), mixed letters and digits (like TA3B7C1D9). Since the truth is known, it tests the labels of
  the identifier analysis and the "which rule stopped it" breakdown.

In the "kimlik_yok" questions of the identifier set (the requested number is not in the tool result) the oracle gives
no number and says so.
"""

from __future__ import annotations

import json
import re
import zlib
from decimal import Decimal

from sayman.assistant.orchestrator import ModelResponse
from sayman.documents.library import can_see
from sayman.evaluation.scoring import get_path
from sayman.evaluation.value_formats import format_plain_number, lower_tr, number_in_words, format_tr_number, format_tr_date

CONTROLS = ("yanlis_arac", "bir_hane", "hep_ret", "baska_musteri", "yaziyla", "yanlis_birim",
              "uydurma_kimlik")
FABRICATED_STYLES = ("sistem", "tireli", "karisik")
_FALLBACK_TOOLS = [("doviz_kuru", {"para_birimi": "USD"}, ["alis"], "kur"),
                  ("dso_hesapla", {"donem": "son_30_gun"}, ["dso_gun"], "gun")]


def _unit(b) -> str:
    return "TL" if b in (None, "TL", "TRY") else b


def write_value(a: dict) -> str:
    kind, d = a["tur"], a["deger"]
    if kind in ("tutar", "doviz"):
        return f"{format_tr_number(abs(Decimal(str(d))), 2)} {_unit(a.get('birim'))}"
    if kind == "kur":
        places = max(-Decimal(str(d)).as_tuple().exponent, 0)
        return f"{format_plain_number(d, places, ',')} TL"
    if kind == "gun":
        x = Decimal(str(d))
        return f"{format_plain_number(x, 0) if x == x.to_integral_value() else format_plain_number(x, 2, ',')} gün"
    if kind == "adet":
        return str(int(d))
    if kind == "tarih":
        return format_tr_date(d)
    return str(d)


def _change_one_digit(text: str) -> str:
    """Changes the last digit of the first digit group: '75.826.754,65 TL' → '75.826.755,65 TL'."""
    m = re.search(r"\d(?=\D|$)", text)
    if not m:
        return text
    r = str((int(m.group()) + 1) % 10)
    return text[:m.start()] + r + text[m.end():]


def _wrong_in_words(a: dict) -> str:
    """The spelling in words of a number different from the expected value (one larger)."""
    v = abs(Decimal(str(a["deger"])))
    if v >= 10 ** 6:
        n, scale = int(v // 10 ** 6) + 1, " milyon"
    elif v >= 1000:
        n, scale = int(v // 1000) + 1, " bin"
    else:
        n, scale = int(v) + 1, ""
    if n >= 1000:
        n = 999
    return f"yaklaşık {number_in_words(max(n, 2))}{scale}"


class OracleSearcher:
    """A searcher that returns the right article in document questions (if the role allows it); returns nothing in
    other questions."""

    def __init__(self, chunks: dict):
        self.chunk_by_id = chunks
        self.soru: dict | None = None

    def search(self, query: str, role: str, k: int = 3, method: str = "anlam"):
        chunk_id = (self.soru or {}).get("dogru_parca") or (self.soru or {}).get("gizli_parca")
        p = self.chunk_by_id.get(chunk_id)
        return [(p, 1.0)] if p is not None and can_see(role, p.roller) else []


def _key_positions(words: list[str], a: dict) -> list[int]:
    """Positions of the key among the words of the article: written in digits ("120", "%2,25", "50.000") or in words
    ("on iki")."""
    k = [lower_tr(x).strip(".,;:()%\"'") for x in words]
    if a["tur"] == "tarih":
        year = a["deger"][:4]
        return [i for i, x in enumerate(k) if year in x]
    if a["tur"] == "metin":  # words may take suffixes ("genel müdürün")
        wanted = lower_tr(a["deger"]).split()
        return [i for i in range(len(k) - len(wanted) + 1)
                if all(k[i + j].startswith(w) for j, w in enumerate(wanted))]
    d = a["deger"]
    found_positions = [i for i, x in enumerate(k) if x.replace(".", "").replace(",", ".") == d]
    n = Decimal(d)
    if not found_positions and n == n.to_integral_value() and 1 < n < 1000:
        spelled = number_in_words(int(n)).split()
        found_positions = [i for i in range(len(k)) if k[i:i + len(spelled)] == spelled]
    return found_positions


def citation_window(text: str, keys: list[dict], margin: int = 3) -> str:
    """A window of words from the article text that covers the first occurrences of all the keys (`margin` words on
    each side). The window occurs in the article verbatim; it is cut at a word that contains a double quote."""
    words = text.split()
    positions = [min(k) for a in keys if (k := _key_positions(words, a))]
    start = max(min(positions, default=0) - margin, 0)
    end = min(max(positions, default=0) + margin + 1, len(words))
    if end - start < 4:
        end = min(start + 4, len(words))
    selected = []
    for x in words[start:end]:
        if '"' in x:
            break
        selected.append(x)
    return " ".join(selected).rstrip(" .,;:")


def _numberless_window(text: str, keys: list[dict]) -> str:
    """A four-word window containing no digit and no key number word (for the yaziyla control)."""
    words = text.split()
    forbidden = {w for a in keys if a["tur"] == "sayi" and Decimal(a["deger"]) == Decimal(a["deger"]).to_integral_value()
             and 1 < Decimal(a["deger"]) < 1000 for w in number_in_words(int(Decimal(a["deger"]))).split()}
    texts = [lower_tr(a["deger"]).split()[0] for a in keys if a["tur"] == "metin"]
    for i in range(len(words) - 3):
        p = words[i:i + 4]
        if not any(c.isdigit() or c == '"' for w in p for c in w) and not any(lower_tr(w).strip(".,;:") in forbidden for w in p) \
                and not any(lower_tr(w).startswith(m) for w in p for m in texts):
            return " ".join(p).rstrip(" .,;:")
    return " ".join(words[:4]).rstrip(" .,;:")


def fabricated_identifier(question_id: str) -> tuple[str, str]:
    """(style, number): a record number that is not in the database; the style varies with the question identifier."""
    crc = zlib.crc32(question_id.encode("utf-8"))
    style = FABRICATED_STYLES[crc % 3]
    if style == "sistem":  # invoice numbers go up to F041314; there is no F09xxxx
        return style, f"F09{crc % 10000:04d}"
    if style == "tireli":
        return style, f"F-2026-{crc % 1000:03d}"
    # letter and single digit in turn: does not fit the pattern of the new rule (letter + at least 3 digits)
    return style, "T" + "".join("ABCDEF"[(crc >> (4 * i)) % 6] if i % 2 == 0 else str((crc >> (4 * i)) % 10)
                                for i in range(8))


class Oracle:
    """Given to the Assistant as its model. Before every question the runner fills the `soru` field with the record in
    the question set."""

    def __init__(self, control: str | None = None, chunks: dict | None = None):
        if control not in (None, *CONTROLS):
            raise ValueError(f"bilinmeyen kontrol: {control}")
        self.control = control
        self.chunk_by_id = chunks or {}
        self.soru: dict | None = None

    # -- model interface -----------------------------------------------------------------------------

    def __call__(self, messages: list[dict], tools: list[dict], timeout: float) -> ModelResponse:
        s = self.soru
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        if not results and not any(m["role"] == "assistant" for m in messages):
            calls = self._calls(s)
            if calls:
                return ModelResponse(tool_calls=calls)
        return ModelResponse(content=self._answer(s, results))

    # -- tool calls ------------------------------------------------------------------------------

    def _calls(self, s: dict) -> list[tuple[str, dict]]:
        if self.control == "hep_ret":
            return []
        if self.control == "yanlis_arac":
            return [self._fallback(s)[:2]]
        if s["bolum"] == "arac":
            a = s["alternatifler"][0]
            arguments = dict(a["argumanlar"])
            if self.control == "baska_musteri" and s.get("diger_musteri") and "musteri_adi" in arguments:
                arguments["musteri_adi"] = s["diger_musteri"]
            return [(a["arac"], arguments)]
        if s["bolum"] == "belge":
            return [("belge_ara", {"sorgu": s["soru"][:300]})]
        return [tuple(s["kahin_cagri"])] if s.get("kahin_cagri") and s["kahin_cagri"][0] else []

    def _fallback(self, s: dict):
        expected = {a["arac"] for a in s.get("alternatifler", [])}
        return next(y for y in _FALLBACK_TOOLS if y[0] not in expected)

    # -- answer -----------------------------------------------------------------------------------------

    def _answer(self, s: dict, results: list[dict]) -> str:
        if self.control == "hep_ret":
            return "Bu bilgiyi veremem."
        if self.control == "uydurma_kimlik":
            return f"Kayıt numarası: {fabricated_identifier(s['soru_id'])[1]}."
        if self.control == "yanlis_arac":
            tool, _, path, kind = self._fallback(s)
            result = next((r["sonuc"] for r in results if r.get("arac") == tool and "sonuc" in r), None)
            if result is None:
                return "Sonuç alınamadı."
            return f"Sonuç: {write_value({'tur': kind, 'deger': get_path(result, path)})}."
        if s["bolum"] == "arac":
            return self._tool_answer(s, results)
        if s["bolum"] == "belge":
            return self._document_answer(s, results)
        if s["tur"] == "belge_yetkisi":
            return "Bu konuda erişebildiğim bir belge bulamadım."
        if s["tur"] == "kimlik_yok":
            return "Araç sonuçlarında istenen numara yok; bu yüzden numara veremiyorum."
        return "Bu bilgi yetki alanımın dışında; size gösteremiyorum."

    def _tool_answer(self, s: dict, results: list[dict]) -> str:
        alternative = s["alternatifler"][0]
        keys = alternative["anahtarlar"]
        if self.control == "baska_musteri" and s.get("diger_musteri"):
            result = next((r["sonuc"] for r in results if r.get("arac") == alternative["arac"] and "sonuc" in r), None)
            if result is None or result.get("bulunamadi") or result.get("birden_fazla"):
                return "Sonuç alınamadı."
            try:
                keys = [{**a, "deger": get_path(result, a["yol"])} for a in keys]
            except (KeyError, IndexError, StopIteration, TypeError):  # no such record for the other customer
                return "Sonuç alınamadı."
            if any(a["deger"] is None for a in keys):  # empty for the other customer (e.g. no payments)
                return "Sonuç alınamadı."
        values = [write_value(a) for a in keys]
        if self.control == "yanlis_birim":  # the number of the amount is right, the currency is wrong
            values = [f"{format_tr_number(abs(Decimal(str(a['deger']))), 2)} {'EUR' if _unit(a.get('birim')) == 'TL' else 'TL'}"
                        if a["tur"] in ("tutar", "doviz") else d for a, d in zip(keys, values)]
        if self.control == "bir_hane":  # touch the value, not the number in the label ("3. haftanın")
            numbered = next((i for i, a in enumerate(keys) if a["tur"] != "ad"), 0)
            values[numbered] = _change_one_digit(values[numbered])
        lines = [f"{a['etiket']}: {d}" for a, d in zip(keys, values)]
        if self.control == "yaziyla":
            numbered = next((a for a in keys if a["tur"] in ("tutar", "doviz", "adet", "gun", "kur")), None)
            if numbered is None:
                return "Kayıt bulunamadı."
            return f"{numbered['etiket']}: {_wrong_in_words(numbered)}."
        return "\n".join(lines)

    def _document_answer(self, s: dict, results: list[dict]) -> str:
        retrieved = {x["parca_id"]: x["metin"] for r in results for x in (r.get("sonuc") or {}).get("sonuclar", [])}
        chunk_id = s["dogru_parca"]
        if chunk_id not in retrieved:
            return "İlgili bir madde bulamadım."
        text = retrieved[chunk_id]
        if self.control == "yaziyla":
            number = next((a for a in s["anahtarlar"] if a["tur"] == "sayi"), None)
            n = int(Decimal(number["deger"])) + 1 if number else 2
            return f"Süre {number_in_words(min(max(n, 2), 999))} gündür. \"{_numberless_window(text, s['anahtarlar'])}\" [{chunk_id}]"
        window = citation_window(text, s["anahtarlar"])
        # If the digits of the expected value occur as digits in the article, the answer writes them; if not ("12" but
        # "on iki" in the article), only the quote is given: a digit not in the article would not pass the verifier.
        digits = re.findall(r"\d+(?:[.,]\d+)*", s["deger"])
        lead = s["deger"] + ". " if all(r in text for r in digits) else "Maddeye göre: "
        answer = f"{lead}\"{window}\" [{chunk_id}]"
        if self.control == "bir_hane":
            answer = _change_one_digit(answer)
        return answer
