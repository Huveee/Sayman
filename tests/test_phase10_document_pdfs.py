"""Phase 10: documents as PDF files and the citation links in demo answers.

The PDFs here are built with pypdf (blank pages and bookmarks), so the tests need no browser. The real PDFs are
written by scripts/phase10_document_pdfs.py, which checks every article's page against the page text.
"""
import datetime as dt
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader, PdfWriter
from test_phase02_warehouse import AS_OF, _hand_built_tables
from test_phase06_document_tool import CHUNKS, EXPORT, DOMESTIC, CFO
from test_phase10_demo import _login, _make_demo

from sayman.warehouse.loader import build_warehouse
from sayman.demo.app import build_app
from sayman.documents.legislation import article_pages_from_texts, split_articles
from sayman.documents.pdf_export import (article_pages, copy_with_title, display_names, document_html, file_name,
                                         write_index)
from sayman.documents.search import Searcher
from sayman.documents.synthetic import Article, Document
from sayman.data.table_io import write_csv
from sayman.data.clock import Clock


def _pdf(path, pages: int, bookmarks: list[tuple[str, int]]):
    """A PDF with blank pages; bookmarks: (title, 0-based page) under one top-level document bookmark."""
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(595, 842)
    top = w.add_outline_item("Belge", 0)
    for title, page in bookmarks:
        w.add_outline_item(title, page, parent=top)
    with open(path, "wb") as f:
        w.write(f)


def _document(articles, document_id="POL-DENEME", title="Deneme <Politika> & Kural", party=None, short=None):
    return Document(document_id, "politika", title, None, party, short, ("cfo",), "2024-01-02",
                    tuple(Article(f"{document_id}#m{n}", document_id, n, t, text, None, None)
                          for n, t, text in articles))


# ------------------------------------------------------------------ readable names


def test_display_name_is_the_title_and_for_a_contract_the_party():
    docs = [_document([], "POL-CEK", "Çek Kabul Politikası"),
            _document([], "SOZ-M1", "Satış Çerçeve Sözleşmesi", "Kale Hilal Kırtasiye San. ve Tic. Ltd. Şti.",
                      "Kale Hilal Kırtasiye")]
    assert display_names(docs) == {"POL-CEK": "Çek Kabul Politikası",
                                   "SOZ-M1": "Satış Çerçeve Sözleşmesi – Kale Hilal Kırtasiye"}


def test_two_companies_with_one_short_name_get_their_full_names():
    docs = [_document([], "SOZ-S002", "Mal Alım Çerçeve Sözleşmesi", "Doğa Tedarik A.Ş.", "Doğa Tedarik"),
            _document([], "SOZ-S033", "Mal Alım Çerçeve Sözleşmesi", "Doğa Tedarik Ltd. Şti.", "Doğa Tedarik"),
            _document([], "SOZ-S001", "Mal Alım Çerçeve Sözleşmesi", "Bereket İmalat A.Ş.", "Bereket İmalat")]
    assert display_names(docs) == {"SOZ-S002": "Mal Alım Çerçeve Sözleşmesi – Doğa Tedarik A.Ş.",
                                   "SOZ-S033": "Mal Alım Çerçeve Sözleşmesi – Doğa Tedarik Ltd. Şti.",
                                   "SOZ-S001": "Mal Alım Çerçeve Sözleşmesi – Bereket İmalat"}


def test_same_name_twice_is_refused():
    with pytest.raises(ValueError, match="Aynı ada düşen"):
        display_names([_document([], "POL-A", "Politika"), _document([], "POL-B", "Politika")])


def test_names_that_share_a_file_name_are_refused():
    # Different names, same file on Windows: forbidden characters become "-" and letter case is ignored
    with pytest.raises(ValueError, match="Aynı dosya adına düşen"):
        display_names([_document([], "POL-A", "Gider/Avans"), _document([], "POL-B", "Gider:Avans")])
    with pytest.raises(ValueError, match="Aynı dosya adına düşen"):
        display_names([_document([], "POL-A", "Çek Kanunu"), _document([], "POL-B", "ÇEK KANUNU")])
    # "ı" and "İ" are different letters on Windows too, so these are two files
    assert len(display_names([_document([], "POL-A", "Kur Politikası"), _document([], "POL-B", "KUR POLİTİKASI")])) == 2


