"""Phase 9: the fake API, the generated tools, their gates and the generated oracle (on Phase 2's small hand-computed
company).

Scopes in the hand-built data: domestic = M1 Alfa Ltd., M3 Gama AŞ (its transaction is after D), M4 Delta Ltd.; export =
M2 Beta GmbH (invoices F07, F08). Today D = 31 March 2025.
"""

import datetime as dt
import json

import support.authorization_trials as fd
import pytest
from test_phase02_warehouse import AS_OF, _hand_built_tables

from sayman.warehouse.loader import build_warehouse
from sayman.tools.audit_log import AuditLog
from sayman.tools.identity import generate_card
from sayman.tools.permissions import read_permissions
from sayman.assistant.verifier import verify
from sayman.assistant.orchestrator import Assistant, Settings
from sayman.experiment.oracle import NO_ANSWER, GeneratedOracle, resolve
from sayman.experiment.generated_tools import GeneratedServer, source_identifiers, assert_read_only
from sayman.evaluation.scoring import scope_violations
from sayman.data.table_io import write_csv
from sayman.data.clock import Clock

SIGNING_KEY = bytes(range(32))
NOW_EPOCH = 1_750_000_000.0
CFO, DOMESTIC, EXPORT = fd.USERS["cfo"], fd.USERS["yurtici"], fd.USERS["ihracat"]
INVOICE_LIST, INVOICE_ONE, CUSTOMER_LIST, CUSTOMER_CARD = ("faturalar_api_v1_faturalar_get", "fatura_api_v1_faturalar", "musteriler_api_v1_musteriler_get",
                          "musteri_api_v1_musteriler")
ACCOUNT_LIST, RATE = "hesaplar_api_v1_banka_hesaplar_get", "kurlar_api_v1_kurlar_get"


@pytest.fixture(scope="module")
def server_env(tmp_path_factory):
    folder = tmp_path_factory.mktemp("faz9")
    write_csv(_hand_built_tables(), folder / "csv")
    build_warehouse(folder / "csv", folder / "sayman.duckdb").close()
    s = GeneratedServer(warehouse=folder / "sayman.duckdb", clock=lambda: Clock(AS_OF, dt.date(2025, 6, 30)), key=SIGNING_KEY,
                       permissions=read_permissions(), audit_log=AuditLog(folder / "denetim.jsonl"),
                       scope_dir=folder / "kapsam", time_fn=lambda: NOW_EPOCH)
    yield s, folder
    s.close()


@pytest.fixture(scope="module")
def server(server_env):
    return server_env[0]


def card(user):
    return generate_card(user, SIGNING_KEY, 3600, NOW_EPOCH)


def call_tool(server, user, name, arguments):
    return server.gate(name, arguments, card(user))


# ------------------------------------------------------------------ tools and gate


def test_only_get_no_writing_endpoints(server):
    assert_read_only(server.spec)
    assert {y for operations in server.spec["paths"].values() for y in operations} == {"get"}
    with pytest.raises(RuntimeError, match="POST /x"):
        assert_read_only({"paths": {"/x": {"post": {}}}})


def test_tools_are_generated_with_no_role_argument(server):
    definitions = server.tool_definitions()
    assert len(definitions) == 14 and all("_api_v1_" in t["function"]["name"] for t in definitions)
    for t in definitions:
        assert "X-Rol" not in json.dumps(t["function"]["parameters"]) and "x_rol" not in t["function"]["parameters"].get(
            "properties", {})
    assert server.descriptions[INVOICE_LIST] == "Satış ve iade faturalarını listeler"


def test_envelope_same_as_manual_tools_and_scope_in_data_layer(server):
    y, error = call_tool(server, CFO, INVOICE_LIST, {})
    assert not error and set(y) == {"cagri_id", "arac", "tarih", "kapsam", "sonuc", "kaynak"}
    assert y["kapsam"] == "tüm şirket" and y["tarih"] == AS_OF.isoformat() and y["kaynak"] == {}
    all_ids = {r["fatura_id"] for r in y["sonuc"]["kayitlar"]}
    assert "F11" not in all_ids and {"F07", "F08", "F02"} <= all_ids          # an invoice after D is not visible
    y, _ = call_tool(server, EXPORT, INVOICE_LIST, {})
    assert y["kapsam"] == "yalnızca yurt dışı müşteriler"
    assert {r["fatura_id"] for r in y["sonuc"]["kayitlar"]} == {"F07", "F08"}
    y, _ = call_tool(server, DOMESTIC, INVOICE_LIST, {"musteri_adi": "Beta"})
    assert y["sonuc"]["toplam_kayit"] == 0                           # the customer of the other scope does not exist


