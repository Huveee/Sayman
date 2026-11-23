"""Phase 10: stores every document as a PDF, so that a citation in a demo answer opens the document at the cited article.

- Laws: the original mevzuat.gov.tr PDF (data/ham/mevzuat) is copied as it is. The page of every article is found by
  the same parse that split the law into articles (src/sayman/documents/legislation.py).
- Policies and contracts (not PDF): each one is rendered to HTML and printed to PDF with the locally installed Edge
  (or Chrome) in headless mode; nothing is downloaded. Article headings become PDF bookmarks, and the page of every
  article is read back from them.

Every article's page is checked against the page text before `sayfalar.json` is written. Running the script again
rewrites all PDFs.

Input:  data/belge/belgeler.jsonl (scripts/phase05_generate_documents.py), data/ham/mevzuat/<kanun_no>.pdf
Output: data/belge/pdf/<belge_id>.pdf, data/belge/pdf/sayfalar.json

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase10_document_pdfs.py
    .venv\\Scripts\\python -I scripts\\phase10_document_pdfs.py --tarayici "C:\\...\\msedge.exe"
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

from sayman.documents import legislation, library
from sayman.documents.pdf_export import (article_key, export_document, find_browser, misplaced_articles, new_work_dir,
                                         write_page_map)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "belge" / "belgeler.jsonl"
LAW_DIR = ROOT / "data" / "ham" / "mevzuat"
OUTPUT = ROOT / "data" / "belge" / "pdf"


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    p = argparse.ArgumentParser(description="Belgeleri PDF olarak saklar (demo kaynak bağlantıları için).")
    p.add_argument("--tarayici", help="Edge ya da Chrome'un yolu (verilmezse bilinen yerlerde aranır)")
    arg = p.parse_args(argv)
    browser = Path(arg.tarayici) if arg.tarayici else find_browser()
    if browser is None or not browser.is_file():
        print("Edge ya da Chrome bulunamadı. Yolunu --tarayici ile verin.")
        return 1
    if not SOURCE.is_file():
        print(f"Belge dosyası yok: {SOURCE}. Önce scripts\\phase05_generate_documents.py çalıştırılmalı.")
        return 1
    documents = library.read_documents(SOURCE)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    page_map, wrong, copied = {}, [], 0
    with new_work_dir() as work:
        for i, doc in enumerate(documents, 1):
            keys = [article_key(m.parca_id) for m in doc.maddeler]
            original = legislation.law_pdf(LAW_DIR, doc.belge_id) if doc.tur == "mevzuat" else None
            if original is not None:
                shutil.copyfile(original, OUTPUT / f"{doc.belge_id}.pdf")
                pages = legislation.article_pages(original, doc.belge_id)
                if list(pages) != keys:
                    print(f"{doc.belge_id}: orijinal PDF'teki maddeler belge kütüphanesiyle aynı değil.")
                    return 1
                copied += 1
                source = "orijinal PDF"
            else:
                pages = export_document(doc, OUTPUT, browser, Path(work))
                source = "üretildi"
            page_map[doc.belge_id] = pages
            wrong += misplaced_articles(OUTPUT / f"{doc.belge_id}.pdf", doc, pages)
            print(f"  [{i}/{len(documents)}] {doc.belge_id} ({source}): {len(doc.maddeler)} madde, "
                  f"{max(pages.values(), default=0)}. sayfaya kadar ({time.perf_counter() - start:.0f} s)")
    articles = sum(len(d.maddeler) for d in documents)
    if wrong:
        print(f"Kontrol başarısız: {len(wrong)} madde eşlenen sayfada görünmüyor: {', '.join(wrong[:10])}")
        return 1
    path = write_page_map(page_map, OUTPUT)
    print(f"Kontrol: {articles} maddenin hepsi eşlenen sayfada görünüyor.")
    print(f"Yazıldı: {OUTPUT} ({len(documents)} PDF, {copied} tanesi orijinal kanun PDF'i) ve {path.name} "
          f"({time.perf_counter() - start:.0f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
