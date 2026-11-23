"""Phase 10: stores every document as a PDF, so that a citation in a demo answer opens the document at the cited article.

- Laws: the original mevzuat.gov.tr PDF (data/ham/mevzuat) is copied with only a title added to its metadata. The page
  of every article is found by the same parse that split the law into articles (src/sayman/documents/legislation.py).
- Policies and contracts (not PDF): each one is rendered to HTML and printed to PDF with the locally installed Edge
  (or Chrome) in headless mode; nothing is downloaded. Article headings become PDF bookmarks, and the page of every
  article is read back from them.
- Every PDF is named after the document's readable name ("Çek Kabul Politikası.pdf"); `belgeler.json` maps each
  document id to its name, file and article pages.

Every article's page is checked against the page text before `belgeler.json` is written. Running the script again
rewrites all PDFs and removes PDFs that are no longer listed.

Input:  data/belge/belgeler.jsonl (scripts/phase05_generate_documents.py), data/ham/mevzuat/<kanun_no>.pdf
Output: data/belge/pdf/<okunaklı ad>.pdf, data/belge/pdf/belgeler.json

Usage (from the project root):
    .venv\\Scripts\\python -I scripts\\phase10_document_pdfs.py
    .venv\\Scripts\\python -I scripts\\phase10_document_pdfs.py --tarayici "C:\\...\\msedge.exe"
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from sayman.documents import legislation, library
from sayman.documents.pdf_export import (article_key, copy_with_title, display_names, export_document, file_name,
                                         find_browser, misplaced_articles, new_work_dir, write_index)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "belge" / "belgeler.jsonl"
LAW_DIR = ROOT / "data" / "ham" / "mevzuat"
OUTPUT = ROOT / "data" / "belge" / "pdf"
OLD_PAGE_MAP = "sayfalar.json"   # the index before readable names


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
    names = display_names(documents)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    index, wrong, copied = {}, [], 0
    with new_work_dir() as work:
        for i, doc in enumerate(documents, 1):
            keys = [article_key(m.parca_id) for m in doc.maddeler]
            pdf_path = OUTPUT / file_name(names[doc.belge_id])
            original = legislation.law_pdf(LAW_DIR, doc.belge_id) if doc.tur == "mevzuat" else None
            if original is not None:
                copy_with_title(original, pdf_path, names[doc.belge_id])
                pages = legislation.article_pages(original, doc.belge_id)
                if list(pages) != keys:
                    print(f"{doc.belge_id}: orijinal PDF'teki maddeler belge kütüphanesiyle aynı değil.")
                    return 1
                copied += 1
                source = "orijinal PDF"
            else:
                pages = export_document(doc, names[doc.belge_id], pdf_path, browser, Path(work))
                source = "üretildi"
            index[doc.belge_id] = {"ad": names[doc.belge_id], "dosya": pdf_path.name, "sayfalar": pages}
            wrong += misplaced_articles(pdf_path, doc, pages)
            print(f"  [{i}/{len(documents)}] {pdf_path.name} ({source}): {len(doc.maddeler)} madde, "
                  f"{max(pages.values(), default=0)}. sayfaya kadar ({time.perf_counter() - start:.0f} s)")
    articles = sum(len(d.maddeler) for d in documents)
    if wrong:
        print(f"Kontrol başarısız: {len(wrong)} madde eşlenen sayfada görünmüyor: {', '.join(wrong[:10])}")
        return 1
    path = write_index(index, OUTPUT)
    listed = {entry["dosya"] for entry in index.values()}
    stale = [f for f in OUTPUT.glob("*.pdf") if f.name not in listed] + [f for f in [OUTPUT / OLD_PAGE_MAP] if f.exists()]
    for f in stale:
        f.unlink()
    print(f"Kontrol: {articles} maddenin hepsi eşlenen sayfada görünüyor.")
    print(f"Yazıldı: {OUTPUT} ({len(documents)} PDF, {copied} tanesi orijinal kanun PDF'i) ve {path.name}; "
          f"listede olmayan {len(stale)} eski dosya silindi ({time.perf_counter() - start:.0f} s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
