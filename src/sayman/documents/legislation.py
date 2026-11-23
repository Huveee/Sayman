"""Splits Turkish laws (mevzuat.gov.tr PDFs) into articles; they are added to the library as distractor documents.

None of the questions in the question set asks about the laws. The laws are there to make search harder: the Cheque
Law (Çek Kanunu), the Code of Obligations (Türk Borçlar Kanunu) and the Commercial Code (Türk Ticaret Kanunu) have
articles on cheques, default interest, defective goods and termination, and these cover the same topics as the
company's policy and contract articles.

- Every "MADDE n –" heading is one chunk; the chunk identifier looks like "KANUN-6098#m5" (n is the article number in
  the law).
- Temporary articles, additional articles, articles inserted later ("MADDE 5/A") and repealed (mülga) articles are not
  taken.
- The short line right above an article (e.g. "b. Hazır olmayanlar arasında") is taken as the article title.
- Some words come out split in the text extracted from the PDF ("tari hi"); this is not corrected. The text of the
  laws is outside copyright (Law No. 5846, art. 31).
"""

from __future__ import annotations

import bisect
import re
from pathlib import Path

from sayman.documents.synthetic import ALL_ROLES, Document, Article

LAWS = {"5941": ("Çek Kanunu", "2009-12-14"), "6098": ("Türk Borçlar Kanunu", "2011-01-11"),
            "6102": ("Türk Ticaret Kanunu", "2011-01-13")}
_ARTICLE = re.compile(r"^[ \t]*(GEÇİCİ\s+|EK\s+)?MADDE\s*(\d+)(\s*/\s*[A-Z])?\s*[-–—]\s*", re.M)
MAX_TITLE_LENGTH = 120


def pdf_text(path: Path) -> str:
    from pypdf import PdfReader
    return "\n".join(s.extract_text() for s in PdfReader(path).pages)


def _title_start(text: str, article_start: int) -> tuple[int, str]:
    """Position where the article title starts, and the title. If there is no title: (article_start, "")."""
    before = text[:article_start].rstrip()
    line_start = before.rfind("\n") + 1
    line = before[line_start:].strip()
    if line and len(line) <= MAX_TITLE_LENGTH and not line.endswith((".", ":", ";", ",")) and "MADDE" not in line:
        return line_start, line
    return article_start, ""


def _taken_articles(text: str, document_id: str):
    """(position of the "MADDE n –" heading, Article) for every article taken from the text, in order."""
    matches = list(_ARTICLE.finditer(text))
    title_starts = [_title_start(text, e.start()) for e in matches]
    seen = set()
    for i, e in enumerate(matches):
        if e.group(1) or e.group(3):
            continue
        number = int(e.group(2))
        end = title_starts[i + 1][0] if i + 1 < len(matches) else len(text)
        body = " ".join(text[e.end():end].split())
        if number in seen or len(body) < 20 or body.startswith("(Mülga"):
            continue
        seen.add(number)
        yield e.start(), Article(f"{document_id}#m{number}", document_id, number, title_starts[i][1] or f"Madde {number}", body, None, None)


def split_articles(text: str, law_no: str) -> tuple[Article, ...]:
    return tuple(article for _, article in _taken_articles(text, f"KANUN-{law_no}"))


def article_pages_from_texts(page_texts: list[str], document_id: str) -> dict[str, int]:
    """The 1-based page of every article taken, found by the same parse over the same joined text as pdf_text."""
    starts, offset = [], 0
    for page in page_texts:
        starts.append(offset)
        offset += len(page) + 1
    return {a.parca_id.split("#", 1)[1]: bisect.bisect_right(starts, position)
            for position, a in _taken_articles("\n".join(page_texts), document_id)}


def law_pdf(directory: Path, document_id: str) -> Path | None:
    """The original mevzuat.gov.tr PDF of a law document, if it was downloaded."""
    number = document_id.removeprefix("KANUN-")
    path = directory / f"{number}.pdf"
    return path if document_id.startswith("KANUN-") and number in LAWS and path.is_file() else None


def article_pages(path: Path, document_id: str) -> dict[str, int]:
    from pypdf import PdfReader
    return article_pages_from_texts([s.extract_text() for s in PdfReader(path).pages], document_id)


def laws(directory: Path) -> list[Document]:
    """Law documents from the files directory/<law_no>.pdf (those that exist, sorted by law number)."""
    documents = []
    for number, (name, date) in sorted(LAWS.items()):
        path = directory / f"{number}.pdf"
        if path.exists():
            documents.append(Document(f"KANUN-{number}", "mevzuat", name, None, None, None, ALL_ROLES, date,
                                  split_articles(pdf_text(path), number)))
    return documents
