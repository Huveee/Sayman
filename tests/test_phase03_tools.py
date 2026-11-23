"""Phase 3: tools, identity, permissions and audit log (with Phase 2's small hand-computed company; no download needed).

All calls go through a real MCP client session; the identity card is sent in the request header (_meta).
Scopes in the hand-built data: domestic = M1 Alfa Ltd., M3 Gama AŞ (no transactions yet), M4 Delta Ltd.;
export = M2 Beta GmbH (invoices F07, F08). Today AS_OF = 31 March 2025.
"""

import datetime as dt
import json
import re
from dataclasses import replace
from pathlib import Path

import duckdb
import support.authorization_trials as fd
import jsonschema
import pytest
from test_phase02_warehouse import AS_OF, _hand_built_tables

from sayman.warehouse.loader import build_warehouse, query_rows
from sayman.tools import server as server_module
from sayman.tools.catalog import TOOLS, AGGREGATE_TOOLS
from sayman.tools.audit_log import AuditLog, verify_chain
from sayman.tools.identity import read_key, generate_card
from sayman.tools.server import SaymanServer
from sayman.tools.permissions import read_permissions
from sayman.data.table_io import SCHEMA, write_csv
from sayman.data.clock import Clock

iso_date = dt.date.fromisoformat
SIGNING_KEY = bytes(range(32))
NOW_EPOCH = 1_750_000_000.0
DATA_END = iso_date("2025-06-30")
CFO, DOMESTIC, EXPORT = fd.USERS["cfo"], fd.USERS["yurtici"], fd.USERS["ihracat"]
SOURCE_COLUMNS = {"banka_hesaplari": "hesap_id", "banka_hareketleri": "hareket_id", "musteriler": "musteri_id",
                    "faturalar": "fatura_id", "tahsilatlar": "tahsilat_id", "alis_faturalari": "alis_fatura_id",
                    "cekler": "cek_id", "doviz_kurlari": "kur_id"}


@pytest.fixture(scope="module")
def tables():
    return _hand_built_tables()


@pytest.fixture(scope="module")
def server_env(tmp_path_factory, tables):
    folder = tmp_path_factory.mktemp("faz3")
    write_csv(tables, folder / "csv")
    build_warehouse(folder / "csv", folder / "sayman.duckdb").close()
    s = SaymanServer(warehouse=folder / "sayman.duckdb", clock=lambda: Clock(AS_OF, DATA_END), key=SIGNING_KEY,
                       permissions=read_permissions(), audit_log=AuditLog(folder / "denetim" / "kayit.jsonl"),
                       scope_dir=folder / "kapsam", time_fn=lambda: NOW_EPOCH)
    yield s, folder
    s.close()


@pytest.fixture(scope="module")
def server(server_env):
    return server_env[0]


@pytest.fixture(scope="module")
def judge(tables, tmp_path_factory):
    h = fd.Judge(tables, AS_OF, tmp_path_factory.mktemp("hakem"))
    yield h
    h.close()


def card(user):
    return generate_card(user, SIGNING_KEY, 3600, NOW_EPOCH)


def call(server, user, tool, **args):
    """A single call; (response, is_error)."""
    return fd.call_mcp(server, [(tool, args, user is not None, card(user) if user else None)])[0]


def result(server, user, tool, **args):
    response, error = call(server, user, tool, **args)
    assert not error, response
    return response["sonuc"]


def refusal(server, user, tool, **args):
    response, error = call(server, user, tool, **args)
    assert error and set(response) == {"cagri_id", "hata", "mesaj"}, response
    return response["hata"]


# ---------------------------------------------------------------- tool list: read-only


def test_tool_list_is_read_only_and_matches_the_role_table(server):
    tools = fd.anyio.run(_list_tools, server)
    names = {a.name for a in tools}
    assert len(tools) == 14 and names == {a.name for a in TOOLS}
    permissions = read_permissions()
    assert permissions.roller["cfo"].tools == names
    assert all(r.tools <= names for r in permissions.roller.values()), "rol tablosunda olmayan araç"
    for a in tools:
        assert a.annotations.readOnlyHint is True and a.annotations.destructiveHint is False, a.name
        jsonschema.Draft202012Validator.check_schema(a.inputSchema)
        assert a.inputSchema["additionalProperties"] is False, a.name


async def _list_tools(server):
    async with fd.create_connected_server_and_client_session(server.mcp) as session:
        return (await session.list_tools()).tools


def test_no_writing_sql_in_tools_and_metrics():
    root = Path(__file__).resolve().parents[1] / "src" / "sayman"
    writing_sql = re.compile(r"\b(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|ATTACH|DETACH|COPY|EXPORT|IMPORT|INSTALL|LOAD|"
                       r"PRAGMA|CHECKPOINT|VACUUM|TRUNCATE)\b")
    for file in (root / "tools" / "catalog.py", root / "warehouse" / "metrics.py"):
        assert not writing_sql.findall(file.read_text(encoding="utf-8")), file.name


