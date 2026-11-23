"""Unit tests for tool-call validity checking and the selection rule (with fake data)."""

from __future__ import annotations

import pytest

from sayman.model_selection.call_validation import (
    is_tool_call_valid,
    is_expected_call,
    is_response_valid,
    selection_rule,
)


def _validate(name, arguments):
    return is_tool_call_valid({"ad": name, "argumanlar": arguments})


def test_correct_call_is_valid():
    assert _validate("geciken_alacaklar", {"min_gun": 60}) == (True, "geçerli")


def test_tool_without_arguments_is_valid_with_empty_arguments():
    assert _validate("nakit_pozisyonu", {})[0]
    assert _validate("nakit_pozisyonu", None)[0]


def test_arguments_arriving_as_json_text_are_parsed():
    assert _validate("doviz_kuru", '{"para_birimi": "USD"}')[0]


@pytest.mark.parametrize(
    "name, arguments, reason_part",
    [
        ("para_transferi_yap", {}, "bilinmeyen araç"),
        ("geciken_alacaklar", {}, "eksik zorunlu alan"),
        ("geciken_alacaklar", {"min_gun": "60"}, "tamsayı olmalı"),
        ("geciken_alacaklar", {"min_gun": True}, "tamsayı olmalı"),
        ("doviz_kuru", {"para_birimi": "dolar"}, "izin verilmeyen değer"),
        ("doviz_kuru", {"para_birimi": "USD", "banka": "X"}, "tanımsız alan"),
        ("doviz_kuru", {"para_birimi": "USD", "tarih": "12.11.2026"}, "YYYY-MM-DD"),
        ("doviz_kuru", {"para_birimi": "USD", "tarih": "2026-02-30"}, "geçerli bir tarih değil"),
        ("doviz_kuru", "{para_birimi: USD}", "JSON olarak okunamadı"),
        ("doviz_kuru", "[1, 2]", "JSON nesnesi değil"),
    ],
)
def test_invalid_calls_are_caught(name, arguments, reason_part):
    valid, reason = _validate(name, arguments)
    assert not valid
    assert reason_part in reason


def test_no_call_is_invalid():
    assert is_response_valid([]) == (False, "araç çağrısı yok")


def test_one_broken_call_among_several_makes_it_invalid():
    calls = [
        {"ad": "nakit_pozisyonu", "argumanlar": {}},
        {"ad": "geciken_alacaklar", "argumanlar": {}},
    ]
    assert not is_response_valid(calls)[0]


def test_expected_call_checks_tool_and_arguments():
    calls = [{"ad": "geciken_alacaklar", "argumanlar": {"min_gun": 60, "en_fazla": 10}}]
    assert is_expected_call(calls, "geciken_alacaklar", {"min_gun": 60})
    assert not is_expected_call(calls, "geciken_alacaklar", {"min_gun": 30})
    assert not is_expected_call(calls, "alacak_yaslandirma", {})


def _candidate(model, billions, durations, valid_count):
    turns = [{"sure_s": s, "gecerli": i < valid_count} for i, s in enumerate(durations)]
    return {"model": model, "parametre_milyar": billions, "turlar": turns}


def test_largest_model_satisfying_the_rule_is_selected():
    candidates = [
        _candidate("kucuk", 4, [5, 6, 5, 7, 6], 5),
        _candidate("buyuk", 9, [20, 25, 22, 30, 21], 5),
    ]
    assert selection_rule(candidates) == "buyuk"


def test_model_with_one_turn_over_the_threshold_is_eliminated():
    candidates = [
        _candidate("kucuk", 4, [5, 6, 5, 7, 6], 5),
        _candidate("buyuk", 9, [20, 25, 22, 30.5, 21], 5),
    ]
    assert selection_rule(candidates) == "kucuk"


def test_candidate_without_five_valid_in_five_questions_is_eliminated():
    candidates = [
        _candidate("kucuk", 4, [5, 6, 5, 7, 6], 5),
        _candidate("buyuk", 9, [10, 10, 10, 10, 10], 4),
    ]
    assert selection_rule(candidates) == "kucuk"


def test_candidate_with_a_missing_turn_is_eliminated():
    assert selection_rule([_candidate("eksik", 9, [5, 5, 5, 5], 4)]) is None


def test_no_selection_when_nobody_passes():
    candidates = [_candidate("kucuk", 4, [31, 6, 5, 7, 6], 5), _candidate("buyuk", 9, [5, 5, 5, 5, 5], 3)]
    assert selection_rule(candidates) is None