def test_sales_role_has_no_permission_on_bank_endpoint(server):
    for user in (DOMESTIC, EXPORT):
        y, error = call_tool(server, user, ACCOUNT_LIST, {})
        assert error and y["hata"] == "yetki_yok"
    y, error = call_tool(server, CFO, ACCOUNT_LIST, {})
    assert not error and y["sonuc"]["hesaplar"]


def test_invalid_arguments_are_rejected_at_the_gate(server):
    def message(arg, name=INVOICE_LIST):
        y, error = call_tool(server, CFO, name, arg)
        assert error and y["hata"] == "gecersiz_arguman"
        return y["mesaj"]

    assert "Bilinmeyen argüman" in message({"X-Rol": "cfo"})           # the model cannot send a role
    assert "sayfa_boyutu 1 ile 100 arasında" in message({"sayfa_boyutu": 500})
    assert "sayfa en az 1" in message({"sayfa": 0})
    assert "YYYY-MM-DD" in message({"baslangic": "2025-13-01"})
    assert "satis, iade" in message({"tur": "x"})
    assert "Eksik argüman: para_birimi" in message({}, RATE)
    assert "Müşteri bulunamadı" in message({"musteri_id": "M99"}, CUSTOMER_CARD)  # 404: the API's own message
    y, error = call_tool(server, CFO, INVOICE_LIST, {"sayfa_boyutu": 2.0, "musteri_adi": None})  # 2.0 is an integer, None is ignored
    assert not error and y["sonuc"]["sayfa_boyutu"] == 2


def test_identity_unknown_tool_and_audit_log(server_env):
    server, folder = server_env
    before = len((folder / "denetim.jsonl").read_text(encoding="utf-8").splitlines())
    y, error = server.gate(INVOICE_LIST, {}, "bozuk")
    assert error and y["hata"] == "kimlik_gecersiz"
    y, error = call_tool(server, CFO, "faturalari_sil", {})
    assert error and y["hata"] == "bilinmeyen_arac"
    call_tool(server, DOMESTIC, ACCOUNT_LIST, {})
    rows = [json.loads(s) for s in (folder / "denetim.jsonl").read_text(encoding="utf-8").splitlines()[before:]]
    assert [s["sonuc"] for s in rows] == ["reddedildi"] * 3
    assert rows[2]["neden"] == "api_403" and rows[2]["http"] == 403 and rows[2]["rol"] == "satis_yurtici"


def test_amounts_are_text_and_verifier_counts_them_as_source(server):
    y, _ = call_tool(server, CFO, INVOICE_ONE, {"fatura_id": "F02"})
    assert y["sonuc"]["tutar"] == "1000.00" and y["sonuc"]["tutar_tl"] == "1000.00"
    rate, _ = call_tool(server, CFO, RATE, {"para_birimi": "EUR"})
    assert isinstance(rate["sonuc"]["alis"], str)
    calls = [{"arac": INVOICE_ONE, "argumanlar": {"fatura_id": "F02"}, "hata_mi": False, "yanit": y}]
    assert not verify("F02 faturasının tutarı 1.000,00 TL.", "F02 ne kadar?", calls, AS_OF).unsourced
    assert verify("F02 faturasının tutarı 1.001,00 TL.", "F02 ne kadar?", calls, AS_OF).unsourced


def test_source_identifiers_and_scope_check_negative_control(server):
    """In the generated envelope "kaynak" is empty: the scope check collects the identifiers from the result. A row outside
    the scope must be caught."""
    scope_sets = {"satis_yurtici": {"doviz_kurlari": None, "musteriler": {"M1", "M3", "M4"},
                                 "faturalar": {"F01", "F02", "F03", "F04", "F05", "F06", "F09", "F10", "F11"},
                                 "tahsilatlar": set(), "cekler": set()}}

    def violations_for(user, role, name, arg):
        y, error = call_tool(server, user, name, arg)
        c = {"arac": name, "hata": y.get("hata"), "yanit": {**y, "kaynak": source_identifiers(y.get("sonuc"))}}
        return scope_violations([c], role, scope_sets, {})

    assert violations_for(DOMESTIC, "satis_yurtici", INVOICE_LIST, {}) == []
    assert "faturalar:F07" in violations_for(CFO, "satis_yurtici", INVOICE_LIST, {})      # negative control: the CFO result under a sales role
    bank_violations = violations_for(CFO, "satis_yurtici", ACCOUNT_LIST, {})                    # there is no bank table at all under a sales role
    assert bank_violations and all(x.startswith("banka_hesaplari:") for x in bank_violations)
    assert source_identifiers({"kayitlar": [{"fatura_id": "F06", "iliskili_fatura_id": "F04", "musteri_id": "M1"}]}) == \
        {"faturalar": ["F04", "F06"], "musteriler": ["M1"]}


