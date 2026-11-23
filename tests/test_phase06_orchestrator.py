"""Phase 6: the orchestrator, driven by a fake model with a pre-written scenario (no real model runs).

The tools are real: they are called through the real gate on Phase 3's small hand-computed company (D = 31 March 2025).
If the fake model is called once more after its scenario ends it raises an error, so "the model was not called again"
is tested too.

In every test an invariant is checked: the text shown to the user is either a verified draft or a fixed text.
"""

import copy
import datetime as dt
import json
from decimal import Decimal

import pytest
from test_phase02_warehouse import AS_OF, _hand_built_tables
from test_phase06_document_tool import CHUNKS

from sayman.warehouse.loader import build_warehouse
from sayman.tools.audit_log import AuditLog
from sayman.tools.identity import generate_card
from sayman.tools.server import SaymanServer
from sayman.tools.permissions import read_permissions
from sayman.assistant.verifier import SCOPE_NOTES
from sayman.assistant.orchestrator import (FIXED_TEXTS, SYSTEM_PROMPT, Assistant, Settings, ModelResponse, ModelTimeout,
                                        tool_definitions)
from sayman.documents.search import Searcher
from sayman.data.table_io import write_csv
from sayman.data.clock import Clock

SIGNING_KEY = bytes(range(32))
NOW_EPOCH = 1_750_000_000.0
CFO, DOMESTIC = "selin.aydin", "murat.kaya"
CASH_ANSWER = "Toplam nakit 12.396,40 TL."  # hand-built data: CFO, 31 March 2025


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    folder = tmp_path_factory.mktemp("faz6_ork")
    write_csv(_hand_built_tables(), folder / "csv")
    build_warehouse(folder / "csv", folder / "sayman.duckdb").close()
    s = SaymanServer(warehouse=folder / "sayman.duckdb", clock=lambda: Clock(AS_OF, dt.date(2025, 6, 30)), key=SIGNING_KEY,
                       permissions=read_permissions(), audit_log=AuditLog(), scope_dir=folder / "kapsam",
                       time_fn=lambda: NOW_EPOCH, belge=Searcher(CHUNKS), search_method="bm25")
    yield s
    s.close()


def card(user):
    return generate_card(user, SIGNING_KEY, 3600, NOW_EPOCH)


class FakeModel:
    """Scenario: one entry per model call: a ModelResponse, a function that builds the answer from the messages, or an
    exception to raise."""

    def __init__(self, *scenario, clock=None, delay=0.0):
        self.scenario, self.clock, self.delay = list(scenario), clock, delay
        self.calls: list[list[dict]] = []
        self.timeouts: list[float] = []

    def __call__(self, messages, tools, timeout):
        assert tools == tool_definitions()
        if not self.scenario:
            raise AssertionError("senaryo bitti ama model bir daha çağrıldı")
        self.calls.append(copy.deepcopy(messages))
        self.timeouts.append(timeout)
        if self.clock is not None:
            self.clock.t += self.delay
        step = self.scenario.pop(0)
        if isinstance(step, Exception):
            raise step
        return step(messages) if callable(step) else step


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def tool_call(name, **args):
    return ModelResponse(tool_calls=[(name, args)], prompt_tokens=100, generated_tokens=10)


def reply(text, **extra):
    return ModelResponse(content=text, **extra)


def tr(amount: str) -> str:
    """Tool format "6745.00" -> Turkish "6.745,00"."""
    d = Decimal(amount)
    full, fraction = f"{abs(d):.2f}".split(".")
    return ("-" if d < 0 else "") + f"{int(full):,}".replace(",", ".") + "," + fraction


def last_tool_result(messages):
    return json.loads(next(m for m in reversed(messages) if m["role"] == "tool")["content"])


