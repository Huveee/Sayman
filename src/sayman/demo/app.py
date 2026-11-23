"""Phase 10 demo: a fake ERP page, the Sayman chat window embedded in it, the back end that signs the identity and
the "fast-forward time" button. Running and resetting: scripts/phase10_demo.py.

Security:
- The browser sees neither the signing key nor the identity card. At login the back end creates a random session id
  (HttpOnly cookie); for every question and report the card is signed here for the user of the session. A user
  field in the request body is ignored. (There is no password in the demo: in a real ERP the ERP's own login opens
  the session.)
- Tool results pass through the Phase 3 gate: the role permission and the data scope are applied there.
- Alerts do not pass through the gate (monitoring runs on the whole company's tables). So they are filtered here:
  the CFO sees all of them; a sales manager sees only the alerts of customers in their own scope and does not see
  the alerts that have no customer (company level).
- The report is built from tools called through the gate with the card of the session's user; the model has no
  part in the report. The section of a tool the role cannot call does not appear in the report.
- A citation in an answer links to the document's PDF (/belge/<id>/<article> redirects to the page); the link text is
  the document's readable name from /belge/adlar. The PDF and the names are served only for documents whose roles
  pass the document tool's rule (belge_ara permission and the document's roles);
  an unknown and a forbidden document get the same 404, so a link cannot reveal that a contract exists.
- Demo state (clock copy, alert store, audit logs, scope copies) is kept in its own folder; the warehouse and
  data/saat.json do not change. The clock cannot be turned back; to start over the folder is deleted.
"""

from __future__ import annotations

import datetime as dt
import secrets
import threading
import time
from urllib.parse import quote
from decimal import Decimal
from pathlib import Path
from typing import Callable

from fastapi import Body, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles

from sayman.tools.audit_log import AuditLog
from sayman.tools.identity import generate_card
from sayman.tools.server import SCOPE_LABELS, SaymanServer
from sayman.tools.permissions import PermissionTable
from sayman.assistant.orchestrator import SYSTEM_PROMPT, Assistant, Settings, tool_definitions
from sayman.monitoring.scheduler import AlertStore, run_scheduler
from sayman.data.clock import Clock, read_clock, write_clock
from sayman.documents.pdf_export import ARTICLE_KEY, DOCUMENT_ID, read_index

STATIC_DIR = Path(__file__).with_name("static")
COOKIE_NAME = "sayman_oturum"
CARD_LIFETIME_S = 600          # a turn lasts at most 300 s (orchestrator.Settings.sure_s); the card must outlive the turn
MAX_QUESTION_LENGTH = 500
MAX_STEP_DAYS = 31
SHOWN_ALERTS = 20
MAX_SESSIONS = 50
DISPLAY_NAMES = {"selin.aydin": "Selin Aydın", "murat.kaya": "Murat Kaya", "deniz.arslan": "Deniz Arslan"}  # fictional
SAMPLE_QUESTIONS = {
    "cfo": ["Bankalarda toplam ne kadar nakit var?",
            "Önümüzdeki 7 günde tedarikçilere ne kadar ödeme yapacağız?",
            "Vadesi geçmiş alacaklarımızın toplamı ne kadar?",
            "Çek kabul politikamıza göre en fazla kaç gün vadeli çek alabiliriz?",
            "Çeki karşılıksız çıkan bir müşteriyle politikamıza göre nasıl çalışmalıyız?",
            "Nakit politikamıza göre asgari nakit eşiği nedir?"],
    "satis": ["Vadesi geçmiş alacaklarımızın toplamı ne kadar?",
              "Bekleyen çeklerimizin toplamı ne kadar?",
              "Bankalarda toplam ne kadar nakit var?"],
}
# In the live rehearsal (22-23 November 2026, sayman-ayarli) these questions got verified answers; the sales
# manager's cash question deliberately ends with "yetkiniz yok". The "asgari nakit eşiği" question had been stopped in
# the first rehearsal (the policy says "on iki ay", the verifier did not read numbers in words); after the verifier
# fix it was tried live and passed (README).
ALERT_FIELDS = ("uyari_id", "gun", "onem", "baslik", "aciklama", "musteri_id")


