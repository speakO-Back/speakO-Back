"""철자와 발음을 비교해 어떤 음운 변동인지 판정하고, 단어 목록에 띄울 설명 문구를 만든다.

## 왜 규칙 판정인가 (HCX로 생성하지 않는 이유)
피그마 단어 목록은 카테고리 이름이 아니라 **구체적인 음운 현상 이름 + 설명**을 보여준다
(`특정 › [특쩡]` — "경음화: 받침 뒤에 오는 예사소리가 된소리로 바뀌어 발음됩니다").
그런데 우리 `표기-발음불일치` 카테고리는 비음화·경음화·유음화·구개음화·격음화를 전부
담는 통이라, 카테고리 고정 문구로는 "표기와 발음이 다릅니다" 수준밖에 못 쓴다.

이걸 LLM으로 생성하면 **틀린 음운 설명을 사용자가 그대로 배운다.** 실제로 피그마 시안의
연음 예시가 그 사례다 — 같은 값 `[다섣]`을 두고 "이렇게 읽혀야 하지만 저렇게 발음된다"고
쓰고, '다섯'에 있지도 않은 'ㅎ'과 'ㄹ'을 근거로 든다. 발음 교육 앱에서 이건 치명적이다.

철자와 G2P 발음을 자모로 분해해 비교하면 어떤 변동인지 **결정적으로** 판정할 수 있다.
비용 0, 재현 가능, 테스트로 고정 가능. 판정이 안 되면 일반 문구로 폴백한다.
"""
from utils.hangul_phonology import CHO, JONG, JUNG, decompose_syllable

LENGTH_MARK = "ː"

# 예사소리 → 된소리 (경음화)
_PLAIN = {"ㄱ", "ㄷ", "ㅂ", "ㅅ", "ㅈ"}
_TENSE = {"ㄲ", "ㄸ", "ㅃ", "ㅆ", "ㅉ"}
# 예사소리 → 거센소리 (격음화)
_ASPIRATED = {"ㅋ", "ㅌ", "ㅍ", "ㅊ"}
_NASALS = {"ㄴ", "ㅁ", "ㅇ"}
# 비음화의 입력이 되는 받침(파열음 계열)
_STOPS = {"ㄱ", "ㄲ", "ㅋ", "ㄷ", "ㅅ", "ㅆ", "ㅈ", "ㅊ", "ㅌ", "ㅂ", "ㅍ"}

_DESCRIPTIONS = {
    "구개음화": "구개음화: 받침 ㄷ, ㅌ이 뒤에 오는 '이'를 만나 ㅈ, ㅊ으로 바뀌어 발음됩니다.",
    "격음화": "격음화: ㅎ이 예사소리와 만나 거센소리(ㅋ, ㅌ, ㅍ, ㅊ)로 합쳐져 발음됩니다.",
    "유음화": "유음화: ㄴ과 ㄹ이 만나면 두 소리가 모두 ㄹ로 바뀌어 발음됩니다.",
    "비음화": "비음화: 받침이 뒤따르는 비음(ㄴ, ㅁ)의 영향으로 콧소리(ㅇ, ㄴ, ㅁ)로 바뀌어 발음됩니다.",
    "경음화": "경음화: 받침 뒤에 오는 예사소리(ㄱ, ㄷ, ㅂ, ㅅ, ㅈ)가 된소리로 바뀌어 발음됩니다.",
    "연음": "연음: 앞 음절의 받침이 뒤 음절의 첫소리로 옮겨가 발음됩니다.",
}
_GENERIC_MISMATCH = "표기와 발음이 다릅니다. 소리 나는 대로 읽어보세요."


def strip_brackets(phoneme: str) -> str:
    """'[구성]' → '구성'. G2P 결과는 대괄호로 감싸여 있다."""
    text = (phoneme or "").strip()
    if text.startswith("[") and text.endswith("]"):
        return text[1:-1]
    return text


def apply_length_marks(phoneme: str, positions) -> str:
    """발음기호의 해당 음절 뒤에 장음 기호(ː)를 넣는다. '[구성]' + (0,) → '[구ː성]'.

    이미 ː가 있으면 그대로 둔다(중복 삽입 방지).
    """
    if not positions:
        return phoneme
    body = strip_brackets(phoneme)
    if not body or LENGTH_MARK in body:
        return phoneme

    marked = []
    for index, char in enumerate(body):
        marked.append(char)
        if index in set(positions):
            marked.append(LENGTH_MARK)
    return f"[{''.join(marked)}]"