def run_turn(server, model, user=CFO, question="Bankalarda ne kadar nakit var?", settings=Settings(), clock=None, k=None):
    turn_log = AuditLog()
    assistant = Assistant(server, model, settings, **({"clock": clock} if clock else {}), audit_log=turn_log)
    before = len(server.audit_log.rows)
    turn = assistant.ask(question, card(user) if k is None else k)
    _assert_invariant(turn)
    [turn_row] = turn_log.rows
    assert turn_row["durum"] == turn.status and turn_row["tur_id"] == turn.turn_id
    return turn, server.audit_log.rows[before:], turn_row


def _assert_invariant(turn):
    if turn.status == "tamam" and turn.scope_note:  # Phase 8b: a verified draft + the fixed scope note
        assert any(d.gecerli and d.missing_scope and turn.answer == t.strip() + "\n\n" + SCOPE_NOTES[d.missing_scope]
                   for t, d in zip(turn.drafts, turn.verifications))
    elif turn.status == "tamam":
        assert turn.verifications[-1].gecerli and turn.answer == turn.drafts[-1].strip()
    else:
        assert turn.answer in FIXED_TEXTS.values()
        for draft in turn.drafts:
            assert not draft.strip() or draft.strip() not in turn.answer


# ---------------------------------------------------------------- tamam (done)


def test_ok_tool_call_then_verified_answer(server):
    model = FakeModel(tool_call("nakit_pozisyonu"), reply(CASH_ANSWER))
    turn, tool_rows, turn_row = run_turn(server, model)
    assert (turn.status, turn.answer, turn.model_calls) == ("tamam", CASH_ANSWER, 2)
    assert [(r["arac"], r["kullanici"], r["sonuc"]) for r in tool_rows] == [("nakit_pozisyonu", CFO, "tamam")]
    assert turn_row["cagri_idleri"] == [tool_rows[0]["cagri_id"]] and turn_row["kullanici"] == CFO
    second = model.calls[1]
    assert [m["role"] for m in second] == ["system", "user", "assistant", "tool"]
    assert AS_OF.isoformat() in second[0]["content"]
    assert last_tool_result(second)["sonuc"]["toplam_tl"] == "12396.40"


def test_identity_card_never_enters_messages(server):
    k = card(CFO)
    model = FakeModel(tool_call("nakit_pozisyonu"), reply(CASH_ANSWER))
    run_turn(server, model, k=k)
    text = json.dumps(model.calls, ensure_ascii=False)
    assert k not in text and k.split(".")[1] not in text


def test_tool_error_is_returned_to_model(server):
    """A schema error is not a "ret" (refusal): the model sees the error and corrects it."""
    model = FakeModel(tool_call("nakit_pozisyonu", tarih="dün"), tool_call("nakit_pozisyonu"), reply(CASH_ANSWER))
    turn, tool_rows, _ = run_turn(server, model)
    assert turn.status == "tamam" and turn.model_calls == 3
    assert [r["sonuc"] for r in tool_rows] == ["reddedildi", "tamam"]
    assert last_tool_result(model.calls[1])["hata"] == "gecersiz_arguman"


def test_document_citation_is_verified(server):
    def cite(messages):
        [p] = [p for p in last_tool_result(messages)["sonuc"]["sonuclar"] if p["parca_id"] == "SOZ-M1#m3"]
        return reply(f'Alfa Ltd. 30 gün içinde öder: "faturayı 30 gün içinde öder" [{p["parca_id"]}]')

    model = FakeModel(tool_call("belge_ara", sorgu="Alfa Ltd. ödeme vadesi"), cite)
    turn, _, _ = run_turn(server, model, question="Alfa Ltd. faturayı kaç günde öder?")
    assert turn.status == "tamam" and [a.reason for a in turn.verifications[-1].citations] == ["tamam"]


# ---------------------------------------------------------------- ret (refusal)


def test_refusal_unauthorized_tool_model_is_not_called_again(server):
    model = FakeModel(tool_call("nakit_pozisyonu"))  # if there is a second call, FakeModel raises an error
    turn, tool_rows, turn_row = run_turn(server, model, user=DOMESTIC)
    assert (turn.status, turn.answer, turn.model_calls) == ("ret", "Bu bilgiyi görme yetkiniz yok.", 1)
    assert [(r["arac"], r["sonuc"], r["neden"]) for r in tool_rows] == \
        [("nakit_pozisyonu", "reddedildi", "yetki_yok")]
    assert turn_row["neden"] == "yetki_yok"