@pytest.mark.parametrize("sql", ["INSERT INTO musteriler (musteri_id) VALUES ('X')", "DELETE FROM faturalar",
                                 "UPDATE banka_hesaplari SET acilis_bakiye_kurus = 0", "CREATE TABLE x (a INTEGER)",
                                 "DROP TABLE faturalar"])
def test_every_role_connection_is_closed_to_writing(server, sql):
    assert set(server.connections) == {"hepsi", "yurtici", "ihracat"}
    for scope, con in server.connections.items():
        with pytest.raises(duckdb.Error, match="read-only|read only"):
            con.execute(sql)


# ---------------------------------------------------------------- identity


def test_every_tool_without_identity_is_refused_and_logged(server):
    before = len(server.audit_log.rows)
    answers = fd.call_mcp(server, [(a.name, {}, False, None) for a in TOOLS])
    for a, (response, error) in zip(TOOLS, answers):
        assert error and response["hata"] == "kimlik_gecersiz" and set(response) == {"cagri_id", "hata", "mesaj"}, a.name
    log_rows = server.audit_log.rows[before:]
    assert [s["arac"] for s in log_rows] == [a.name for a in TOOLS]
    assert all(s["sonuc"] == "reddedildi" and s["neden"] == "kart_yok" and s["kullanici"] is None for s in log_rows)


@pytest.mark.parametrize("attack", list(fd.IDENTITY_ATTACKS))
def test_identity_attacks(server, attack):
    user = DOMESTIC if attack in ("degistirilmis", "rol_iddiasi") else CFO
    send, k = fd.prepare_card(attack, user, SIGNING_KEY, NOW_EPOCH)
    before = len(server.audit_log.rows)
    response, error = fd.call_mcp(server, [("nakit_pozisyonu", {}, send, k)])[0]
    row = server.audit_log.rows[before]
    assert error and "sonuc" not in response
    assert row["neden"] == fd.IDENTITY_ATTACKS[attack]
    if attack == "rol_iddiasi":  # the card is valid; the role is read from the user list, not from the card
        assert (response["hata"], row["kullanici"], row["rol"]) == ("yetki_yok", DOMESTIC, "satis_yurtici")
    else:
        assert response["hata"] == "kimlik_gecersiz" and row["kullanici"] is None
        assert response["mesaj"] == "Kimlik doğrulanamadı."  # the reason is not told to the user


def test_valid_card_is_accepted_and_not_written_to_the_log(server_env):
    s, folder = server_env
    k = card(CFO)
    response, error = fd.call_mcp(s, [("nakit_pozisyonu", {}, True, k)])[0]
    assert not error and response["kapsam"] == "tüm şirket"
    assert s.audit_log.rows[-1]["kullanici"] == CFO
    assert k.split(".")[1] not in (folder / "denetim" / "kayit.jsonl").read_text(encoding="utf-8")


def test_server_without_a_key_does_not_start(tmp_path):
    for env_vars in ({}, {"SAYMAN_KIMLIK_ANAHTARI": ""}, {"SAYMAN_KIMLIK_ANAHTARI": "zz" * 32},
                   {"SAYMAN_KIMLIK_ANAHTARI": "ab" * 16}):
        with pytest.raises(RuntimeError):
            read_key(env_vars)
    assert read_key({"SAYMAN_KIMLIK_ANAHTARI": SIGNING_KEY.hex()}) == SIGNING_KEY
    with pytest.raises(RuntimeError, match="anahtar"):
        SaymanServer(warehouse=tmp_path / "yok.duckdb", clock=lambda: Clock(AS_OF, DATA_END), key=b"",
                       permissions=read_permissions(), audit_log=AuditLog())


def test_card_lasts_at_most_12_hours():
    with pytest.raises(ValueError):
        generate_card(CFO, SIGNING_KEY, 13 * 3600, NOW_EPOCH)


# ---------------------------------------------------------------- permissions and scope


def test_sales_managers_see_only_their_own_customers(server):
    # Domestic: M1 1,120.00 + M4 150.00 = 1,270.00. Export: M2 15,000 euro-cents x 36.5 = 5,475.00.
    assert result(server, DOMESTIC, "alacak_yaslandirma")["toplam_tl"] == "1270.00"
    assert result(server, EXPORT, "alacak_yaslandirma")["toplam_tl"] == "5475.00"
    assert result(server, CFO, "alacak_yaslandirma")["toplam_tl"] == "6745.00"
    assert [f["fatura_id"] for f in result(server, EXPORT, "geciken_alacaklar", min_gun=1)["faturalar"]] == ["F07"]
    assert result(server, EXPORT, "cek_portfoyu", durum="hepsi")["cek_sayisi"] == 0
    assert result(server, DOMESTIC, "cek_portfoyu", durum="hepsi")["cek_sayisi"] == 2


@pytest.mark.parametrize("user", [DOMESTIC, EXPORT])
def test_sales_manager_cannot_call_forbidden_tools(server, user):
    for tool in fd.FORBIDDEN_TOOLS:
        assert refusal(server, user, tool, **fd._valid_args(tool, AS_OF)) == "yetki_yok", tool


