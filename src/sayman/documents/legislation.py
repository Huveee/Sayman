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


def split_articles(text: str, law_no: str) -> tuple[Article, ...]:
    document_id = f"KANUN-{law_no}"
    matches = list(_ARTICLE.finditer(text))
    title_starts = [_title_start(text, e.start()) for e in matches]
    articles, seen = [], set()
    for i, e in enumerate(matches):
        if e.group(1) or e.group(3):
            continue
        number = int(e.group(2))
        end = title_starts[i + 1][0] if i + 1 < len(matches) else len(text)
        body = " ".join(text[e.end():end].split())
        if number in seen or len(body) < 20 or body.startswith("(Mülga"):
            continue
        seen.add(number)
        articles.append(Article(f"{document_id}#m{number}", document_id, number, title_starts[i][1] or f"Madde {number}", body, None, None))
    return tuple(articles)


def laws(directory: Path) -> list[Document]:
    """Law documents from the files directory/<law_no>.pdf (those that exist, sorted by law number)."""
    documents = []
    for number, (name, date) in sorted(LAWS.items()):
        path = directory / f"{number}.pdf"
        if path.exists():
            documents.append(Document(f"KANUN-{number}", "mevzuat", name, None, None, None, ALL_ROLES, date,
                                  split_articles(pdf_text(path), number)))
    return documents
