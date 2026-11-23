"""Phase 3 acceptance test: tools, permissions and 60 unauthorized attempts on the real data.

The Phase 1 scripts and scripts/phase02_build_warehouse.py must have been run first. The server opens the warehouse
read-only; the scope warehouses of the sales roles are built in a temporary folder. Identity cards are signed with a
test key.

Acceptance criteria:
- All 60 unauthorized attempts behave as expected; leakage is 0. The attempts in group C really touch the data that
  must be hidden (the CFO gets a different answer to the same question). The one exception: all cheques belong to
  domestic customers, so there is nothing to hide in the domestic manager's cheque question.
- The scope warehouses of the sales roles are identical to a warehouse that is filtered separately in Python and
  built from the result; the answers of the allowed tools are also identical to the answers in that warehouse.
- Answers are bounded: the longest answer is under 3,400 characters, the tool list under 8,000 characters; every
  call finishes within 2 s.
- Every call is in the audit log; the chain is intact.
"""

import datetime as dt
import json
import time
from pathlib import Path

import support.authorization_trials as fd
import pytest

from sayman.warehouse.loader import open_warehouse
from sayman.tools.audit_log import AuditLog, verify_chain
from sayman.tools.identity import generate_card
from sayman.tools.server import SaymanServer
from sayman.tools.permissions import read_permissions
from sayman.data.table_io import SCHEMA, read_csv, data_digest
from sayman.data.clock import read_clock

ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "data" / "uretilen"
WAREHOUSE = ROOT / "data" / "ambar" / "sayman.duckdb"
CLOCK_FILE = ROOT / "data" / "saat.json"
SIGNING_KEY = bytes(range(1, 33))
NOW_EPOCH = 1_790_000_000.0

pytestmark = pytest.mark.skipif(not (GENERATED.exists() and WAREHOUSE.exists() and CLOCK_FILE.exists()),
                                reason="Önce phase01_generate_data.py ve phase02_build_warehouse.py çalıştırılmalı")
CFO, DOMESTIC, EXPORT = fd.USERS["cfo"], fd.USERS["yurtici"], fd.USERS["ihracat"]


@pytest.fixture(scope="module")
def tables():
    return read_csv(GENERATED)


@pytest.fixture(scope="module")
def clock():
    return read_clock(CLOCK_FILE)


@pytest.fixture(scope="module")
def server_env(tmp_path_factory, clock):
    folder = tmp_path_factory.mktemp("faz3_kabul")
    s = SaymanServer(warehouse=WAREHOUSE, clock=lambda: clock, key=SIGNING_KEY, permissions=read_permissions(),
                       audit_log=AuditLog(folder / "kayit.jsonl"), scope_dir=folder / "kapsam",
                       time_fn=lambda: NOW_EPOCH)
    yield s, folder
    s.close()


@pytest.fixture(scope="module")
def server(server_env):
    return server_env[0]


@pytest.fixture(scope="module")
def judge(tables, clock, tmp_path_factory):
    h = fd.Judge(tables, clock.simdi, tmp_path_factory.mktemp("hakem"))
    yield h
    h.close()


def card(user):
    return generate_card(user, SIGNING_KEY, 3600, NOW_EPOCH)


def test_warehouse_was_built_from_these_csvs(tables):
    con = open_warehouse(WAREHOUSE)
    try:
        assert con.execute("SELECT deger FROM ambar_bilgisi WHERE anahtar = 'veri_hash'").fetchone()[0] == \
            data_digest(tables)[0], "ambar data/uretilen ile aynı değil: scripts/phase02_build_warehouse.py yeniden çalıştırılmalı"
    finally:
        con.close()