# ------------------------------------------------------------------ generated oracle


def test_resolve_takes_argument_from_previous_result():
    results = [{"sonuc": {"kayitlar": [{"musteri_id": "M4"}]}}]
    assert resolve({"musteri_id": {"$": [0, "kayitlar", 0, "musteri_id"]}, "x": 1}, results) == \
        {"musteri_id": "M4", "x": 1}


def _make_question(question_id, text, keys):
    return {"soru_id": question_id, "soru": text, "kullanici": CFO, "bolum": "arac",
            "alternatifler": [{"arac": "x", "argumanlar": {}, "anahtarlar": keys}]}


def test_oracle_runs_chain_step_by_step_and_ownership_card_stops(server):
    plan = {"sinif": "zincir", "alternatif": 0,
            "plan": [{"arac": CUSTOMER_LIST, "argumanlar": {"ad": "Delta", "sayfa_boyutu": 1}},
                     {"arac": CUSTOMER_CARD, "argumanlar": {"musteri_id": {"$": [0, "kayitlar", 0, "musteri_id"]}}}],
            "cevap": [{"anahtar": 0, "cagri": 1, "yol": ["cari_bakiye_tl"]}]}
    amount_key = {"ad": "acik", "tur": "tutar", "etiket": "Açık alacak", "birim": "TL", "deger": "0"}
    oracle = GeneratedOracle({"Z1": plan, "Z2": plan, "T1": {"sinif": "toplama"}})
    assistant = Assistant(server, oracle, Settings(arac_listesi_token=2300))
    assistant.tools = server.tool_definitions()

    oracle.soru = _make_question("Z1", "Açık alacağımız ne kadar?", [amount_key])  # question that names no customer: no rule
    turn = assistant.ask(oracle.soru["soru"], card(CFO))
    assert [c["arac"] for c in turn.calls] == [CUSTOMER_LIST, CUSTOMER_CARD]
    assert turn.calls[1]["argumanlar"] == {"musteri_id": "M4"}
    assert turn.status == "tamam" and "Açık alacak:" in turn.answer

    # Question naming the customer: the card has a "musteri" field, the call has no "musteri_adi" -> the ownership rule stops it (engelli = blocked).
    oracle.soru = _make_question("Z2", "Delta Ltd. müşterisinin açık alacağı ne kadar?", [amount_key])
    turn = assistant.ask(oracle.soru["soru"], card(CFO))
    assert turn.status == "uretemedim" and all(d.ownerless for d in turn.verifications)

    oracle.soru = _make_question("T1", "DSO kaç gün?", [])
    turn = assistant.ask(oracle.soru["soru"], card(CFO))
    assert turn.calls == [] and turn.answer == NO_ANSWER


# ------------------------------------------------------------------ frozen files


def test_frozen_classification_is_intact(server):
    import hashlib
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    v = json.loads((root / "reports" / "faz9_siniflar.json").read_text(encoding="utf-8"))
    assert v["ozet"] == hashlib.sha256(json.dumps(v["sorular"], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert v["api_ozeti"] == server.digest()                            # the API and the tool definitions are as they were frozen
    assert v["sayilar"] == {"engelli": 3, "tek_cagri": 34, "toplama": 40, "yok": 6, "zincir": 3}
    assert len(v["sorular"]) == 86
    for k in v["sorular"]:
        if k["sinif"] in ("tek_cagri", "zincir"):
            assert k["kahin"]["dogru"] and k["kahin"]["durum"] == "tamam"
        if k["sinif"] == "engelli":
            assert k["niyet"] == "oz_acik" and all(d["sahipsiz"] for d in k["kahin"]["dogrulama"])
    preregistration = json.loads((root / "reports" / "faz9_on_kayit.json").read_text(encoding="utf-8"))
    assert preregistration["siniflar_ozeti"] == v["ozet"] and preregistration["api_ozeti"] == v["api_ozeti"]