def test_nonexistent_and_not_yours_get_the_same_answer(server):
    def same(user, tool, existing, missing):
        (a, _), (b, _) = call(server, user, tool, **existing), call(server, user, tool, **missing)
        a.pop("cagri_id"), b.pop("cagri_id")
        assert a == b, (existing, a, b)

    same(DOMESTIC, "musteri_ozeti", {"musteri_adi": "Beta GmbH"}, {"musteri_adi": "Zeta GmbH"})
    same(DOMESTIC, "alacak_yaslandirma", {"musteri_adi": "M2"}, {"musteri_adi": "M9"})
    assert result(server, DOMESTIC, "alacak_yaslandirma", musteri_adi="m1")["musteri_id"] == "M1"  # its own customer
    same(DOMESTIC, "fatura_ara", {"fatura_no": "F07"}, {"fatura_no": "F99"})
    same(EXPORT, "fatura_ara", {"fatura_no": "f01"}, {"fatura_no": "f98"})
    same(EXPORT, "musteri_ozeti", {"musteri_adi": "Alfa Ltd."}, {"musteri_adi": "Omega Ltd."})


def test_scope_warehouses_are_identical_to_the_python_filter(server, judge):
    for scope in fd.SCOPES:
        for name in SCHEMA:
            sql = f"SELECT * FROM {name} ORDER BY ALL"
            assert server.connections[scope].execute(sql).fetchall() == \
                judge.reference_warehouses[scope].execute(sql).fetchall(), (scope, name)


# ---------------------------------------------------------------- 60 attempts


def test_60_unauthorized_attempts_zero_leakage(server, tables, judge):
    attempts = fd.trials(tables, AS_OF, DATA_END)
    assert [sum(d["grup"] == g for d in attempts) for g in "ABCDE"] == [12, 12, 24, 6, 6]
    results = fd.run_trials(server, attempts, SIGNING_KEY, NOW_EPOCH, judge)
    problematic = [(r["no"], r["arac"], r["argumanlar"], r["sorunlar"]) for r in results if r["sorunlar"]]
    assert not problematic
    # The domestic manager sees all the cheques (export customers have none): that attempt finds no data to hide.
    empty = [(r["kullanici"], r["arac"]) for r in results if r["grup"] == "C" and not r["gizledi"]]
    assert empty == [(DOMESTIC, "cek_portfoyu")]


def test_refusal_answers_are_short_and_do_not_echo_the_input(server):
    long_text = "Z" * 50_000
    for tool, args in [("musteri_ozeti", {"musteri_adi": long_text}), ("fatura_ara", {long_text: 1}),
                       ("fatura_ara", {"fatura_no": long_text}), ("dso_hesapla", {"donem": long_text}),
                       ("geciken_alacaklar", {"min_gun": long_text}), ("geciken_alacaklar", {}),
                       ("nakit_pozisyonu", {"tarih": long_text})]:
        response, error = call(server, CFO, tool, **args)
        text = json.dumps(response, ensure_ascii=False)
        assert error and response["hata"] == "gecersiz_arguman" and len(text) < 300 and "ZZZ" not in text, (tool, text)
    response, error = fd.call_mcp(server, [(long_text, {}, True, card(CFO))])[0]
    assert error and response["hata"] == "bilinmeyen_arac" and len(server.audit_log.rows[-1]["arac"]) == 100
    assert len(json.dumps(server.audit_log.rows[-2]["argumanlar"])) < 2100  # truncated in the log as well
    assert "Eksik argüman: min_gun." in fd.call_mcp(server, [("geciken_alacaklar", {}, True, card(CFO))])[0][0]["mesaj"]


def test_judge_catches_leakage(server, tables, judge):
    """The judge itself is tested too: it must catch an out-of-scope answer."""
    attempt = {"arac": "fatura_ara", "argumanlar": {"fatura_no": "F07"}, "beklenen": ("kapsamli",)}
    cfo_answer, _ = call(server, CFO, "fatura_ara", fatura_no="F07")
    problems = judge.evaluate_response(attempt, "yurtici", cfo_answer, False)
    assert any("farklı" in s for s in problems) and any("kapsam dışı değer" in s for s in problems)


# ---------------------------------------------------------------- date


def test_future_date_is_refused_in_every_date_field(server):
    tomorrow = (AS_OF + dt.timedelta(days=1)).isoformat()
    today = AS_OF.isoformat()
    for a in TOOLS:
        for field in a.date_fields:
            args = {"baslangic": today, "bitis": today} if "baslangic" in a.properties else {}
            args.update({k: v for k, v in {"musteri_adi": "Alfa", "min_gun": 1, "donem": "son_30_gun",
                                            "para_birimi": "EUR"}.items() if k in a.required})
            assert not call(server, CFO, a.name, **{**args, field: today})[1], (a.name, field)
            assert refusal(server, CFO, a.name, **{**args, field: tomorrow}) == "gelecek_tarih", (a.name, field)


