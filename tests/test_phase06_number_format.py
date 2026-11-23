"""English-format numbers in a draft: rewritten to Turkish format before verification.

Found in the demo on 23 November 2026: for "önümüzdeki ay durumumuz nasıl olur?" the model took the right weekly figures
from `nakit_akis_tahmini` but wrote them as "+8,851,941.82 TL". The verifier reads "8,851" as a Turkish decimal, so
every draft failed. Only unambiguous English formats are rewritten; the model's message history keeps the raw draft.
"""
import pytest
from test_phase06_orchestrator import CASH_ANSWER, FakeModel, reply, run_turn, server, tool_call  # noqa: F401

from sayman.assistant.orchestrator import to_turkish_number_format


@pytest.mark.parametrize("raw, expected", [
    ("1. hafta +8,851,941.82 TL", "1. hafta +8.851.941,82 TL"),
    ("4. hafta -12,973,882.43 TL.", "4. hafta -12.973.882,43 TL."),
    ("Toplam nakit 12,396.40 TL.", "Toplam nakit 12.396,40 TL."),
    ("Toplam 5,000,000 TL", "Toplam 5.000.000 TL"),
    ("Kur 1,234.5678", "Kur 1.234,5678"),
    ("+8,851,941.82 TL, +5,786,496.72 TL", "+8.851.941,82 TL, +5.786.496,72 TL"),
])
def test_unambiguous_english_numbers_become_turkish(raw, expected):
    assert to_turkish_number_format(raw) == expected
    assert to_turkish_number_format(expected) == expected


@pytest.mark.parametrize("text", [
    "Banka hesaplarımızın toplamı 75.826.754,65 TL.",
    "Oran 3,294 puan.",
    "Faiz %0,5; vade 2026-09-01.",
    "Toplam 8851941.82 TL",
    "75,8 milyon TL",
    "Çekler: C025774, C031470, C032144",
    "1,234,5678",
    "Bekleyen 12 çek, 3 fatura.",
])
def test_other_numbers_are_left_alone(text):
    assert to_turkish_number_format(text) == text


def test_english_format_answer_is_verified_and_shown_in_turkish(server):
    model = FakeModel(tool_call("nakit_pozisyonu"), reply("Toplam nakit 12,396.40 TL."))
    turn, _, _ = run_turn(server, model)
    assert (turn.status, turn.answer) == ("tamam", CASH_ANSWER)


def test_model_history_keeps_the_raw_draft(server):
    wrong = "Toplam nakit 99,999,999.00 TL."
    model = FakeModel(tool_call("nakit_pozisyonu"), reply(wrong), reply(CASH_ANSWER))
    turn, _, _ = run_turn(server, model)
    assert (turn.status, turn.answer) == ("tamam", CASH_ANSWER)
    assert turn.drafts[0] == "Toplam nakit 99.999.999,00 TL."
    third = model.calls[2]
    assert third[-2] == {"role": "assistant", "content": wrong}
    assert third[-1]["role"] == "user" and "99.999.999,00" in third[-1]["content"]