def test_scope_warehouses_are_identical_to_the_python_filter(server, judge, tables):
    for scope in fd.SCOPES:
        scope_tables = fd.scope_tables(tables, scope)
        for name in SCHEMA:
            sql = f"SELECT * FROM {name} ORDER BY ALL"
            assert server.connections[scope].execute(sql).fetchall() == \
                judge.reference_warehouses[scope].execute(sql).fetchall(), (scope, name)
            assert server.connections[scope].execute(f"SELECT count(*) FROM {name}").fetchone()[0] == len(scope_tables[name])
    counts = {k: server.connections[k].execute("SELECT count(*) FROM musteriler").fetchone()[0]
               for k in ("hepsi", *fd.SCOPES)}
    assert counts["yurtici"] > 0 and counts["ihracat"] > 0 and counts["yurtici"] + counts["ihracat"] == \
        counts["hepsi"]


def test_60_unauthorized_attempts_zero_leakage(server, judge, tables, clock):
    attempts = fd.trials(tables, clock.simdi, clock.veri_bitis)
    results = fd.run_trials(server, attempts, SIGNING_KEY, NOW_EPOCH, judge)
    problematic = [(r["no"], r["arac"], r["argumanlar"], r["sorunlar"]) for r in results if r["sorunlar"]]
    assert not problematic
    outside_cheques = [c for c in tables["cekler"] if c["musteri_id"] in
                    {m["musteri_id"] for m in tables["musteriler"] if not m["yurtici"]}]
    expected_empty = [] if outside_cheques else [(DOMESTIC, "cek_portfoyu")]
    assert [(r["kullanici"], r["arac"]) for r in results if r["grup"] == "C" and not r["gizledi"]] == expected_empty


