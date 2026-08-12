"""발음 주의 단어 추출이 **실행할 때마다 같은 결과**를 내는지 고정한다.

## 무엇이 터졌었나 (2026-08-09 실측)

추출기가 중복 제거에 집합을 쓰고 `list(set(...))`으로 돌려줬다. 파이썬 문자열 해시는
프로세스마다 무작위라(PYTHONHASHSEED) **그 리스트의 순서가 실행할 때마다 달라진다.**
호출자는 외부 사전 API 폭주를 막으려고 `MAX_DIFFICULT_WORDS`(40개)로 **앞에서 자르기**
때문에, 순서가 흔들리면 **잘려나가는 단어가 매번 바뀐다.**

    제로 대본: 후보 144개 → 40개만 남음
    1회차 앞 8개: 책임 본격 핵심 개념 방식 이야기 최소 굴욕
    2회차 앞 8개: 마무리 굴욕 노력 철학자 다양 추구 위드 제공
    3회차 앞 8개: 사회 사고 배경 시대 구성원 수용 모두 자유주의자
    최종 목록도 16개 vs 20개로 갈렸다 (겹치는 단어는 6개뿐)

사용자 눈에는 **"같은 대본을 다시 분석했더니 발음 주의 단어가 딴 게 나온다"** 로 보인다.
하이라이팅 화면과 `highlight.docx`가 매번 달라지므로 시연에서 바로 드러난다.

## 이 파일이 잡는 방식

한 프로세스 안에서 두 번 불러 비교하는 방식으로는 **절대 안 잡힌다** — 해시 시드는
프로세스 안에서 고정이라 `list(set(...))`도 같은 답을 준다. 그래서 순서 자체가
**대본에 처음 나온 순서와 같은지**를 본다. 집합을 쓰면 이 성질이 깨진다.
"""
import re

import pytest

import main
from etri.etri_client import EtriLanguageAnalyzer
from nlp.kiwi_analyzer import KiwiAnalyzer


class _Token:
    def __init__(self, form, tag="NNG"):
        self.form = form
        self.tag = tag


def _analyzer_over(tokens):
    """Kiwi 모델을 안 띄우고 토크나이저 출력만 흉내낸다 (로드가 느리고 환경을 탄다)."""
    analyzer = KiwiAnalyzer.__new__(KiwiAnalyzer)
    analyzer.use_fallback = False
    analyzer.kiwi = type("_K", (), {"tokenize": staticmethod(lambda text: tokens)})()
    return analyzer


# ---------------------------------------------------------------- Kiwi

def test_kiwi_keeps_first_appearance_order():
    """⚠️ 이 파일의 핵심. 집합으로 중복을 지우면 이 순서가 깨진다."""
    forms = ["발표", "구성", "발표", "굴욕", "존엄", "구성", "책임"]
    words = _analyzer_over([_Token(f) for f in forms])

    assert words.extract_difficult_words("무시됨") == ["발표", "구성", "굴욕", "존엄", "책임"]


def test_kiwi_order_is_stable_across_repeated_calls():
    forms = ["시대", "연대", "자유", "시대", "포용", "가치", "책임", "존엄"]
    analyzer = _analyzer_over([_Token(f) for f in forms])

    first = analyzer.extract_difficult_words("무시됨")
    second = analyzer.extract_difficult_words("무시됨")

    assert first == second


def test_kiwi_drops_short_words_and_other_tags_without_losing_order():
    tokens = [
        _Token("발표"), _Token("는", "JX"), _Token("것"),
        _Token("메타버스", "NNP"), _Token("ChatGPT", "SL"), _Token("의", "JKG"),
        _Token("구성"),
    ]
    assert _analyzer_over(tokens).extract_difficult_words("무시됨") == [
        "발표", "메타버스", "ChatGPT", "구성",
    ]


# ---------------------------------------------------------------- ETRI (키가 돌아오면 쓰는 경로)

def test_etri_keeps_first_appearance_order(monkeypatch):
    """ETRI 경로에도 같은 결함이 있었다. 키가 발급되는 순간 되살아나지 않도록 같이 고정한다."""
    payload = {"return_object": {"sentence": [
        {"morp": [{"lemma": w, "type": t} for w, t in [
            ("발표", "NNG"), ("는", "JX"), ("구성", "NNG"), ("것", "NNG"),
        ]]},
        {"morp": [{"lemma": w, "type": t} for w, t in [
            ("발표", "NNG"), ("굴욕", "NNG"), ("SpeaKO", "SL"),
        ]]},
    ]}}

    class _Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return payload

    analyzer = EtriLanguageAnalyzer()
    monkeypatch.setattr(analyzer, "use_fallback", False, raising=False)
    analyzer.access_key = "test-key"
    monkeypatch.setattr("etri.etri_client.requests.post", lambda *a, **k: _Response())
    monkeypatch.setattr("etri.etri_client.log_etri_call", lambda *a, **k: None)

    assert analyzer.extract_difficult_words("무시됨") == ["발표", "구성", "굴욕", "SpeaKO"]


# ---------------------------------------------------------------- 상한과의 상호작용

def test_cap_keeps_the_beginning_of_the_script():
    """상한에 걸릴 때 남는 건 '아무 40개'가 아니라 **앞부분 40개**여야 한다.

    발표자가 먼저 마주치는 단어가 목록에 남는 게 자연스럽고, 무엇보다 **매번 같아야** 한다.
    """
    forms = [f"단어{i:03d}" for i in range(main.MAX_DIFFICULT_WORDS + 30)]
    words = _analyzer_over([_Token(f) for f in forms]).extract_difficult_words("무시됨")

    capped = words[:main.MAX_DIFFICULT_WORDS]
    assert capped[0] == "단어000"
    assert capped[-1] == f"단어{main.MAX_DIFFICULT_WORDS - 1:03d}"


def test_analysis_pipeline_is_deterministic(monkeypatch):
    """추출부터 분류까지 통째로 두 번 돌려 같은 결과가 나오는지 본다."""
    script = "Slide 1: 굴욕과 존엄, 그리고 책임. Slide 2: 굴욕을 최소화하는 연대."

    monkeypatch.setattr(main.etri_analyzer, "extract_difficult_words", lambda text: [])
    monkeypatch.setattr(
        main.kiwi_analyzer, "extract_difficult_words",
        lambda text: ["굴욕", "존엄", "책임", "연대"],
    )
    monkeypatch.setattr(
        main.g2p_converter, "convert_words",
        lambda words: [{"word": w, "phoneme": f"[{w}]", "is_different": True} for w in words],
    )
    monkeypatch.setattr(main.stdict_client, "long_vowel_positions", lambda word: ())

    first, first_summary = main._analyze_difficult_words(script)
    second, second_summary = main._analyze_difficult_words(script)

    assert [w["word"] for w in first] == [w["word"] for w in second]
    assert first_summary == second_summary


def test_slide_labels_do_not_enter_the_word_list():
    """'Slide'가 외국어(SL)로 잡히면 목록 맨 앞을 차지한다. 라벨 제거는 분석 전에 끝나야 한다."""
    script = "Slide 1: 굴욕 Slide 2: 존엄"
    assert "Slide" not in re.sub(r"Slide \d+:", " ", script)