def _letters(syllable):
    """음절 → (초성, 중성, 종성) 낱자. 한글이 아니면 None."""
    parts = decompose_syllable(syllable)
    if parts is None:
        return None
    cho, jung, jong = parts
    return CHO[cho], JUNG[jung], JONG[jong]


def detect_rule(word: str, phoneme: str):
    """철자와 발음을 비교해 음운 변동 이름을 돌려준다 (판정 불가면 None).

    음절 수가 다르면(축약 등) 신뢰할 수 없으므로 판정하지 않는다 — 억지로 이름을 붙이느니
    일반 문구를 쓰는 편이 낫다.
    """
    spelled = word or ""
    spoken = strip_brackets(phoneme).replace(LENGTH_MARK, "")
    if not spelled or len(spelled) != len(spoken) or spelled == spoken:
        return None

    pairs = []
    for a, b in zip(spelled, spoken):
        left, right = _letters(a), _letters(b)
        if left is None or right is None:
            return None
        pairs.append((left, right))

    # 앞선 규칙일수록 더 구체적인 조건이라 먼저 본다.
    for index, ((cho_a, jung_a, jong_a), (cho_b, _, jong_b)) in enumerate(pairs):
        prev_jong = pairs[index - 1][0][2] if index > 0 else ""

        # 구개음화: 받침 ㄷ/ㅌ + '이' → ㅈ/ㅊ
        if prev_jong in {"ㄷ", "ㅌ"} and cho_a == "ㅇ" and jung_a == "ㅣ" and cho_b in {"ㅈ", "ㅊ"}:
            return "구개음화"

        # 격음화: ㅎ이 예사소리와 합쳐져 거센소리로. 두 방향 다 있다.
        # (1) 받침 ㅎ + 예사소리 초성 — 좋고 → 조코
        if cho_b in _ASPIRATED and cho_a in _PLAIN and (prev_jong == "ㅎ" or "ㅎ" in {jong_a}):
            return "격음화"
        if jong_b in _ASPIRATED and jong_a in _PLAIN:
            return "격음화"
        # (2) 예사소리 받침 + 초성 ㅎ — 축하 → 추카, 역할 → 여칼, 입학 → 이팍, 맏형 → 마텽.
        #     실은 이쪽이 더 흔한데 (1)만 보고 있어서 전부 일반 문구로 떨어졌다(실측 2026-08-06).
        #     앞 음절의 받침이 사라져 있어야 한다 — 합쳐졌다는 증거다.
        prev_jong_spoken = pairs[index - 1][1][2] if index > 0 else ""
        if cho_a == "ㅎ" and cho_b in _ASPIRATED and prev_jong in _PLAIN and not prev_jong_spoken:
            return "격음화"

    for index, ((cho_a, _, jong_a), (cho_b, _, jong_b)) in enumerate(pairs):
        # 유음화: ㄴ↔ㄹ이 맞닿아 둘 다 ㄹ로
        if jong_a == "ㄴ" and jong_b == "ㄹ":
            return "유음화"
        if cho_a == "ㄴ" and cho_b == "ㄹ":
            return "유음화"

    for index, ((cho_a, _, jong_a), (cho_b, _, jong_b)) in enumerate(pairs):
        # 비음화: 파열음 받침이 콧소리로 (국물 → 궁물), 또는 ㄹ이 ㄴ으로 (협력 → 혐녁)
        if jong_a in _STOPS and jong_b in _NASALS:
            return "비음화"
        if cho_a == "ㄹ" and cho_b == "ㄴ":
            return "비음화"

    for (cho_a, _, _), (cho_b, _, _) in pairs:
        # 경음화: 예사소리가 된소리로 (특정 → 특쩡)
        if cho_a in _PLAIN and cho_b in _TENSE:
            return "경음화"

    return None


def describe(word: str, phoneme: str, category: str, long_vowel_positions=()) -> str:
    """단어 목록에 띄울 설명 문구. 카테고리별로 근거가 다르다."""
    if category == "장단음":
        if long_vowel_positions and min(long_vowel_positions) > 0:
            nth = min(long_vowel_positions) + 1
            return f"장단음: 이 단어의 {nth}번째 음절은 길게 발음합니다."
        # 표준 발음법 제6항 — 긴소리는 원칙적으로 단어의 첫음절에서만 나타난다.
        return "장단음: 이 단어의 첫 음절은 길게 발음합니다."

    if category == "연음":
        return _DESCRIPTIONS["연음"]

    if category == "표기-발음불일치":
        rule = detect_rule(word, phoneme)
        return _DESCRIPTIONS.get(rule, _GENERIC_MISMATCH)

    return ""
