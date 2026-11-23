"""Documents as PDF files: the demo turns each citation in an answer into a link that opens the document's PDF at the
cited article.

The laws keep their original mevzuat.gov.tr PDFs (see scripts/phase10_document_pdfs.py). The policies and contracts
exist only as article lists in `data/belge/belgeler.jsonl`: each one is rendered to HTML and printed to PDF by a locally
installed Chromium browser (Edge or Chrome, headless); nothing is downloaded. Article headings become PDF bookmarks,
and the page of every article is read back from those bookmarks into `sayfalar.json` next to the PDFs.
"""
from __future__ import annotations

import html
import json
import re
import subprocess
import tempfile
from pathlib import Path

from pypdf import PdfReader

from sayman.documents.synthetic import Document

PAGE_MAP_FILE = "sayfalar.json"
DOCUMENT_ID = re.compile(r"[A-Za-z0-9_\-]+")   # the document part of a citation, as in documents/citation.py
ARTICLE_KEY = re.compile(r"m\d+")
BROWSER_CANDIDATES = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)
TYPE_NAMES = {"tedarik_sozlesmesi": "Tedarik sözleşmesi", "satis_sozlesmesi": "Satış sözleşmesi",
              "hizmet_sozlesmesi": "Hizmet sözleşmesi", "politika": "Şirket politikası", "mevzuat": "Mevzuat"}
STYLE = """
@page { size: A4; margin: 18mm 16mm; }
body { font-family: "Segoe UI", Calibri, Arial, sans-serif; font-size: 10.5pt; line-height: 1.45; color: #1f2733; }
h1 { font-size: 17pt; margin: 0 0 4pt; color: #1d3f66; }
.ust { color: #5b6573; font-size: 9pt; margin: 0 0 14pt; padding-bottom: 8pt; border-bottom: 1px solid #c9d3df; }
h2 { font-size: 11.5pt; margin: 14pt 0 4pt; color: #1d3f66; break-after: avoid; }
p { margin: 0 0 5pt; text-align: justify; }
"""


def article_key(parca_id: str) -> str:
    """"POL-CEK#m2" -> "m2"."""
    return parca_id.split("#", 1)[1]


def find_browser() -> Path | None:
    return next((Path(c) for c in BROWSER_CANDIDATES if Path(c).is_file()), None)


def document_html(doc: Document) -> str:
    e = html.escape
    meta = [doc.belge_id, TYPE_NAMES.get(doc.tur, doc.tur)] + [x for x in (doc.taraf_ad, doc.tarih) if x]
    parts = ['<!doctype html><html lang="tr"><head><meta charset="utf-8">', f"<title>{e(doc.baslik)}</title>",
             f"<style>{STYLE}</style></head><body>", f"<h1>{e(doc.baslik)}</h1>",
             f'<p class="ust">{" · ".join(e(m) for m in meta)}</p>']
    for m in doc.maddeler:
        heading = f"Madde {m.no}" + (f" – {m.baslik}" if m.baslik else "")
        parts.append(f'<h2 id="{e(article_key(m.parca_id))}">{e(heading)}</h2>')
        parts += [f"<p>{e(line)}</p>" for line in m.metin.split("\n") if line.strip()]
    parts.append("</body></html>")
    return "\n".join(parts)


def print_pdf(browser: Path, html_path: Path, pdf_path: Path, profile_dir: Path, timeout_s: int = 600) -> None:
    """Prints one HTML file to PDF with headless Chromium. Headings (h1, h2) become the PDF's bookmarks."""
    pdf_path.unlink(missing_ok=True)
    subprocess.run([str(browser), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    "--generate-pdf-document-outline", f"--user-data-dir={profile_dir}",
                    f"--print-to-pdf={pdf_path}", html_path.resolve().as_uri()],
                   capture_output=True, timeout=timeout_s, check=False)
    if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
        raise RuntimeError(f"PDF üretilemedi: {pdf_path.name}")


def article_pages(pdf_path: Path, keys: list[str]) -> dict[str, int]:
    """The 1-based page of each article, read from the bookmarks: the document title is the only top-level bookmark
    and the article headings are its children, in document order."""
    reader = PdfReader(pdf_path)
    children = next((o for o in reader.outline if isinstance(o, list)), [])
    articles = [o for o in children if not isinstance(o, list)]
    if len(articles) != len(keys):
        raise ValueError(f"{pdf_path.name}: {len(keys)} madde var ama {len(articles)} yer imi bulundu")
    return {k: reader.get_destination_page_number(o) + 1 for k, o in zip(keys, articles)}


def misplaced_articles(pdf_path: Path, doc: Document, pages: dict[str, int]) -> list[str]:
    """Articles whose mapped page does not show their heading (a check on the bookmark mapping)."""
    reader = PdfReader(pdf_path)
    texts: dict[int, str] = {}
    wrong = []
    for m in doc.maddeler:
        page = pages[article_key(m.parca_id)]
        if page not in texts:
            texts[page] = reader.pages[page - 1].extract_text() or ""
        if not re.search(rf"madde\s*{m.no}(?!\d)", texts[page], re.IGNORECASE):  # "Madde 5 –", original "MADDE 5-"
            wrong.append(m.parca_id)
    return wrong


def export_document(doc: Document, out_dir: Path, browser: Path, work_dir: Path) -> dict[str, int]:
    html_path = work_dir / f"{doc.belge_id}.html"
    html_path.write_text(document_html(doc), encoding="utf-8")
    pdf_path = out_dir / f"{doc.belge_id}.pdf"
    print_pdf(browser, html_path, pdf_path, work_dir / "profil")
    return article_pages(pdf_path, [article_key(m.parca_id) for m in doc.maddeler])


def write_page_map(page_map: dict[str, dict[str, int]], out_dir: Path) -> Path:
    path = out_dir / PAGE_MAP_FILE
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(page_map, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    temporary.replace(path)
    return path


def read_page_map(out_dir: Path) -> dict[str, dict[str, int]]:
    path = out_dir / PAGE_MAP_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def new_work_dir() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(prefix="sayman_pdf_")