def test_new_day_becomes_visible_when_the_clock_advances(server, monkeypatch):
    tomorrow = (AS_OF + dt.timedelta(days=1)).isoformat()
    assert refusal(server, CFO, "nakit_pozisyonu", tarih=tomorrow) == "gelecek_tarih"
    monkeypatch.setattr(server, "clock", lambda: Clock(AS_OF + dt.timedelta(days=1), DATA_END))
    # 1 April: A_TRY unchanged (H10 is on 2 April); C_USD opened, balance 0; EUR 6,000 x 37.5 = 225,000.
    assert result(server, CFO, "nakit_pozisyonu", tarih=tomorrow)["toplam_tl"] == "12419.00"


@pytest.mark.parametrize("args", [{"tarih": "20250331"}, {"tarih": "2025-02-30"}, {"tarih": "31.03.2025"},
                                  {"tarih": "2025-03-31T00:00:00"}, {"tarih": 20250331}, {"en_fazla": 0},
                                  {"en_fazla": 11}, {"en_fazla": True}, {"en_fazla": "5"},
                                  {"musteri_adi": "a" * 101}, {"bilinmeyen": 1}])
def test_invalid_arguments_are_refused(server, args):
    assert refusal(server, CFO, "alacak_yaslandirma", **args) == "gecersiz_arguman"


def test_integer_works_even_when_written_with_a_decimal_point(server):
    s = result(server, CFO, "geciken_alacaklar", min_gun=30.0, en_fazla=2.0)
    assert s["min_gun"] == 30 and isinstance(s["min_gun"], int) and len(s["faturalar"]) == 2
    assert refusal(server, CFO, "geciken_alacaklar", min_gun=30.5) == "gecersiz_arguman"


def test_reversed_range_gives_an_understandable_error(server):
    response, error = call(server, CFO, "banka_hareketleri", baslangic="2025-03-31", bitis="2025-03-01")
    assert error and response["hata"] == "gecersiz_arguman" and "sonra" in response["mesaj"]


# ---------------------------------------------------------------- tool values (computed by hand)


def test_cash_position(server):
    s = result(server, CFO, "nakit_pozisyonu")
    assert [(h["hesap_id"], h["bakiye"], h["kur"], h["bakiye_tl"]) for h in s["hesaplar"]] == [
        ("A_TRY", "10169.00", "1", "10169.00"), ("B_EUR", "60.00", "37.1234", "2227.40")]
    assert s["toplam_tl"] == "12396.40"


def test_bank_transactions(server):
    s = result(server, CFO, "banka_hareketleri", baslangic="2025-03-01", bitis="2025-03-31", en_fazla=3)
    # TRY: H05 -30, H06 -120, H07 +50, H08 -500, H09 +49. EUR: H12 +50, H13 +10.
    assert s["toplamlar"] == [{"para_birimi": "EUR", "adet": 2, "giris": "60.00", "cikis": "0.00"},
                              {"para_birimi": "TRY", "adet": 5, "giris": "99.00", "cikis": "-650.00"}]
    assert (s["hareket_sayisi"], s["gosterilen"]) == (7, 3)
    assert [h["hareket_id"] for h in s["hareketler"]] == ["H09", "H08", "H07"]
    inflows = result(server, CFO, "banka_hareketleri", baslangic="2025-03-01", bitis="2025-03-31", yon="giris")
    assert [h["hareket_id"] for h in inflows["hareketler"]] == ["H09", "H07", "H13", "H12"]
    single = result(server, CFO, "banka_hareketleri", baslangic="2025-03-01", bitis="2025-03-31", hesap_id="B_EUR")
    assert [h["hareket_id"] for h in single["hareketler"]] == ["H13", "H12"]


def test_receivable_aging(server):
    s = result(server, CFO, "alacak_yaslandirma")
    assert s["dilimler"] == {"vadesi_gelmemis": "420.00", "gecikme_1_30": "5875.00", "gecikme_31_60": "400.00",
                             "gecikme_61_90": "0.00", "gecikme_91_180": "30.00", "gecikme_180_ustu": "70.00",
                             "eslesmemis_tahsilat": "-50.00"}
    assert (s["toplam_tl"], s["vadesi_gecmis_tl"], s["musteri_sayisi"]) == ("6745.00", "6375.00", 3)
    assert [(m["musteri_id"], m["toplam_tl"], m["vadesi_gecmis_tl"]) for m in s["en_buyuk_alacaklar"]] == [
        ("M2", "5475.00", "5475.00"), ("M1", "1120.00", "870.00"), ("M4", "150.00", "30.00")]
    single = result(server, CFO, "alacak_yaslandirma", musteri_adi="delta")
    assert (single["musteri_id"], single["toplam_tl"], single["dilimler"]["vadesi_gelmemis"]) == ("M4", "150.00", "120.00")
    assert result(server, CFO, "alacak_yaslandirma", musteri_adi="Ltd")["birden_fazla"] is True