@pytest.mark.parametrize("name, expected", [
    ("Çek Kabul Politikası", "Çek Kabul Politikası.pdf"),
    ("Mal Alım Çerçeve Sözleşmesi – Doğa Tedarik A.Ş.", "Mal Alım Çerçeve Sözleşmesi – Doğa Tedarik A.Ş.pdf"),
    ('Kira: "Merkez/Depo" <2024>?', "Kira- -Merkez-Depo- -2024--.pdf"),
])
def test_file_name_is_windows_safe(name, expected):
    assert file_name(name) == expected


# ------------------------------------------------------------------ PDF content and page map


def test_document_html_escapes_text_and_has_every_article():
    doc = _document([(1, "Amaç", "Bu politika <b>çekleri</b> düzenler.\nİkinci satır & devamı."), (2, "Vade", "120 gün.")])
    page = document_html(doc, "Deneme Politikası")
    assert "<b>" not in page and "&lt;b&gt;çekleri&lt;/b&gt;" in page and "İkinci satır &amp; devamı." in page
    assert "<title>Deneme Politikası</title>" in page and "<h1>Deneme &lt;Politika&gt; &amp; Kural</h1>" in page
    assert '<h2 id="m1">Madde 1 – Amaç</h2>' in page and '<h2 id="m2">Madde 2 – Vade</h2>' in page
    assert "POL-DENEME · Şirket politikası · 2024-01-02" in page


def test_article_pages_come_from_the_bookmarks_one_based(tmp_path):
    _pdf(tmp_path / "a.pdf", 4, [("Madde 1", 0), ("Madde 2", 0), ("Madde 3", 2)])
    assert article_pages(tmp_path / "a.pdf", ["m1", "m2", "m3"]) == {"m1": 1, "m2": 1, "m3": 3}


def test_law_pages_follow_the_article_parse():
    """Laws keep their original PDF: the page comes from the same parse that split the law into articles, so
    additional, temporary and repealed articles are skipped there too."""
    pages = ["ÇEK KANUNU\nAmaç\nMADDE 1 – (1) Bu Kanunun amacı çeklerin güvenilirliğini artırmaktır.\n",
             "Tanımlar\nMADDE 2 – (1) Bu Kanunda geçen terimler aşağıda tanımlanmıştır.\n"
             "EK MADDE 1 – (1) Ek madde metni de yeterince uzun bir metindir.\n",
             "GEÇİCİ MADDE 2 – (1) Geçici madde metni yeterince uzun bir metin.\nMADDE 3 – (Mülga: 1/1/2012)\n"
             "MADDE 4 – (1) Dördüncü maddenin metni yeterince uzundur.\n"]
    assert article_pages_from_texts(pages, "KANUN-5941") == {"m1": 1, "m2": 2, "m4": 3}
    assert [a.no for a in split_articles("\n".join(pages), "5941")] == [1, 2, 4]


def test_law_copy_gets_only_a_title(tmp_path):
    _pdf(tmp_path / "kanun.pdf", 3, [("MADDE 1", 0), ("MADDE 2", 2)])
    copy_with_title(tmp_path / "kanun.pdf", tmp_path / "kopya.pdf", "Çek Kanunu")
    original, copy = PdfReader(tmp_path / "kanun.pdf"), PdfReader(tmp_path / "kopya.pdf")
    assert copy.metadata.title == "Çek Kanunu" and len(copy.pages) == len(original.pages) == 3
    assert article_pages(tmp_path / "kopya.pdf", ["m1", "m2"]) == {"m1": 1, "m2": 3}


def test_article_pages_refuse_a_count_mismatch(tmp_path):
    _pdf(tmp_path / "a.pdf", 2, [("Madde 1", 0)])
    with pytest.raises(ValueError, match="2 madde var ama 1 yer imi"):
        article_pages(tmp_path / "a.pdf", ["m1", "m2"])


# ------------------------------------------------------------------ links in the demo

NAMES = {"POL-CEK": "Çek Kabul Politikası", "POL-NAKIT": "Nakit Yönetimi Politikası",
         "SOZ-M1": "Satış Çerçeve Sözleşmesi – Alfa", "SOZ-M2": "Satış Çerçeve Sözleşmesi – Beta GmbH"}
PAGES = {"POL-CEK": {"m2": 2}, "POL-NAKIT": {"m2": 1}, "SOZ-M1": {"m3": 3}, "SOZ-M2": {"m3": 1}}