def test_refusal_invalid_identity_model_is_never_called(server):
    model = FakeModel()
    turn, tool_rows, turn_row = run_turn(server, model, k="bozuk.kart")
    assert (turn.status, turn.answer, turn.model_calls) == ("ret", "Kimlik doğrulanamadı.", 0)
    assert tool_rows == [] and turn_row["neden"] == "imza_gecersiz" and turn_row["kullanici"] is None


def test_refusal_card_expiring_before_turn_ends_does_not_start(server):
    short_card = generate_card(CFO, SIGNING_KEY, 200, NOW_EPOCH)  # valid for 200 s; the turn lasts 300 s
    turn, _, turn_row = run_turn(server, FakeModel(), k=short_card)
    assert turn.status == "ret" and turn_row["neden"] == "kart_suresi_dolmus"


def test_one_refusal_in_mixed_step_does_not_end_the_loop(server):
    def respond(messages):
        return reply(f"Açık alacak toplamı {tr(last_tool_result(messages)['sonuc']['toplam_tl'])} TL.")

    model = FakeModel(ModelResponse(tool_calls=[("nakit_pozisyonu", {}), ("alacak_yaslandirma", {})]), respond)
    turn, tool_rows, _ = run_turn(server, model, user=DOMESTIC, question="Nakit ve alacak durumu?")
    assert turn.status == "tamam"
    assert [(r["arac"], r["sonuc"]) for r in tool_rows] == \
        [("nakit_pozisyonu", "reddedildi"), ("alacak_yaslandirma", "tamam")]


# ------------------------------------------------------------------ correction and "cevap üretemedim" (cannot answer)


def test_correct_answer_after_correction(server):
    model = FakeModel(tool_call("nakit_pozisyonu"), reply("Toplam nakit 12.936,40 TL."), reply(CASH_ANSWER))
    turn, _, _ = run_turn(server, model)
    assert (turn.status, turn.answer, len(turn.drafts)) == ("tamam", CASH_ANSWER, 2)
    correction = model.calls[2][-1]
    assert correction["role"] == "user" and "12.936,40" in correction["content"]
    assert model.calls[2][-2] == {"role": "assistant", "content": "Toplam nakit 12.936,40 TL."}


def test_cannot_answer_when_second_draft_is_also_wrong(server):
    model = FakeModel(tool_call("nakit_pozisyonu"), reply("Toplam nakit 12.936,40 TL."),
                       reply("Toplam nakit yaklaşık 13 milyon TL."))
    turn, _, turn_row = run_turn(server, model)
    assert (turn.status, turn.answer) == ("uretemedim", FIXED_TEXTS["uretemedim"])
    assert "cevap üretemedim" in turn.answer and "12.936" not in turn.answer and "13 milyon" not in turn.answer
    assert turn_row["kaynaksiz"] == ["13 milyon"] and turn_row["taslak_sayisi"] == 2


def test_fabricated_citation_not_corrected_gives_cannot_answer(server):
    model = FakeModel(tool_call("belge_ara", sorgu="Alfa Ltd. ödeme vadesi"),
                       reply('Vade 30 gün: "alıcı faturayı en geç 45 gün içinde öder" [SOZ-M1#m3]'),
                       reply('Vade 30 gün: "gecikmede aylık faiz uygulanır" [SOZ-M1#m9]'))
    turn, _, turn_row = run_turn(server, model, question="Alfa Ltd. vadesi?")
    assert turn.status == "uretemedim"
    assert turn_row["gecersiz_alinti"] == ["bilinmeyen_parca"]
    assert "parçanın metninde aynen geçmiyor" in model.calls[2][-1]["content"]


