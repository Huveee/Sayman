"""Phase 8 training data: leakage check (does it catch deliberately added leakage), the teacher, the completion format,
the rules of the training templates and (if present) the consistency of the generated data."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

import support.phase07_questions as f7
import support.phase08_training_questions as e8
from sayman.assistant.orchestrator import ModelResponse
from sayman.training import leakage
from sayman.training.prompt import END_TOKEN, RecordingModel, tool_call_text, completion
from sayman.training.teacher import NoAnswer, Teacher, document_answer

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "egitim" / "veri"


# ------------------------------------------------------------------ leakage check


def _evaluation_set() -> leakage.EvaluationData:
    return leakage.EvaluationData(
        patterns=["{musteri} için toplam açık alacağımız ne kadar?", "Bugün {pb} alış kuru kaç?"],
        questions=["Vadi Mavi Dekorasyon için toplam açık alacağımız ne kadar?", "Bugün USD alış kuru kaç?"],
        identifiers={"M00227", "F010098"}, names={leakage.normalize("Vadi Mavi Dekorasyon Ltd. Şti.")},
        article_texts={"Vadesi 7 günü geçen alacak için müşteriye hatırlatma e-postası gönderilir."},
        rates={("USD", "48.1745")})


def _clean_question(**extra) -> dict:
    return {"soru_id": "E-1", "soru": "Koru Cam firmasının henüz kapanmamış kaç faturası bulunuyor?",
            "kalip": "{musteri} firmasının henüz kapanmamış kaç faturası bulunuyor?", **extra}


def test_clean_training_question_passes():
    r = leakage.audit([_clean_question(varliklar=["M09999"], adlar=["Koru Cam"])], _evaluation_set())
    assert r.clean, r.violations


@pytest.mark.parametrize("extra, kind", [
    ({"kalip": "{musteri} için toplam açık alacağımız ne kadar?"}, "kalip"),           # same skeleton
    ({"kalip": "{Musteri} için toplam açık alacağımız ne kadar acaba?"}, "kalip"),     # very similar skeleton
    ({"soru": "Vadi Mavi Dekorasyon için toplam açık alacağımız ne kadardır?"}, "soru"),
    ({"varliklar": ["F010098"]}, "kimlik"),
    ({"adlar": ["VADİ MAVİ DEKORASYON LTD ŞTİ"]}, "ad"),                               # upper case, no full stop
    ({"hedef_metin": "Vadesi 7 günü geçen alacak için müşteriye hatırlatma e-postası gönderilir."}, "madde"),
    ({"kur": ["USD", "48.1745"]}, "kur"),
])
def test_deliberately_added_leakage_is_caught(extra, kind):
    r = leakage.audit([_clean_question(**extra)], _evaluation_set())
    assert not r.clean
    assert r.violations[kind], r.violations


def test_skeleton_masks_variables_and_lowercases_turkish():
    assert leakage.skeleton("{Donem} İçin DSO kaç gün?") == "<v> için dso kaç gün"
    assert leakage.jaccard("a b c", "a b d") == pytest.approx(0.5)


def test_thresholds_are_values_set_before_the_check():
    assert leakage.PATTERN_THRESHOLD == 0.6 and leakage.QUESTION_THRESHOLD == 0.8


# ------------------------------------------------------------------ completion format


def test_tool_call_completion_xml_and_end():
    t = completion(ModelResponse(tool_calls=[("musteri_ozeti", {"musteri_adi": "Koru Cam"})]))
    assert t == ("<tool_call>\n<function=musteri_ozeti>\n<parameter=musteri_adi>\nKoru Cam\n</parameter>\n"
                 "</function>\n</tool_call>" + END_TOKEN)


def test_completion_without_arguments_and_with_many_calls():
    assert completion(ModelResponse(tool_calls=[("nakit_pozisyonu", {})])) == \
        "<tool_call>\n<function=nakit_pozisyonu>\n</function>\n</tool_call>" + END_TOKEN
    two = tool_call_text([("a", {"n": 3}), ("b", {})])
    assert two == "<tool_call>\n<function=a>\n<parameter=n>\n3\n</parameter>\n</function>\n</tool_call>" \
                  "\n<tool_call>\n<function=b>\n</function>\n</tool_call>"


def test_text_answer_completion():
    assert completion(ModelResponse(content="  Toplam 5 TL.\n")) == "Toplam 5 TL." + END_TOKEN


def test_recording_model_keeps_copy_of_messages_at_that_moment():
    class FakeModel:
        soru = None

        def __call__(self, m, a, z):
            return ModelResponse(content="x")
    k = RecordingModel(FakeModel())
    messages = [{"role": "user", "content": "a"}]
    k(messages, [], 1)
    messages.append({"role": "assistant", "content": "b"})
    assert len(k.drain()[0]["mesajlar"]) == 1
    k.soru = {"x": 1}
    assert k.model.soru == {"x": 1}


# ------------------------------------------------------------------ teacher


def _messages(question="soru", results=()):
    m = [{"role": "system", "content": "s"}, {"role": "user", "content": question}]
    if results:
        m.append({"role": "assistant", "content": "", "tool_calls": []})
        m += [{"role": "tool", "content": json.dumps(r, ensure_ascii=False)} for r in results]
    return m


def test_teacher_first_step_is_independent_of_role():
    teacher = Teacher()
    answers = []
    for role in ("cfo", "satis_yurtici"):
        teacher.soru = {"soru_id": "E-1", "bolum": "yetkisiz", "rol": role, "cagri": ["nakit_pozisyonu", {}]}
        answers.append(teacher(_messages(), [], 1).tool_calls)
    assert answers[0] == answers[1] == [("nakit_pozisyonu", {})]


def test_teacher_answers_tool_result_and_rejects_correction_turn():
    teacher = Teacher()
    teacher.soru = {"soru_id": "E-2", "bolum": "arac", "cagri": ["x", {}], "cevap": "Toplam 5 TL."}
    assert teacher(_messages(results=[{"arac": "x", "sonuc": {"t": "5"}}]), [], 1).content == "Toplam 5 TL."
    correction = _messages(results=[{"arac": "x", "sonuc": {}}]) + [{"role": "assistant", "content": "y"},
                                                                    {"role": "user", "content": "düzelt"}]
    with pytest.raises(NoAnswer):
        teacher(correction, [], 1)


def _document_question():
    return {"soru_id": "E-belge-1", "bolum": "belge", "dogru_parca": "SOZ-S001#m3", "deger": "90 gün",
            "anahtarlar": [{"tur": "sayi", "deger": "90"}], "konu_cumlesi": "Koru Cam sözleşmesine göre ödeme vadesi",
            "ikinci_sorgu": "Koru Cam ödeme vadesi", "cagri": ["belge_ara", {"sorgu": "q"}]}


def _document_result(*chunks):
    return {"arac": "belge_ara", "sonuc": {"sonuclar": [{"parca_id": p, "metin": m} for p, m in chunks]}}


def test_teacher_cites_when_correct_article_arrives():
    teacher = Teacher()
    teacher.soru = _document_question()
    text = "ŞİRKET, faturaları fatura tarihinden itibaren 90 gün içinde banka havalesiyle öder."
    c = teacher(_messages(results=[_document_result(("SOZ-S002#m3", "başka"), ("SOZ-S001#m3", text))]), [], 1)
    assert c.content.startswith("Koru Cam sözleşmesine göre ödeme vadesi: 90 gün. \"")
    assert c.content.endswith("[SOZ-S001#m3]") and "90 gün içinde" in c.content


def test_teacher_searches_once_more_when_document_is_missing_then_gives_up():
    teacher = Teacher()
    teacher.soru = _document_question()
    first = _messages(results=[_document_result(("SOZ-S002#m3", "başka madde metni burada"))])
    assert teacher(first, [], 1).tool_calls == [("belge_ara", {"sorgu": "Koru Cam ödeme vadesi"})]
    two = _messages(results=[_document_result(("SOZ-S002#m3", "a b c d")), _document_result(("SOZ-S009#m1", "e f g h"))])
    with pytest.raises(NoAnswer):
        teacher(two, [], 1)


def test_document_answer_writes_no_summary_when_digit_is_not_in_article():
    s = {**_document_question(), "deger": "12 ay", "anahtarlar": [{"tur": "sayi", "deger": "12"}]}
    c = document_answer(s, "Taahhüt süresi on iki aydır ve uzatılabilir.", "SOZ-S001#m3")
    assert "12" not in c and c.startswith("Koru Cam sözleşmesine göre ödeme vadesi maddede şöyle geçiyor:")


def test_teacher_says_not_found_for_other_regions_customer():
    teacher = Teacher()
    teacher.soru = {"soru_id": "E-3", "bolum": "yetkisiz", "tur": "baska_musteri", "cagri": ["musteri_ozeti", {}],
                "cevap": "Bulamadım."}
    c = teacher(_messages(results=[{"arac": "musteri_ozeti", "sonuc": {"bulunamadi": True}}]), [], 1)
    assert c.content == "Bulamadım."
    with pytest.raises(NoAnswer):  # if the customer was found (the scope leaked), it is not taken into training
        teacher(_messages(results=[{"arac": "musteri_ozeti", "sonuc": {"acik_alacak_tl": "5"}}]), [], 1)


# ------------------------------------------------------------------ template rules


def test_excluded_intents_absent_from_training_templates_and_present_in_phase7():
    training = {template.name for template in e8.TEMPLATES}
    phase7 = {template.name for template in f7.TEMPLATES}
    assert not training & set(e8.EXCLUDED_INTENTS)
    assert set(e8.EXCLUDED_INTENTS) <= phase7


def test_training_days_exclude_evaluation_day():
    assert e8.EVALUATION_DAY == dt.date(2026, 9, 1)
    assert e8.EVALUATION_DAY not in e8.TRAINING_DAYS


def test_training_templates_differ_from_phase7_templates():
    phase7 = {leakage.skeleton(k) for template in f7.TEMPLATES for k in template.patterns}
    for template in e8.TEMPLATES:
        for k in template.patterns:
            assert leakage.skeleton(k) not in phase7, k


def test_fill_answer_negative_amount_and_missing_key():
    a = [{"ad": "net", "tur": "tutar", "deger": "-1234.5", "birim": "TL"}]
    assert e8.fill_answer("{n} haftalık tahminde ilk haftanın neti [net].", {"n": 3}, a) == \
        "3 haftalık tahminde ilk haftanın neti -1.234,50 TL."
    with pytest.raises(KeyError):
        e8.fill_answer("[yok] TL", {}, a)


def test_every_templates_answers_use_their_keys():
    for template in e8.TEMPLATES:
        for c in template.answer_templates:
            assert "[" in c and "]" in c, template.name


# ------------------------------------------------------------------ generated data (if present)


def _read_data():
    if not (DATA / "egitim.jsonl").exists():
        pytest.skip("eğitim verisi henüz üretilmedi (scripts/phase08_training_data.py)")
    return {name: [json.loads(x) for x in (DATA / f"{name}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
            for name in ("egitim", "dogrulama")}


def test_same_prompt_same_completion():
    data = _read_data()
    seen = {}
    for o in data["egitim"] + data["dogrulama"]:
        assert seen.setdefault(o["istem"], o["tamamlama"]) == o["tamamlama"], o["soru_id"]


def test_completions_end_with_end_marker_and_prompts_with_assistant_header():
    for rows in _read_data().values():
        for o in rows:
            assert o["tamamlama"].endswith(END_TOKEN) and o["tamamlama"].count(END_TOKEN) == 1
            assert o["istem"].endswith("<|im_start|>assistant\n<think>\n\n</think>\n\n")


def test_training_and_validation_questions_are_disjoint():
    data = _read_data()
    assert not {o["soru_id"] for o in data["egitim"]} & {o["soru_id"] for o in data["dogrulama"]}