def test_overdue_receivables(server):
    s = result(server, CFO, "geciken_alacaklar", min_gun=1)
    assert [(f["fatura_id"], f["gecikme_gun"], f["acik_tutar"], f["acik_tutar_tl"]) for f in s["faturalar"]] == [
        ("F07", 20, "150.00", "5475.00"), ("F02", 58, "400.00", "400.00"), ("F04", 19, "400.00", "400.00"),
        ("F01", 181, "70.00", "70.00"), ("F09", 120, "30.00", "30.00")]
    assert (s["fatura_sayisi"], s["toplam_tl"]) == (5, "6375.00")
    s30 = result(server, CFO, "geciken_alacaklar", min_gun=30, en_fazla=2)
    assert (s30["fatura_sayisi"], s30["toplam_tl"], [f["fatura_id"] for f in s30["faturalar"]]) == (
        3, "500.00", ["F02", "F01"])


def test_customer_summary(server):
    s = result(server, CFO, "musteri_ozeti", musteri_adi="alfa")
    assert (s["musteri_id"], s["acik_alacak_tl"], s["acik_fatura_sayisi"], s["en_eski_acik_vade"]) == (
        "M1", "1120.00", 4, "2024-10-01")
    assert [t["tahsilat_id"] for t in s["son_tahsilatlar"]] == ["T5", "T2", "T1"]
    assert (s["bekleyen_cek_sayisi"], s["bekleyen_cek_tutari"]) == (1, "200.00")  # C1; C2 bounced
    # Closed in the last 12 months: only F03 (10 days after the due date).
    assert (s["son_12_ay_kapanan_fatura"], s["son_12_ay_gec_kapanan_fatura"], s["son_12_ay_ortalama_gecikme_gun"]) \
        == (1, 1, 10)
    # Gama AŞ's first transaction is on 15 April: not visible today yet.
    assert result(server, CFO, "musteri_ozeti", musteri_adi="Gama AŞ")["bulunamadi"] is True
    assert refusal(server, CFO, "musteri_ozeti", musteri_adi="al") == "gecersiz_arguman"


def test_dso(server):
    s = result(server, CFO, "dso_hesapla", donem="son_12_ay")
    assert (s["dso_gun"], s["acik_alacak_tl"], s["donem_satisi_tl"], s["pencere_baslangic"]) == (
        "247.78", "6745.00", "9936.00", "2024-04-01")


def test_upcoming_payments(server):
    s = result(server, CFO, "yaklasan_odemeler", gun_sayisi=30)
    assert [(o["alis_fatura_id"], o["kalan_gun"], o["tutar_tl"], o["tedarikci"]) for o in s["odemeler"]] == [
        ("A4", -28, "30.00", "Delta Kağıt"), ("A1", 0, "250.00", "Delta Kağıt"), ("A3", 24, "80.00", "Kira AŞ")]
    assert (s["toplam_tl"], s["vadesi_gecmis_tl"]) == ("360.00", "30.00")


def test_cash_flow_forecast(server):
    s = result(server, CFO, "nakit_akis_tahmini", hafta_sayisi=13)
    h1 = s["haftalar"][0]
    # Week 1: receivable 618,851 + POS 9,800 inflow, supplier 28,000 outflow.
    assert (s["acilis_tl"], h1["giris_tl"], h1["cikis_tl"], h1["net_tl"], h1["bilinen_kalemlerle_bakiye_tl"]) == (
        "12396.40", "6286.51", "280.00", "6006.51", "18402.91")
    assert s["haftalar"][-1]["bilinen_kalemlerle_bakiye_tl"] == "18852.91" and s["supheli_alacak_tl"] == "30.00"


def test_fx_rate_risk_and_fx_rate(server):
    assert result(server, CFO, "kur_riski")["dovizler"] == [
        {"para_birimi": "EUR", "alacak": "150.00", "banka": "60.00", "toplam": "210.00", "kur": "37.1234",
         "toplam_tl": "7795.91", "kur_yuzde_10_degisirse_etki_tl": "779.59", "alacak_kur_farki_tl": "93.51"}]
    assert result(server, CFO, "kur_riski", para_birimi="USD")["dovizler"] == []
    usd, error = call(server, DOMESTIC, "doviz_kuru", para_birimi="USD")
    assert not error and (usd["sonuc"]["alis"], usd["kaynak"]) == ("34", {"doviz_kurlari": ["2025-03-31-USD"]})
    sunday = result(server, DOMESTIC, "doviz_kuru", para_birimi="EUR", tarih="2025-03-30")
    assert (sunday["kur_tarihi"], sunday["alis"]) == ("2025-03-28", "37")
    assert result(server, EXPORT, "doviz_kuru", para_birimi="GBP")["bulunamadi"] is True