@pytest.fixture(scope="module")
def pdf_demo(tmp_path_factory):
    folder = tmp_path_factory.mktemp("faz10_pdf")
    tables = _hand_built_tables()
    write_csv(tables, folder / "csv")
    build_warehouse(folder / "csv", folder / "sayman.duckdb").close()
    pdfs = folder / "pdf"
    pdfs.mkdir()
    index = {}
    for document_id, name in NAMES.items():
        _pdf(pdfs / file_name(name), 3, [("Madde", 0)])
        index[document_id] = {"ad": name, "dosya": file_name(name), "sayfalar": PAGES[document_id]}
    write_index(index, pdfs)
    demo = _make_demo(folder, folder / "sayman.duckdb", Clock(AS_OF, dt.date(2025, 6, 30)), tables,
                      belge=Searcher(CHUNKS), document_pdfs=pdfs)
    yield demo, build_app(demo)
    demo.close()


def test_citation_link_redirects_to_the_article_page(pdf_demo):
    _, app = pdf_demo
    c = _login(app, CFO)
    r = c.get("/belge/POL-CEK/m2", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/belge/POL-CEK.pdf#page=2"
    assert r.headers["cache-control"] == "no-store"


def test_pdf_opens_inline_under_its_readable_name_and_is_not_cached(pdf_demo):
    _, app = pdf_demo
    r = _login(app, CFO).get("/belge/POL-NAKIT.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == (
        "inline; filename=\"POL-NAKIT.pdf\"; filename*=UTF-8''" + quote("Nakit Yönetimi Politikası.pdf"))
    assert r.headers["cache-control"] == "no-store" and r.content.startswith(b"%PDF")


@pytest.mark.parametrize("user, visible", [
    (CFO, {"POL-CEK", "POL-NAKIT", "SOZ-M1", "SOZ-M2"}),
    (DOMESTIC, {"POL-CEK", "SOZ-M1"}),
    (EXPORT, {"POL-CEK", "SOZ-M2"}),
])
def test_names_list_only_the_documents_the_role_may_open(pdf_demo, user, visible):
    _, app = pdf_demo
    r = _login(app, user).get("/belge/adlar")
    assert r.status_code == 200 and r.json() == {"adlar": {i: NAMES[i] for i in visible}}


@pytest.mark.parametrize("user, document_id, visible", [
    (CFO, "SOZ-M2", True), (DOMESTIC, "SOZ-M1", True), (DOMESTIC, "SOZ-M2", False), (DOMESTIC, "POL-NAKIT", False),
    (EXPORT, "SOZ-M2", True), (EXPORT, "SOZ-M1", False), (EXPORT, "POL-CEK", True),
])
def test_pdf_follows_the_document_roles(pdf_demo, user, document_id, visible):
    _, app = pdf_demo
    c = _login(app, user)
    assert c.get(f"/belge/{document_id}.pdf").status_code == (200 if visible else 404)
    assert c.get(f"/belge/{document_id}/m{2 if document_id.startswith('POL') else 3}",
                 follow_redirects=False).status_code == (302 if visible else 404)


def test_unknown_and_forbidden_documents_look_the_same(pdf_demo):
    _, app = pdf_demo
    c = _login(app, DOMESTIC)
    unknown, forbidden = c.get("/belge/SOZ-YOK.pdf"), c.get("/belge/SOZ-M2.pdf")
    assert unknown.status_code == forbidden.status_code == 404 and unknown.json() == forbidden.json()


@pytest.mark.parametrize("path", ["/belge/POL-CEK/m9", "/belge/POL-CEK/madde2", "/belge/..%2Fbelgeler.pdf",
                                  "/belge/POL-CEK.json", "/belge/belgeler.pdf", "/belge/KANUN-6102.pdf",
                                  "/belge/" + quote("Çek Kabul Politikası.pdf")])
def test_other_paths_are_not_served(pdf_demo, path):
    _, app = pdf_demo
    assert _login(app, CFO).get(path, follow_redirects=False).status_code == 404


@pytest.mark.parametrize("path", ["/", "/static/app.js", "/static/style.css"])
def test_page_files_are_revalidated(pdf_demo, path):
    """Found in the demo: the browser kept a stale app.js after the update, so citations were not links."""
    _, app = pdf_demo
    r = TestClient(app).get(path)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("path", ["/belge/POL-CEK.pdf", "/belge/POL-CEK/m2", "/belge/adlar"])
def test_pdf_and_names_need_a_session(pdf_demo, path):
    _, app = pdf_demo
    assert TestClient(app).get(path, follow_redirects=False).status_code == 401
