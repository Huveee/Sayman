"""Tools of the Phase 8 forgetting test: fixed subset, reading the answer, comparison and target."""

import json

import pytest

from sayman.evaluation import forgetting


def _row(link, number, correct=2):
    return {"link": link, "question_number": number, "flores_passage": f"Metin {link}", "question": f"Soru {number}?",
            "mc_answer1": "a", "mc_answer2": "b", "mc_answer3": "c", "mc_answer4": "d",
            "correct_answer_num": str(correct), "dialect": "tur_Latn"}


@pytest.fixture
def source_file(tmp_path):
    p = tmp_path / "tur_Latn.jsonl"
    rows = [_row(f"https://ornek/{i}", 1 + i % 2) for i in range(300)]
    p.write_text("\n".join(json.dumps(s) for s in reversed(rows)) + "\n", encoding="utf-8")
    return p


def test_subset_is_fixed_and_independent_of_file_order(source_file, tmp_path):
    questions = forgetting.read_questions(source_file)
    first_ids = [s["soru_id"] for s in forgetting.subset(questions)]
    assert len(first_ids) == forgetting.QUESTION_COUNT == len(set(first_ids))
    assert first_ids == [s["soru_id"] for s in forgetting.subset(forgetting.read_questions(source_file))]
    reversed_path = tmp_path / "ters.jsonl"
    reversed_path.write_text("\n".join(reversed(source_file.read_text(encoding="utf-8").splitlines())), encoding="utf-8")
    assert first_ids == [s["soru_id"] for s in forgetting.subset(forgetting.read_questions(reversed_path))]
    assert questions[0]["dogru"] == 2 and questions[0]["siklar"] == ["a", "b", "c", "d"]


def test_prompt_text_carries_question_and_numbered_choices(source_file):
    s = forgetting.read_questions(source_file)[0]
    t = forgetting.build_prompt(s)
    assert s["metin"] in t and s["soru"] in t and "1) a\n2) b\n3) c\n4) d" in t
    assert t.endswith("(1, 2, 3 ya da 4).")


@pytest.mark.parametrize("text, expected", [
    ("2", 2), ("Cevap: 3", 3), ("**4**", 4), ("1) ilk şık", 1), ("Doğru seçenek 2'dir.", 2),
    ("", None), ("Bilmiyorum", None), ("12", None), ("5", None), (None, None),
])
def test_answer_number(text, expected):
    assert forgetting.answer_number(text) == expected


def _record(qid, correct, chosen):
    return {"soru_id": qid, "dogru": correct, "secilen": chosen}


def test_summary_and_comparison():
    base = [_record(f"s{i}", 1, 1 if i < 80 else 2) for i in range(100)]          # 80%
    tuned = [_record(f"s{i}", 1, 1 if 4 <= i < 80 else None) for i in range(100)]  # 76%
    assert forgetting.summarize(base) == {"soru": 100, "dogru": 80, "dogruluk": 80.0, "okunamayan": 0}
    assert forgetting.summarize(tuned)["okunamayan"] == 24
    k = forgetting.compare(base, tuned)
    assert k["fark"] == -4.0 and k["yalniz_temel_dogru"] == 4 and k["yalniz_ayarli_dogru"] == 0 and k["gecti"]
    assert k["p"] == 0.125  # 4 questions right only in the base, 0 only in the tuned model: 2 × (1/2)⁴
    worse = [_record(f"s{i}", 1, 1 if 6 <= i < 80 else 2) for i in range(100)]      # 74%, a drop of 6 points
    assert not forgetting.compare(base, worse)["gecti"]


def test_comparison_only_on_common_questions():
    base = [_record("a", 1, 1), _record("b", 1, 2)]
    tuned = [_record("b", 1, 1), _record("c", 1, 1)]
    k = forgetting.compare(base, tuned)
    assert k["soru"] == 1 and k["temel_dogruluk"] == 0.0 and k["ayarli_dogruluk"] == 100.0
    with pytest.raises(ValueError):
        forgetting.compare([_record("a", 1, 1)], [_record("z", 1, 1)])