def test_cheque_portfolio(server):
    pending = result(server, CFO, "cek_portfoyu")
    assert (pending["cek_sayisi"], pending["toplam_tl"], [c["cek_id"] for c in pending["cekler"]]) == (
        1, "200.00", ["C1"])
    all_items = result(server, CFO, "cek_portfoyu", durum="hepsi")
    assert all_items["durumlara_gore"] == [{"durum": "karsiliksiz", "adet": 1, "toplam_tl": "99.99"},
                                       {"durum": "portfoyde", "adet": 1, "toplam_tl": "200.00"}]
    assert [c["cek_id"] for c in all_items["cekler"]] == ["C2", "C1"]
    assert result(server, CFO, "cek_portfoyu", vade_gun=10)["cek_sayisi"] == 0  # C1 is due in 20 days
    assert result(server, CFO, "cek_portfoyu", vade_gun=20)["cek_sayisi"] == 1


def test_cheque_portfolio_by_customer_name(server):
    """Phase 9 third arm: cheques are filtered by customer name (both cheques belong to M1 Alfa)."""
    assert result(server, CFO, "cek_portfoyu", musteri_adi="Alfa Ltd.", durum="hepsi")["cek_sayisi"] == 2
    assert result(server, CFO, "cek_portfoyu", musteri_adi="Delta Ltd.", durum="hepsi")["cek_sayisi"] == 0
    # Customer and due-date filters together (the due-date condition is added to the customer condition, not
    # written over it).
    assert result(server, CFO, "cek_portfoyu", musteri_adi="Alfa Ltd.", vade_gun=20)["cek_sayisi"] == 1
    assert result(server, CFO, "cek_portfoyu", musteri_adi="Alfa Ltd.", vade_gun=10)["cek_sayisi"] == 0
    assert result(server, CFO, "cek_portfoyu", musteri_adi="Zeta Ltd.")["bulunamadi"] is True
    # Scope: the export manager cannot see a domestic customer's cheques; the answer is the same as for
    # "no such customer".
    assert result(server, EXPORT, "cek_portfoyu", musteri_adi="Alfa Ltd.") == \
        result(server, EXPORT, "cek_portfoyu", musteri_adi="Zeta Ltd.")
    assert result(server, DOMESTIC, "cek_portfoyu", musteri_adi="Alfa Ltd.", durum="hepsi")["cek_sayisi"] == 2


def test_exceeding_the_max_limit_says_what_to_do(server):
    """Phase 9 third arm: a model that exceeds the limit learns from the message which value to retry with."""
    response, error = call(server, CFO, "cek_portfoyu", en_fazla=11)
    assert error and response["mesaj"] == "Argüman hatası: en_fazla en çok 10 olabilir; 10 ile yeniden deneyin."
    response, error = call(server, CFO, "cek_portfoyu", en_fazla=0)
    assert error and response["mesaj"] == "Argüman hatası: en_fazla 1 ile 10 arasında olmalı."


def test_pos_collections(server):
    s = result(server, CFO, "pos_tahsilatlari", baslangic="2025-03-01", bitis="2025-03-31")
    assert s == {"baslangic": "2025-03-01", "bitis": "2025-03-31", "islem_sayisi": 2, "iade_sayisi": 0,
                 "satis_tl": "150.00", "iade_tl": "0.00", "komisyon_tl": "3.00", "net_tl": "147.00",
                 "valoru_gelmemis_net_tl": "98.00"}


def test_invoice_search(server):
    s = result(server, CFO, "fatura_ara", fatura_no=" f07 ")
    assert s["bulunan"] == 1 and s["faturalar"][0] == {
        "fatura_id": "F07", "kaynak_fatura_no": "F07", "tur": "satis", "musteri": "Beta GmbH",
        "fatura_tarihi": "2025-01-10", "vade_tarihi": "2025-03-11", "tutar": "200.00", "para_birimi": "EUR",
        "tutar_tl": "7300.00", "durum": "acik", "acik_tutar": "150.00"}
    return_invoices = result(server, CFO, "fatura_ara", durum="iade")["faturalar"]
    assert [(f["fatura_id"], f["iliskili_fatura_id"]) for f in return_invoices] == [("F06", "F04")]
    delta = result(server, CFO, "fatura_ara", musteri_adi="delta", durum="acik")
    assert [f["fatura_id"] for f in delta["faturalar"]] == ["F10", "F09"]
    assert result(server, CFO, "fatura_ara", fatura_no="F11")["bulunan"] == 0  # will be issued on 15 April
    assert result(server, CFO, "fatura_ara", en_fazla=3)["bulunan"] == 10


# ---------------------------------------------------------------- source identifiers and output limits


def _shown_identifiers(obj):
    """The primary identifier of every row in the answer (the most specific one first)."""
    ordered = ("hareket_id", "tahsilat_id", "cek_id", "alis_fatura_id", "fatura_id", "kur_id", "hesap_id",
              "musteri_id")
    if isinstance(obj, dict):
        key = next((a for a in ordered if a in obj), None)
        found = {obj[key]} if key else set()
        return found.union(*(_shown_identifiers(v) for v in obj.values()))
    if isinstance(obj, list):
        return set().union(*(_shown_identifiers(v) for v in obj))
    return set()