def scoped_description(rule: str | None, description: str, title: str) -> str:
    """The description shown to the sales manager: only the part that comes from that customer's own records."""
    if rule == "karsiliksiz_cek":       # "... karşılıksız çıktı; <the company's cheque threshold>."
        return description.split(";")[0].rstrip(".") + "."
    if rule == "musteri_gecikmesi":     # "<customer is one of the company's N largest customers>. <its own invoices>"
        marker = "müşterisinden biri. "  # the customer name may also contain ". " (Ltd. Şti.): look at the marker, not at the sentence
        return description.split(marker, 1)[1] if marker in description else title + "."
    return title + "."                  # unknown rule: no details are given


class DemoError(RuntimeError):
    """A refusal to show the user that does not change the state (e.g. the end of the data was reached)."""


def format_lira(value) -> str:
    """Tool format "75826754.65" -> "75.826.754,65 TL"."""
    d = Decimal(str(value))
    whole, fraction = f"{abs(d):.2f}".split(".")
    return ("-" if d < 0 else "") + f"{int(whole):,}".replace(",", ".") + "," + fraction + " TL"


def _cash(s):
    return format_lira(s["toplam_tl"]), [(f"{h['banka']} · {h['para_birimi']}", format_lira(h["bakiye_tl"])) for h in s["hesaplar"]]


def _payments(s):
    return format_lira(s["toplam_tl"]), [("Fatura sayısı", str(s["fatura_sayisi"])),
                                ("İçinde vadesi geçmiş", format_lira(s["vadesi_gecmis_tl"]))]


def _receivables(s):
    return format_lira(s["toplam_tl"]), ([("Vadesi geçmiş", format_lira(s["vadesi_gecmis_tl"])),
                                 ("Müşteri sayısı", str(s["musteri_sayisi"]))]
                                + [(a["musteri"], format_lira(a["toplam_tl"])) for a in s["en_buyuk_alacaklar"][:3]])


def _cheques(s):
    return format_lira(s["toplam_tl"]), ([("Çek sayısı", str(s["cek_sayisi"]))]
                                + [(f"{c['musteri']} · {c['vade_tarihi']}", format_lira(c["tutar_tl"])) for c in s["cekler"][:3]])


# (title, tool, arguments, result -> (summary, rows)); the sections of tools the role cannot call are skipped
REPORT_SECTIONS = [
    ("Nakit pozisyonu", "nakit_pozisyonu", {}, _cash),
    ("Önümüzdeki 7 günün ödemeleri", "yaklasan_odemeler", {"gun_sayisi": 7}, _payments),
    ("Açık alacaklar", "alacak_yaslandirma", {"en_fazla": 3}, _receivables),
    ("7 gün içinde vadesi gelen çekler", "cek_portfoyu", {"vade_gun": 7, "en_fazla": 3}, _cheques),
]


def ollama_warmer(model) -> Callable[[dt.date], None]:
    """Fills the model's cache with that day's system prompt and tool list. Since the system prompt starts with
    today's date, the cache is wasted every time the clock advances; if it is not warmed up, the next first question
    takes ~100 s."""
    def warmer(today: dt.date) -> None:
        model([{"role": "system", "content": SYSTEM_PROMPT.format(bugun=today.isoformat())},
               {"role": "user", "content": "Merhaba"}], tool_definitions(), 600)
    return warmer