def test_truncated_answer_counts_as_unverified(server):
    model = FakeModel(tool_call("nakit_pozisyonu"), reply("Toplam nakit 12.396,40 TL; ayrıca", truncated=True),
                       reply(CASH_ANSWER))
    turn, _, _ = run_turn(server, model)
    assert turn.status == "tamam" and turn.answer == CASH_ANSWER
    assert "yarıda kesildi" in model.calls[2][-1]["content"]


def test_empty_answer_twice_gives_cannot_answer(server):
    turn, _, _ = run_turn(server, FakeModel(reply(""), reply("  ")))
    assert turn.status == "uretemedim"


# ---------------------------------------------------------------- limits


def test_step_limit(server):
    model = FakeModel(tool_call("doviz_kuru", para_birimi="USD"), tool_call("doviz_kuru", para_birimi="EUR"),
                       tool_call("doviz_kuru", para_birimi="GBP"))
    turn, tool_rows, turn_row = run_turn(server, model, settings=Settings(en_cok_adim=3))
    assert (turn.status, turn.answer, turn.model_calls) == ("adim_siniri", FIXED_TEXTS["adim_siniri"], 3)
    assert len(tool_rows) == 3 and turn_row["neden"] == "model_cagrisi"


def test_step_limit_when_no_step_left_for_correction(server):
    model = FakeModel(tool_call("nakit_pozisyonu"), reply("Toplam nakit 12.936,40 TL."))
    turn, _, _ = run_turn(server, model, settings=Settings(en_cok_adim=2))
    assert turn.status == "adim_siniri" and "12.936" not in turn.answer


def test_tool_call_limit_exceeded_runs_none(server):
    three_calls = ModelResponse(tool_calls=[("doviz_kuru", {"para_birimi": p}) for p in ("USD", "EUR", "GBP")])
    turn, tool_rows, turn_row = run_turn(server, FakeModel(three_calls), settings=Settings(en_cok_cagri=2))
    assert turn.status == "adim_siniri" and turn_row["neden"] == "arac_cagrisi" and tool_rows == []


def test_duration_limit_after_model_call(server):
    clock = FakeClock()
    model = FakeModel(tool_call("nakit_pozisyonu"), reply(CASH_ANSWER), clock=clock, delay=170.0)
    turn, _, _ = run_turn(server, model, clock=clock)
    assert (turn.status, turn.answer, turn.model_calls) == ("sure_siniri", FIXED_TEXTS["sure_siniri"], 2)
    assert model.timeouts == [pytest.approx(300.0), pytest.approx(130.0)]  # the remaining time becomes the timeout


def test_duration_limit_model_timeout(server):
    model = FakeModel(tool_call("nakit_pozisyonu"), ModelTimeout("okuma zaman aşımı"))
    turn, _, _ = run_turn(server, model)
    assert turn.status == "sure_siniri" and turn.answer == FIXED_TEXTS["sure_siniri"]


def test_duration_limit_before_call(server):
    clock = FakeClock()
    model = FakeModel(tool_call("nakit_pozisyonu"), clock=clock, delay=0.0)

    def slow_gate(name, arg, k, _original=server.gate):
        clock.t += 500  # the tool took a long time
        return _original(name, arg, k)

    server.gate = slow_gate
    try:
        turn, _, _ = run_turn(server, model, clock=clock)
    finally:
        del server.gate
    assert turn.status == "sure_siniri" and turn.model_calls == 1


def test_model_error(server):
    turn, _, turn_row = run_turn(server, FakeModel(ConnectionError("Ollama kapalı")))
    assert (turn.status, turn.answer) == ("model_hatasi", FIXED_TEXTS["model_hatasi"])
    assert turn_row["neden"].startswith("ConnectionError")


def test_context_limit_after_large_tool_result(server):
    first = [{"role": "system", "content": SYSTEM_PROMPT.format(bugun=AS_OF.isoformat())},
           {"role": "user", "content": "Alacaklar?"}]
    limit = Assistant(server, None, Settings(arac_listesi_token=0))._estimate_tokens(first) + 50
    model = FakeModel(ModelResponse(tool_calls=[("alacak_yaslandirma", {})]))  # the prompt token count is not reported
    turn, tool_rows, _ = run_turn(server, model, question="Alacaklar?",
                                      settings=Settings(arac_listesi_token=0, cevap_payi=0, baglam_token=limit))
    assert (turn.status, turn.model_calls) == ("baglam_siniri", 1) and len(tool_rows) == 1