WIDE_CALLS = [
    ("nakit_pozisyonu", {}), ("banka_hareketleri", {"baslangic": "2025-01-01", "bitis": "2025-03-31", "en_fazla": 10}),
    ("alacak_yaslandirma", {}), ("alacak_yaslandirma", {"musteri_adi": "alfa"}),
    ("geciken_alacaklar", {"min_gun": 1, "en_fazla": 10}), ("musteri_ozeti", {"musteri_adi": "Alfa Ltd."}),
    ("dso_hesapla", {"donem": "son_90_gun"}), ("yaklasan_odemeler", {"gun_sayisi": 90, "en_fazla": 10}),
    ("nakit_akis_tahmini", {"hafta_sayisi": 13}), ("kur_riski", {}), ("doviz_kuru", {"para_birimi": "EUR"}),
    ("cek_portfoyu", {"durum": "hepsi", "en_fazla": 10}),
    ("pos_tahsilatlari", {"baslangic": "2025-01-01", "bitis": "2025-03-31"}),
    ("fatura_ara", {"durum": "hepsi", "en_fazla": 10}),
]


@pytest.mark.parametrize("user", [CFO, DOMESTIC, EXPORT])
def test_every_answer_carries_source_identifiers_and_they_are_real(server, user):
    role = server.permissions.roller[server.permissions.users[user]]
    con = server.connections[role.scope]
    calls = [(a, args) for a, args in WIDE_CALLS if a in role.tools]
    answers = fd.call_mcp(server, [(a, args, True, card(user)) for a, args in calls])
    for (tool, args), (response, error) in zip(calls, answers):
        assert not error, (tool, response)
        assert set(response) == {"cagri_id", "arac", "tarih", "kapsam", "sonuc", "kaynak"}
        assert re.fullmatch(r"C[0-9a-f]{12}", response["cagri_id"])
        source = response["kaynak"]
        all_items = set().union(*source.values()) if source else set()
        if tool in AGGREGATE_TOOLS or {"bulunamadi", "birden_fazla"} & set(response["sonuc"]):
            assert source == {}, tool  # only a total (referred to by cagri_id) or no rows at all
            continue
        assert source, (tool, "kaynak boş")
        shown = _shown_identifiers(response["sonuc"])
        if tool == "doviz_kuru":  # the answer does not write the identifier of the rate row separately
            assert len(all_items) == 1 and not shown
        else:
            assert shown == all_items, (tool, "kaynak, gösterilen satırlarla birebir aynı değil")
        for table, ids in source.items():
            column = SOURCE_COLUMNS[table]
            existing = {s[column] for s in query_rows(con, f"SELECT {column} FROM {table}")}
            assert set(ids) <= existing, (tool, table)


ROW_LISTS = ("hareketler", "en_buyuk_alacaklar", "faturalar", "odemeler", "cekler")


def test_lists_respect_the_max_limit(server):
    for tool, args in WIDE_CALLS:
        for n in (1, 10):
            if "en_fazla" not in dict((a.name, a.properties) for a in TOOLS)[tool] or "musteri_adi" in args:
                continue
            s = result(server, CFO, tool, **{**args, "en_fazla": n})
            lists = [v for k, v in s.items() if k in ROW_LISTS]
            assert lists and all(len(v) <= n for v in lists), (tool, n)


# ---------------------------------------------------------------- audit log


def test_every_call_is_in_the_audit_log_and_the_chain_is_intact(server_env):
    s, folder = server_env
    before = len(s.audit_log.rows)
    answers = fd.call_mcp(s, [("nakit_pozisyonu", {}, True, card(CFO)), ("nakit_pozisyonu", {}, True, card(DOMESTIC)),
                                ("yok_boyle", {}, True, card(CFO)), ("dso_hesapla", {"donem": "x"}, True, card(CFO)),
                                ("kur_riski", {}, False, None)])
    new_rows = s.audit_log.rows[before:]
    assert [r["cagri_id"] for r in new_rows] == [c["cagri_id"] for c, _ in answers]
    assert [(r["sonuc"], r.get("neden")) for r in new_rows] == [
        ("tamam", None), ("reddedildi", "yetki_yok"), ("reddedildi", "bilinmeyen_arac"),
        ("reddedildi", "gecersiz_arguman"), ("reddedildi", "kart_yok")]
    assert new_rows[0]["kayit_sayisi"] == 2 and all(isinstance(r["sure_ms"], float) for r in new_rows)
    in_file = AuditLog.read(folder / "denetim" / "kayit.jsonl")
    assert len(in_file) == len(s.audit_log.rows) and verify_chain(in_file) == []


def test_chain_breaks_when_a_record_is_altered_or_deleted(tmp_path):
    k = AuditLog(tmp_path / "k.jsonl")
    for i in range(4):
        k.write(arac="nakit_pozisyonu", sonuc="tamam", i=i)
    rows_ = AuditLog.read(tmp_path / "k.jsonl")
    assert verify_chain(rows_) == []
    altered = [dict(s) for s in rows_]
    altered[1]["sonuc"] = "reddedildi"
    assert verify_chain(altered)
    assert verify_chain(rows_[:1] + rows_[2:])
    # When the file is reopened the chain continues where it left off.
    AuditLog(tmp_path / "k.jsonl").write(arac="kur_riski", sonuc="tamam")
    whole_log = AuditLog.read(tmp_path / "k.jsonl")
    assert len(whole_log) == 5 and verify_chain(whole_log) == []
    with pytest.raises(ValueError):
        k.write(kart="x.y")