class Demo:
    def __init__(self, *, warehouse: Path, state_dir: Path, initial_clock: Clock, key: bytes, permissions: PermissionTable,
                 tables: dict[str, list[dict]], model, model_name: str = "sahte", belge=None,
                 search_method: str = "bm25", step_days: int = 7, time_fn: Callable[[], float] = time.time,
                 warmer: Callable[[dt.date], None] | None = None, document_pdfs: Path | None = None):
        """tables: the tables the monitoring rules read (data/uretilen). warmer: if given, the model is warmed up in the
        background at start and every time the clock advances; if not given (fake model) it counts as ready.
        document_pdfs: the folder written by scripts/phase10_document_pdfs.py; citations in answers link there."""
        self.directory = Path(state_dir)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.clock_path = self.directory / "saat.json"
        self.clock = read_clock(self.clock_path) if self.clock_path.exists() else initial_clock
        self.key, self.permissions, self.tables, self.time_fn = key, permissions, tables, time_fn
        self.model_name, self.step_days, self.warmer = model_name, step_days, warmer
        self.store = AlertStore(self.directory / "izleme")
        status = self.store.status()
        if status and dt.date.fromisoformat(status["son_gun"]) > self.clock.simdi:
            # The previous run was interrupted after writing the store but before writing the clock: the clock catches up with the store (it cannot be turned back).
            self.clock = Clock(dt.date.fromisoformat(status["son_gun"]), self.clock.veri_bitis)
        write_clock(self.clock, self.clock_path)
        run_scheduler(tables, self.clock, self.store)  # alerts up to today go to the store; advancing brings only the new ones
        self.server = SaymanServer(warehouse=warehouse, clock=lambda: self.clock, key=key, permissions=permissions,
                                     audit_log=AuditLog(self.directory / "denetim.jsonl"),
                                     scope_dir=self.directory / "kapsam", time_fn=time_fn, belge=belge,
                                     search_method=search_method)
        # The alert filter is read from the same source the gate uses (musteriler.yurtici in the warehouse).
        self.domestic = {m: bool(y) for m, y in
                        self.server.connections["hepsi"].execute("SELECT musteri_id, yurtici FROM musteriler").fetchall()}
        self._state_lock = threading.RLock()  # alerts and the report are not read while the clock advances (half-written file, two dates)
        # The same DuckDB connections are used by both the chat turn and the report: gate calls are queued.
        self._db_lock = threading.RLock()
        gate = self.server.gate

        def serialized_gate(name, arguments, card):
            with self._db_lock:
                return gate(name, arguments, card)
        self.server.gate = serialized_gate
        self.assistant = Assistant(self.server, model, Settings(), audit_log=AuditLog(self.directory / "asistan.jsonl"))
        self.lock = threading.Lock()  # a model turn and advancing the clock do not run at the same time
        self.sessions: dict[str, str] = {}
        self.model_ready = warmer is None
        # Document PDFs: the roles come from the same chunks the document tool searches.
        self.document_roles = {c.belge_id: c.roller for c in belge.chunk_by_id.values()} if belge is not None else {}
        self.document_pdfs = Path(document_pdfs) if document_pdfs is not None else None
        self.document_index = read_index(self.document_pdfs) if self.document_pdfs is not None else {}
        self._warm_up()

    # -- identity ----------------------------------------------------------------------------------

    def profile(self, user: str) -> dict:
        role = self.permissions.users[user]
        r = self.permissions.roller[role]
        return {"kullanici": user, "ad": DISPLAY_NAMES.get(user, user), "rol": role, "rol_adi": r.name,
                "kapsam_adi": SCOPE_LABELS[r.scope]}

    def card(self, user: str) -> str:
        return generate_card(user, self.key, CARD_LIFETIME_S, self.time_fn())

    # -- document PDFs -----------------------------------------------------------------------------

    def can_open_document(self, user: str, document_id: str) -> bool:
        """The same rule as the document tool: the role may call belge_ara and is among the document's roles."""
        role = self.permissions.users[user]
        return "belge_ara" in self.permissions.roller[role].tools and role in self.document_roles.get(document_id, ())

    def document_names(self, user: str) -> dict[str, str]:
        """Readable names of the documents the user may open (the browser shows them as citation link text)."""
        return {i: e["ad"] for i, e in self.document_index.items() if self.can_open_document(user, i)}

    def document_pdf(self, user: str, document_id: str) -> tuple[Path, str] | None:
        """(PDF path, file name) if the user may open the document and its PDF exists."""
        if self.document_pdfs is None or not DOCUMENT_ID.fullmatch(document_id):
            return None
        entry = self.document_index.get(document_id)
        if entry is None or not self.can_open_document(user, document_id):
            return None
        path = self.document_pdfs / entry["dosya"]
        if path.parent != self.document_pdfs or not path.is_file():
            return None
        return path, entry["dosya"]

    def article_page(self, user: str, document_id: str, article: str) -> int | None:
        if not ARTICLE_KEY.fullmatch(article) or self.document_pdf(user, document_id) is None:
            return None
        return self.document_index[document_id]["sayfalar"].get(article)

    # -- alerts ------------------------------------------------------------------------------------

    def filter_alerts(self, user: str, alerts: list[dict]) -> list[dict]:
        """The alerts the user may see, with only the fields to be shown. From the description that goes to the sales
        manager the part computed from the whole company's data is removed (the company's cheque threshold for a
        bounced cheque, the customer's rank in the company for a delay)."""
        scope = self.permissions.roller[self.permissions.users[user]].scope
        result = [{k: u.get(k) for k in ALERT_FIELDS} | {"kural": u.get("kural")} for u in alerts]
        if scope != "hepsi":
            wanted_domestic = scope == "yurtici"
            result = [u for u in result if self.domestic.get(u["musteri_id"]) is wanted_domestic]
            for u in result:
                u["aciklama"] = scoped_description(u["kural"], u["aciklama"] or "", u["baslik"])
        for u in result:
            del u["kural"]
        return result

    def alerts(self, user: str) -> list[dict]:
        with self._state_lock:
            visible = self.filter_alerts(user, self.store.alerts())
        return sorted(visible, key=lambda u: (u["gun"], u["uyari_id"]), reverse=True)[:SHOWN_ALERTS]

    # -- report ------------------------------------------------------------------------------------

    def report(self, user: str) -> dict:
        with self._state_lock:
            return self._report(user)

    def _report(self, user: str) -> dict:
        role = self.permissions.users[user]
        card = self.card(user)
        sections = []
        for title, tool, arguments, formatter in REPORT_SECTIONS:
            if not self.permissions.is_allowed(role, tool):
                continue
            response, is_error = self.server.gate(tool, arguments, card)
            if is_error:
                sections.append({"baslik": title, "ozet": "—", "satirlar": [["Durum", response.get("mesaj") or "alınamadı"]]})
                continue
            summary, rows = formatter(response["sonuc"])
            sections.append({"baslik": title, "ozet": summary, "satirlar": [list(s) for s in rows]})
        return {"tarih": self.clock.simdi.isoformat(), **self.profile(user), "bolumler": sections}

    # -- clock and model ---------------------------------------------------------------------------

    def advance(self, days: int) -> dict:
        """Advances the clock and adds the alerts of the new days to the store. The caller must hold the lock."""
        if self.clock.simdi >= self.clock.veri_bitis:
            raise DemoError(f"Verinin sonuna gelindi ({self.clock.veri_bitis.isoformat()}). Baştan başlamak için "
                             "demoyu sıfırlayın: scripts/phase10_demo.py sifirla")
        previous, new_clock = self.clock, self.clock.advance(days)
        with self._state_lock:
            result = run_scheduler(self.tables, new_clock, self.store)  # monitoring first: if it fails, the clock does not advance
            write_clock(new_clock, self.clock_path)  # if it cannot be written, the clock catches up with the store at startup
            self.clock = new_clock
        self._warm_up()
        return {"onceki": previous.simdi.isoformat(), "bugun": new_clock.simdi.isoformat(), "yeni": result["yeni"]}

    def _warm_up(self) -> None:
        if self.warmer is None:
            return
        self.model_ready = False
        today = self.clock.simdi

        def work():
            try:
                self.warmer(today)
            except Exception:  # if warming up fails only the first question is slow
                pass
            finally:
                if self.clock.simdi == today:
                    self.model_ready = True
        threading.Thread(target=work, daemon=True).start()

    def close(self) -> None:
        if not getattr(self, "_kapandi", False):
            self.server.close()
            self._kapandi = True


