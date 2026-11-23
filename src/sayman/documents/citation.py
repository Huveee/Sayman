"""Citation check: does a quote the model took from a document really occur in the chunk it points to?

In its answer the model gives the quote in quotation marks, immediately followed by the chunk identifier in square
brackets:

    Vade 30 gündür: "fatura tarihinden itibaren 30 gün içinde banka havalesiyle öder" [SOZ-S001#m3]

Only three things are normalized before comparing: whitespace (runs of spaces and line breaks count as one space),
quotation marks and apostrophes (’ ‘ ” “ « » all become the plain mark) and letter case (by the Turkish rule: I→ı,
İ→i). Digits, punctuation and words are left alone: "%12" differs from "%1,2", and "30 gün" differs from "otuz gün".

The quote must occur at word and number boundaries: a match that starts or ends in the middle of a number or a word
does not count ("20 günü" does not occur in "120 günü").

If the quote contains "..." or "…" in the middle, the parts are searched separately and in the same order.

Citations that do not follow the format are also invalid: a chunk identifier given without a quoted quote
("alintisiz_atif"; quotes written with single quotes land here too, because in Turkish a single quote is confused with
an apostrophe) and a quoted quote that is not followed by a chunk identifier ("kimliksiz_alinti").

Known limit: this check verifies that the quote occurs in the text; it does not verify that the quote supports the
claim. A real but irrelevant quote passes.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

MIN_WORDS = 3
_QUOTE_MARKS = str.maketrans({"’": "'", "‘": "'", "‚": "'", "`": "'", "´": "'", "”": '"', "“": '"', "„": '"', "«": '"',
                         "»": '"'})
_CITATION = re.compile(r'["“”«»]([^"“”«»]+)["“”«»]\s*\[([A-Za-z0-9_\-]+#m\d+)\]')
_CHUNK_ID = re.compile(r"\[([A-Za-z0-9_\-]+#m\d+)\]")
_QUOTED = re.compile(r'["“”«»]([^"“”«»]+)["“”«»]')
_ELLIPSIS = re.compile(r"\s*(?:\.\.\.|…)\s*")


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text).translate(_QUOTE_MARKS)
    text = text.replace("I", "ı").replace("İ", "i").lower()
    return " ".join(text.split())


@dataclass(frozen=True)
class CitationResult:
    parca_id: str
    quote: str
    gecerli: bool
    reason: str                       # "tamam", "bilinmeyen_parca", "cok_kisa", "metinde_yok", "baska_parcada",
                                     # "alintisiz_atif", "kimliksiz_alinti"
    found_in: tuple[str, ...] = ()  # identifiers of the other chunks, if the quote occurs in them


def _segments(quote: str) -> list[str]:
    segments = [normalize_text(p).strip(" .,;:") for p in _ELLIPSIS.split(quote.strip().strip("\"'“”«»"))]
    return [p for p in segments if p]


def _on_boundaries(text: str, start: int, end: int) -> bool:
    """Does the match avoid starting or ending in the middle of a word or a number?

    The quote "20 günü" does not occur in "120 günü", "50.000 TL" does not occur in "250.000 TL", and "%2" does not
    occur in "%2,25"."""
    first, last = text[start], text[end - 1]
    if start > 0:
        before = text[start - 1]
        if (before.isalnum() and first.isalnum()) or (
                first.isdigit() and before in ".," and start > 1 and text[start - 2].isdigit()):
            return False
    if end < len(text):
        after = text[end]
        if (after.isalnum() and last.isalnum()) or (
                last.isdigit() and after in ".," and end + 1 < len(text) and text[end + 1].isdigit()):
            return False
    return True


def _occurs_in_order(segments: list[str], text: str) -> bool:
    """Do the segments occur in the text in this order, each at word and number boundaries?"""
    position = 0
    for p in segments:
        found = text.find(p, position)
        while found >= 0 and not _on_boundaries(text, found, found + len(p)):
            found = text.find(p, found + 1)
        if found < 0:
            return False
        position = found + len(p)
    return True


def check_citation(quote: str, chunk_id: str, texts: dict[str, str]) -> CitationResult:
    """texts: chunk identifier -> the article's own text (not the search text)."""
    if chunk_id not in texts:
        return CitationResult(chunk_id, quote, False, "bilinmeyen_parca")
    segments = _segments(quote)
    if sum(len(p.split()) for p in segments) < MIN_WORDS:
        return CitationResult(chunk_id, quote, False, "cok_kisa")
    if _occurs_in_order(segments, normalize_text(texts[chunk_id])):
        return CitationResult(chunk_id, quote, True, "tamam")
    elsewhere = tuple(sorted(other_id for other_id, m in texts.items() if other_id != chunk_id and _occurs_in_order(segments, normalize_text(m))))
    return CitationResult(chunk_id, quote, False, "baska_parcada" if elsewhere else "metinde_yok", elsewhere)


def quoted_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) positions of the quoted parts of the text; the number verifier does not look inside them."""
    return [(m.start(), m.end()) for m in _QUOTED.finditer(text)]


def citations(answer: str) -> list[tuple[str, str]]:
    """The (quote, chunk identifier) pairs in the answer, in order of occurrence."""
    return [(m.group(1), m.group(2)) for m in _CITATION.finditer(answer)]


def check_answer(answer: str, texts: dict[str, str]) -> list[CitationResult]:
    """One result for every citation and for every identifier or quoted quote that does not follow the format, in text
    order.

    An answer with no citation at all returns an empty list; whether an answer that must rest on a document lacks
    citations is for the caller to decide."""
    results, citation_starts, id_starts = [], set(), set()
    for m in _CITATION.finditer(answer):
        results.append((m.start(), check_citation(m.group(1), m.group(2), texts)))
        citation_starts.add(m.start())
        id_starts.add(m.start(2) - 1)
    for m in _CHUNK_ID.finditer(answer):
        if m.start() not in id_starts:
            results.append((m.start(), CitationResult(m.group(1), "", False, "alintisiz_atif")))
    for m in _QUOTED.finditer(answer):
        if m.start() not in citation_starts:
            results.append((m.start(), CitationResult("", m.group(1), False, "kimliksiz_alinti")))
    return [s for _, s in sorted(results, key=lambda x: x[0])]