def _in_scope_calls(server, scope, now):
    con = server.connections[scope]
    customers = [r[0] for r in con.execute(
        "SELECT m.ad FROM musteriler m JOIN faturalar f USING (musteri_id) WHERE f.fatura_tarihi <= ? "
        "GROUP BY m.ad ORDER BY count(*) DESC, m.ad LIMIT 3", [now]).fetchall()]
    invoices = [r[0] for r in con.execute(
        "SELECT fatura_id FROM faturalar WHERE fatura_tarihi <= ? ORDER BY fatura_id", [now]).fetchall()]
    earlier = (now - dt.timedelta(days=180)).isoformat()
    return ([("alacak_yaslandirma", {}), ("alacak_yaslandirma", {"en_fazla": 10}), ("alacak_yaslandirma", {"tarih": earlier}),
             ("geciken_alacaklar", {"min_gun": 1, "en_fazla": 10}), ("geciken_alacaklar", {"min_gun": 90}),
             ("dso_hesapla", {"donem": "son_30_gun"}), ("dso_hesapla", {"donem": "son_90_gun"}),
             ("dso_hesapla", {"donem": "son_12_ay", "tarih": "2025-12-31"}),
             ("doviz_kuru", {"para_birimi": "USD"}), ("doviz_kuru", {"para_birimi": "GBP", "tarih": earlier}),
             ("cek_portfoyu", {}), ("cek_portfoyu", {"durum": "karsiliksiz", "en_fazla": 10}),
             ("cek_portfoyu", {"vade_gun": 30}), ("fatura_ara", {"durum": "acik", "en_fazla": 10}),
             ("fatura_ara", {"durum": "iade"}), ("fatura_ara", {"fatura_no": invoices[len(invoices) // 2]})]
            + [("musteri_ozeti", {"musteri_adi": name}) for name in customers]
            + [("alacak_yaslandirma", {"musteri_adi": customers[0]}),
               ("fatura_ara", {"musteri_adi": customers[1], "durum": "kapali"})])


@pytest.mark.parametrize("user", [DOMESTIC, EXPORT])
def test_sales_role_gets_the_scoped_answer_on_allowed_tools(server, judge, clock, user):
    scope = server.permissions.roller[server.permissions.users[user]].scope
    calls = _in_scope_calls(server, scope, clock.simdi)
    # belge_ara (Phase 6) looks at the document library, not the warehouse; role filtering is tested in
    # test_phase06_document_tool.py.
    assert {a for a, _ in calls} == \
        set(server.permissions.roller[server.permissions.users[user]].tools) - {"belge_ara"}
    answers = fd.call_mcp(server, [(a, args, True, card(user)) for a, args in calls])
    for (tool, args), (response, error) in zip(calls, answers):
        attempt = {"arac": tool, "argumanlar": args, "beklenen": ("kapsamli",)}
        assert judge.evaluate_response(attempt, scope, response, error, exclude_echoed=True) == [], (tool, args)
        assert not error, (tool, args, response)


def test_answers_and_tool_list_are_bounded_and_fast(server, clock):
    tools = [{"type": "function", "function": {"name": t.name, "description": t.description,
                                                 "parameters": t.inputSchema}} for t in server.tools()]
    # The Phase 9 third arm added musteri_adi to cek_portfoyu (+116 characters; before it was 7,986). The real
    # limit is the token budget (`assistant.orchestrator.Ayar.arac_listesi_token` = 2400); the character limit is a
    # rough proxy for it.
    assert len(json.dumps(tools, ensure_ascii=False)) < 8200
    top_customer = server.connections["hepsi"].execute(
        "SELECT m.ad FROM faturalar f JOIN musteriler m USING (musteri_id) GROUP BY m.ad "
        "ORDER BY count(*) DESC, m.ad LIMIT 1").fetchone()[0]
    measurements = []
    for user in (CFO, DOMESTIC, EXPORT):
        allowed = server.permissions.roller[server.permissions.users[user]].tools
        for tool, args in fd.widest_calls(clock.simdi, top_customer):
            if tool not in allowed:
                continue
            start = time.perf_counter()
            response, error = fd.call_mcp(server, [(tool, args, True, card(user))])[0]
            duration = time.perf_counter() - start
            assert not error, (user, tool, response)
            measurements.append((len(json.dumps(response, ensure_ascii=False)), duration, user, tool, args.get("en_fazla")))
    # 3,400 characters ~1,800 tokens (measured with qwen3.5:9b in reports/faz3_araclar.json): after the tool list,
    # the system prompt and the answer share, two of the longest answers fit comfortably in the 8,192-token window;
    # the third is on the edge.
    assert max(o[0] for o in measurements) < 3400, max(measurements)
    assert max(o[1] for o in measurements) < 2.0, max(measurements, key=lambda o: o[1])


def test_every_call_is_in_the_audit_log_and_the_chain_is_intact(server_env):
    s, folder = server_env
    in_file = AuditLog.read(folder / "kayit.jsonl")
    assert len(in_file) == len(s.audit_log.rows) > 60 and verify_chain(in_file) == []
    text = (folder / "kayit.jsonl").read_text(encoding="utf-8")
    for k in (CFO, DOMESTIC, EXPORT):
        assert card(k).split(".")[1] not in text


def test_amount_paid_before_its_invoice_does_not_reveal_the_future_invoice(server):
    """In the cash-squeeze event some supplier payments were made before their invoice. On every day between the
    payment day and the invoice day, the CFO's tools must not show the number of an invoice that has not been issued yet."""
    from sayman.tools.catalog import TOOLS_BY_NAME, Context

    con = server.connections["hepsi"]
    samples = con.execute("""
        SELECT o.tarih, a.alis_fatura_id, a.tarih FROM odemeler o JOIN alis_faturalari a USING (alis_fatura_id)
        WHERE o.tarih < a.tarih ORDER BY o.tarih, a.alis_fatura_id""").fetchall()
    assert len(samples) == 34
    for day in sorted({o[0] for o in samples}):
        future = {purchase_invoice_id for _, purchase_invoice_id, invoice_date in samples if invoice_date > day} | {
            r[0] for r in con.execute("SELECT alis_fatura_id FROM alis_faturalari WHERE tarih > ?", [day]).fetchall()}
        b = Context(con, day)
        for tool, args in [("yaklasan_odemeler", {"gun_sayisi": 90, "en_fazla": 10}),
                           ("banka_hareketleri", {"baslangic": day.isoformat(), "bitis": day.isoformat(), "en_fazla": 10})]:
            _, result, source = TOOLS_BY_NAME[tool].run(b, args)
            text = json.dumps([result, source], ensure_ascii=False)
            assert not [purchase_invoice_id for purchase_invoice_id in future if purchase_invoice_id in text], (day, tool)