def build_app(demo: Demo) -> FastAPI:
    app = FastAPI(title="Sayman demo", docs_url=None, redoc_url=None, openapi_url=None)
    # Only from this computer: a request that arrives under another name (DNS rebinding) is rejected
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.middleware("http")
    async def revalidate_page_files(request: Request, call_next):
        # Without Cache-Control a browser reuses the page script by its own guess (a stale app.js after an update);
        # no-cache keeps the copy but asks the server every time (304 when unchanged).
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    def current_user(request: Request) -> str:
        user = demo.sessions.get(request.cookies.get(COOKIE_NAME, ""))
        if user is None:
            raise HTTPException(401, "Önce giriş yapın.")
        return user

    def acquire_lock():
        if not demo.lock.acquire(blocking=False):
            raise HTTPException(409, "Sayman başka bir işi bitiriyor; birazdan yeniden deneyin.")

    @app.get("/")
    def home_page():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/kullanicilar")
    def users():
        return {"kullanicilar": [demo.profile(k) for k in demo.permissions.users]}

    @app.post("/api/giris")
    def login(request: Request, response: Response, body: dict = Body(...)):
        user = body.get("kullanici")
        if not isinstance(user, str) or user not in demo.permissions.users:
            raise HTTPException(400, "Bilinmeyen kullanıcı.")
        demo.sessions.pop(request.cookies.get(COOKIE_NAME, ""), None)  # the old session is closed
        while len(demo.sessions) >= MAX_SESSIONS:
            demo.sessions.pop(next(iter(demo.sessions)))
        token = secrets.token_urlsafe(32)
        demo.sessions[token] = user
        response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="strict")
        return demo.profile(user)

    @app.post("/api/cikis")
    def logout(request: Request, response: Response):
        demo.sessions.pop(request.cookies.get(COOKIE_NAME, ""), None)
        response.delete_cookie(COOKIE_NAME)
        return {"tamam": True}

    @app.get("/api/ben")
    def profile(request: Request):
        return demo.profile(current_user(request))

    @app.get("/api/durum")
    def status():
        return {"bugun": demo.clock.simdi.isoformat(), "veri_bitis": demo.clock.veri_bitis.isoformat(),
                "adim_gun": demo.step_days, "model": demo.model_name, "model_hazir": demo.model_ready,
                "mesgul": demo.lock.locked()}

    @app.get("/api/ornek_sorular")
    def sample_questions(request: Request):
        role = demo.permissions.users[current_user(request)]
        return {"sorular": SAMPLE_QUESTIONS["cfo" if role == "cfo" else "satis"]}

    @app.post("/api/sor")
    def ask(request: Request, body: dict = Body(...)):
        user = current_user(request)  # identity only from the session; other fields in the body are ignored
        question = body.get("soru")
        if not isinstance(question, str) or not question.strip() or len(question) > MAX_QUESTION_LENGTH:
            raise HTTPException(422, f"Soru 1 ile {MAX_QUESTION_LENGTH} karakter arasında olmalı.")
        acquire_lock()
        try:
            turn = demo.assistant.ask(question.strip(), demo.card(user))
        finally:
            demo.lock.release()
        return {"durum": turn.status, "cevap": turn.answer, "sure_s": turn.sure_s,
                "araclar": [{"arac": c.get("arac"), "hata": c["yanit"].get("hata") if c.get("hata_mi") else None} for c in turn.calls],
                "bugun": demo.clock.simdi.isoformat()}

    @app.post("/api/ileri_sar")
    def advance_time(request: Request, body: dict | None = Body(None)):
        user = current_user(request)
        days = (body or {}).get("gun", demo.step_days)
        if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= MAX_STEP_DAYS:
            raise HTTPException(422, f"Gün 1 ile {MAX_STEP_DAYS} arasında olmalı.")
        acquire_lock()
        try:
            result = demo.advance(days)
        except DemoError as e:
            raise HTTPException(409, str(e)) from None
        finally:
            demo.lock.release()
        return {"onceki": result["onceki"], "bugun": result["bugun"],
                "yeni_uyarilar": demo.filter_alerts(user, result["yeni"]), "rapor": demo.report(user)}

    @app.get("/api/uyarilar")
    def alerts(request: Request):
        return {"bugun": demo.clock.simdi.isoformat(), "uyarilar": demo.alerts(current_user(request))}

    @app.get("/api/rapor")
    def report(request: Request):
        return demo.report(current_user(request))

    @app.get("/belge/adlar")
    def document_names(request: Request):
        return {"adlar": demo.document_names(current_user(request))}

    @app.get("/belge/{document_id}.pdf")
    def document_pdf(request: Request, document_id: str):
        found = demo.document_pdf(current_user(request), document_id)
        if found is None:
            raise HTTPException(404, "Belge bulunamadı.")
        path, name = found
        # The id is the ASCII fallback; browsers save the file under the readable name (RFC 6266 filename*).
        return FileResponse(path, media_type="application/pdf", headers={
            "Content-Disposition": f"inline; filename=\"{document_id}.pdf\"; filename*=UTF-8''{quote(name)}",
            "Cache-Control": "no-store"})

    @app.get("/belge/{document_id}/{article}")
    def document_article(request: Request, document_id: str, article: str):
        page = demo.article_page(current_user(request), document_id, article)
        if page is None:
            raise HTTPException(404, "Belge bulunamadı.")
        return RedirectResponse(f"/belge/{document_id}.pdf#page={page}", status_code=302,
                                headers={"Cache-Control": "no-store"})

    return app
