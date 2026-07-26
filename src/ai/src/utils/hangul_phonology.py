HANGUL_BASE = 0xAC00
HANGUL_LAST = 0xD7A3

CHO = ["ㄱ", "ㄲ", "ㄴ", "ㄷ", "ㄸ", "ㄹ", "ㅁ", "ㅂ", "ㅃ", "ㅅ", "ㅆ", "ㅇ", "ㅈ", "ㅉ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"]
JUNG = ["ㅏ", "ㅐ", "ㅑ", "ㅒ", "ㅓ", "ㅔ", "ㅕ", "ㅖ", "ㅗ", "ㅘ", "ㅙ", "ㅚ", "ㅛ", "ㅜ", "ㅝ", "ㅞ", "ㅟ", "ㅠ", "ㅡ", "ㅢ", "ㅣ"]
JONG = ["", "ㄱ", "ㄲ", "ㄳ", "ㄴ", "ㄵ", "ㄶ", "ㄷ", "ㄹ", "ㄺ", "ㄻ", "ㄼ", "ㄽ", "ㄾ", "ㄿ", "ㅀ", "ㅁ", "ㅂ", "ㅄ", "ㅅ", "ㅆ", "ㅇ", "ㅈ", "ㅊ", "ㅋ", "ㅌ", "ㅍ", "ㅎ"]

NULL_CHO_INDEX = CHO.index("ㅇ")


def decompose_syllable(char: str):
    """
    완성형 한글 음절 하나를 (초성, 중성, 종성) 인덱스로 분해한다.
    한글 음절이 아니면 None.
    """
    code = ord(char)
    if code < HANGUL_BASE or code > HANGUL_LAST:
        return None
    offset = code - HANGUL_BASE
    jong = offset % 28
    jung = (offset // 28) % 21
    cho = offset // 28 // 21
    return cho, jung, jong


def has_liaison_pattern(word: str) -> bool:
    """
    단어 철자 안에 "받침이 있는 음절 + 초성이 없는(ㅇ) 다음 음절" 구조가 있는지 검사한다.
    이 구조가 있으면 받침이 다음 음절 초성으로 옮겨가는 연음이 일어날 수 있는 환경이다
    (예: "발음" → [바름], "먹이" → [머기]).
    실제 연음이 일어났는지는 발음(phoneme)이 철자와 다른지(is_different)와 같이 봐야 하므로,
    이 함수는 "연음이 일어날 수 있는 구조인지"만 판단하는 보조 함수다.
    """
    syllables = [decompose_syllable(c) for c in word]
    for i in range(len(syllables) - 1):
        cur = syllables[i]
        nxt = syllables[i + 1]
        if cur is None or nxt is None:
            continue
        _, _, jong = cur
        next_cho, _, _ = nxt
        if jong != 0 and next_cho == NULL_CHO_INDEX:
            return True
    return False