def test_context_estimate_adds_only_new_messages_on_top_of_reported_count(server):
    """In the smoke test (question 9) the character estimate over all messages overcounted the real prompt by about 2,000
    tokens. If the model reports its prompt token count, only the messages added afterwards are estimated."""
    assistant = Assistant(server, None)
    messages = [{"role": "system", "content": "x" * 1700}, {"role": "user", "content": "y" * 170}]
    new_message = {"role": "tool", "tool_name": "nakit_pozisyonu", "content": "z" * 1700}
    full = assistant._estimate_tokens(messages + [new_message])
    incremental = assistant._estimate_tokens(messages + [new_message], known=(3000, 2))
    assert incremental == 3000 + int(len(json.dumps(new_message)) / 1.7)
    assert full == 2400 + int(sum(len(json.dumps(m)) for m in messages + [new_message]) / 1.7)

    # If the reported count is close to the window, the new tool result does not fit: the model is not called again.
    model = FakeModel(ModelResponse(tool_calls=[("alacak_yaslandirma", {})], prompt_tokens=7600, generated_tokens=20))
    turn, _, _ = run_turn(server, model, question="Alacaklar?")
    assert (turn.status, turn.model_calls) == ("baglam_siniri", 1)


def test_context_limit_from_prompt_tokens_reported_by_model(server):
    """Even if the character estimate does not catch it, the answer is not used when the model reports a full window."""
    model = FakeModel(tool_call("nakit_pozisyonu"), reply(CASH_ANSWER, prompt_tokens=7900))
    turn, _, turn_row = run_turn(server, model)
    assert (turn.status, turn_row["neden"], turn.drafts) == ("baglam_siniri", "istem_token", [])


def test_default_duration_from_phase0_speed():
    assert Settings().sure_s == 300.0


def test_longest_document_result_fits_context_estimate(server):
    """The longest measured document result (3 articles × 2,500 characters = 8,224 characters, actually 2,580 tokens) fits the
    window after the first model call (reported ~2,776 tokens); it would not fit if estimated with the JSON ratio (1.7)."""
    document = {"role": "tool", "tool_name": "belge_ara", "content": "ç" * 8224}
    call_message = {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "belge_ara",
                                                                             "arguments": {"sorgu": "defter"}}}]}
    messages = [{"role": "system", "content": "x"}, {"role": "user", "content": "y"}, call_message, document]
    a = Settings()
    limit = a.baglam_token - a.cevap_payi
    assert Assistant(server, None)._estimate_tokens(messages, known=(2776, 2)) < limit
    json_like = Assistant(server, None, Settings(belge_karakter_token=a.karakter_token))
    assert json_like._estimate_tokens(messages, known=(2776, 2)) > limit

def test_correction_is_requested_when_document_answer_has_no_citation(server):
    """Phase 7 decision: if document search returned results, the answer must contain at least one valid citation."""
    model = FakeModel(tool_call("belge_ara", sorgu="Alfa Ltd. ödeme vadesi"),
                       reply("Alfa Ltd. faturayı 30 gün içinde öder."),
                       reply('Alfa Ltd. 30 gün içinde öder: "faturayı 30 gün içinde öder" [SOZ-M1#m3]'))
    turn, _, turn_row = run_turn(server, model, question="Alfa Ltd. faturayı kaç günde öder?")
    assert turn.status == "tamam" and len(turn.drafts) == 2
    assert turn.verifications[0].citation_missing and not turn.verifications[0].gecerli
    assert "belge_ara kullandın ama cevapta geçerli bir alıntı yok" in model.calls[2][-1]["content"]


def test_system_prompt_short_answer_and_citation_rule():
    assert "en çok 120 kelime" in SYSTEM_PROMPT and "en az bir alıntı olmalı" in SYSTEM_PROMPT