def test_unexpected_error_detail_goes_only_to_the_log(server, monkeypatch):
    def blow_up(b, a):
        raise RuntimeError("gizli ayrıntı: /yol/dosya")

    tool = server_module.TOOLS_BY_NAME["doviz_kuru"]
    monkeypatch.setitem(server_module.TOOLS_BY_NAME, "doviz_kuru", replace(tool, run=blow_up))
    response, error = call(server, CFO, "doviz_kuru", para_birimi="USD")
    assert error and response["hata"] == "hata" and "gizli" not in json.dumps(response, ensure_ascii=False)
    assert server.audit_log.rows[-1]["sonuc"] == "hata" and "gizli" in server.audit_log.rows[-1]["neden"]


# ---------------------------------------------------------------- stdio server in a separate process


async def _stdio_session(parameters, card_):
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with stdio_client(parameters) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        tools = (await session.list_tools()).tools
        with_card = await session.call_tool("nakit_pozisyonu", {}, meta={"sayman_kimlik": card_})
        without_card = await session.call_tool("nakit_pozisyonu", {})
        return tools, with_card, without_card


def test_stdio_server_runs_in_a_separate_process(server_env, tmp_path):
    import sys

    from mcp import StdioServerParameters

    from sayman.data.clock import write_clock

    _, folder = server_env
    root = Path(__file__).resolve().parents[1]
    write_clock(Clock(AS_OF, DATA_END), tmp_path / "saat.json")
    parameters = StdioServerParameters(
        command=sys.executable, cwd=str(root),
        args=["-I", str(root / "scripts" / "phase03_server.py"), "--ambar", str(folder / "sayman.duckdb"),
              "--saat", str(tmp_path / "saat.json"), "--kayit", str(tmp_path / "kayit.jsonl"),
              "--kapsam-klasoru", str(tmp_path / "kapsam")],
        env={"SAYMAN_KIMLIK_ANAHTARI": SIGNING_KEY.hex()})
    # The separate process uses the real clock: the card is also issued with the current time.
    tools, with_card, without_card = fd.anyio.run(_stdio_session, parameters, generate_card(CFO, SIGNING_KEY, 300))
    assert len(tools) == 14
    assert not with_card.isError and with_card.structuredContent["sonuc"]["toplam_tl"] == "12396.40"
    assert without_card.isError and without_card.structuredContent["hata"] == "kimlik_gecersiz"
    log_rows = AuditLog.read(tmp_path / "kayit.jsonl")
    assert [(s["sonuc"], s["kullanici"]) for s in log_rows] == [("tamam", CFO), ("reddedildi", None)]
    assert verify_chain(log_rows) == []


def test_aggregate_answers_are_reproduced_exactly_from_the_audit_log(server):
    """The answer of the four tools without row identifiers must be reproducible from the audit-log row alone:
    tool, arguments, role (-> scope) and simulation day."""
    from sayman.tools.catalog import TOOLS_BY_NAME, Context

    calls = [(CFO, "dso_hesapla", {"donem": "son_90_gun"}), (CFO, "nakit_akis_tahmini", {"hafta_sayisi": 4}),
                (CFO, "kur_riski", {}), (CFO, "pos_tahsilatlari", {"baslangic": "2025-03-01", "bitis": "2025-03-31"}),
                (EXPORT, "dso_hesapla", {"donem": "son_12_ay"})]
    before = len(server.audit_log.rows)
    answers = fd.call_mcp(server, [(a, args, True, card(k)) for k, a, args in calls])
    for (response, error), row in zip(answers, server.audit_log.rows[before:]):
        assert not error and row["cagri_id"] == response["cagri_id"] and row["arac"] in AGGREGATE_TOOLS
        assert (row["simdi"], row["tarih"]) == (AS_OF.isoformat(), response["tarih"])
        tool = TOOLS_BY_NAME[row["arac"]]
        con = server.connections[server.permissions.roller[row["rol"]].scope]
        date, s, source = tool.run(Context(con, iso_date(row["simdi"])), row["argumanlar"])
        assert json.loads(json.dumps({"tarih": date.isoformat(), "sonuc": s, "kaynak": source}, ensure_ascii=False)) \
            == {k: response[k] for k in ("tarih", "sonuc", "kaynak")}, row["arac"]
    # The day is really needed: one day later P1's value date has arrived.
    pos = TOOLS_BY_NAME["pos_tahsilatlari"].run(Context(server.connections["hepsi"], AS_OF + dt.timedelta(days=1)),
                                                        calls[3][2])[1]
    assert pos["valoru_gelmemis_net_tl"] == "0.00" != answers[3][0]["sonuc"]["valoru_gelmemis_net_tl"]
