"""
G2P(철자 → 발음) 변환이 실제로 규칙을 적용하는지에 대한 회귀 테스트.

배경(실측): g2pkk는 내부적으로 mecab(Windows는 eunjeon)을 요구하는데 사전이 함께 설치되지
않아 로드에 실패했고, 그 결과 서버가 조용히 폴백 사전으로 떨어져 있었다. 폴백 사전은 30여 단어뿐이라
대부분의 단어에서 **발음기호가 철자와 똑같이** 나왔다(실측: 실시간 → [실시간], 연음 0건).
게다가 Linux 경로에서는 mecab이 없어도 예외 없이 mecab=None으로 생성돼, 생성 시점엔 멀쩡해 보이고
호출할 때마다 터진다. 그래서 "로드됐는지"가 아니라 "실제로 변환되는지"로 판정해야 한다.
"""

import pytest

from g2p.g2p_client import G2pConverter


@pytest.fixture(scope="module")
def converter():
    return G2pConverter()


def test_g2p_is_not_in_fallback_mode(converter):
    """폴백으로 떨어지면 발음기호가 철자와 같아져 발음 코칭이 사실상 죽는다."""
    assert converter.use_fallback is False, "g2pkk가 로드되지 않아 폴백 사전으로 동작 중이다"


@pytest.mark.parametrize(
    "word, expected",
    [
        ("국물", "궁물"),      # 비음화
        ("학년", "항년"),      # 비음화
        ("신라", "실라"),      # 유음화
        ("좋고", "조코"),      # 격음화
        ("축하", "추카"),      # 격음화
        ("같이", "가치"),      # 구개음화
        ("굳이", "구지"),      # 구개음화
    ],
)
def test_phonological_rules_are_applied(converter, word, expected):
    assert converter.g2p(word) == expected


def test_liaison_in_sentence(converter):
    """연음이 실제로 일어나야 한다(폴백 모드에서는 원문 그대로 나왔다)."""
    assert "바름" in converter.g2p("발음 평가")


def test_works_check_detects_broken_analyzer():
    """mecab이 없어 호출 시 터지는 상태를 '동작함'으로 오판하면 안 된다."""

    class _Broken:
        def __call__(self, text):
            raise AttributeError("'NoneType' object has no attribute 'pos'")

    class _WrongResult:
        def __call__(self, text):
            return text  # 규칙이 하나도 적용되지 않은 결과

    assert G2pConverter._works(_Broken()) is False
    assert G2pConverter._works(_WrongResult()) is False


def test_kiwi_shim_provides_pos_tuples():
    """g2pkk가 쓰는 인터페이스는 pos() 하나뿐이다 — (형태소, 태그) 튜플을 돌려줘야 한다."""
    from g2p.g2p_client import _KiwiMecabShim

    tokens = _KiwiMecabShim().pos("발음을 평가합니다")
    assert tokens and all(isinstance(t, tuple) and len(t) == 2 for t in tokens)
    assert all(isinstance(form, str) and isinstance(tag, str) for form, tag in tokens)
