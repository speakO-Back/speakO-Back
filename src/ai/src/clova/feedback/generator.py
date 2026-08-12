"""
발음 평가 결과(숫자)를 사람이 읽는 코칭 피드백으로 바꾸는 HCX 클라이언트.

왜 필요한가: Azure는 "정확도 87.4 / 유창성 82.1" 같은 점수만 준다. 사용자는 이 숫자만 보고는
무엇을 어떻게 고쳐야 할지 모른다. 점수 + 실제로 틀린 단어들을 근거로 "무엇이 좋았고, 무엇이
약하며, 어떻게 연습하면 되는지"를 한국어로 설명해준다. (피그마 '발표 코칭 내역 / AI 발음 피드백')

출력 형식: 엄격한 JSON을 요구하면 모델이 형식을 자주 깬다(이 프로젝트에서 TOON으로 이미 겪음).
그래서 [총평]/[잘한 점]/[개선할 점]/[연습 팁] 네 개의 고정 머리말만 요구하고, 파싱은 관대하게 한다.
"""

import os
import re
import requests
from dotenv import load_dotenv

from utils.usage_tracker import log_hcx_call
from clova.hcx_request import post_with_retry

load_dotenv()

REQUEST_TIMEOUT_SECONDS = 30

# 프롬프트에 넣을 '많이 틀린 단어' 개수 상한. 전부 넣으면 입력이 길어지고 모델이 요점을 놓친다.
MAX_WEAK_WORDS = 12
# 이 점수 미만인 단어를 '약한 발음'으로 본다(Azure 0~100 기준).
WEAK_WORD_THRESHOLD = 70
# 이 점수 이상이면 '잘 발음한 단어'로 본다. 칭찬에도 근거가 필요하다 —
# 근거를 안 주면 모델이 대본에서 아무 단어나 골라 "잘 발음했습니다"라고 지어낸다(실측).
STRONG_WORD_THRESHOLD = 90
MAX_STRONG_WORDS = 8

_SECTION_KEYS = {
    "총평": "summary",
    "잘한 점": "strengths",
    "개선할 점": "improvements",
    "연습 팁": "practice_tips",
}


def _is_placeholder_key(api_key):
    return not api_key or "여기에_" in api_key


def collect_weak_words(words_detail, threshold=WEAK_WORD_THRESHOLD, limit=MAX_WEAK_WORDS):
    """단어별 결과에서 점수가 낮거나 잘못 발음한 단어를 골라 점수 오름차순으로 돌려준다."""
    weak = []
    for word in words_detail or []:
        if not isinstance(word, dict):
            continue
        score = word.get("accuracy_score")
        error_type = (word.get("error_type") or "").strip()
        # 누락(Omission)은 '발음이 나쁜 것'이 아니라 안 읽은 것이므로 발음 지적 대상에서 뺀다.
        if error_type == "Omission":
            continue
        is_weak = (isinstance(score, (int, float)) and score < threshold) or error_type == "Mispronunciation"
        if is_weak and word.get("word"):
            weak.append({
                "word": word["word"],
                "accuracy_score": score,
                "error_type": error_type or "None",
            })

    weak.sort(key=lambda w: w["accuracy_score"] if isinstance(w["accuracy_score"], (int, float)) else 0)
    return weak[:limit]


def collect_strong_words(words_detail, threshold=STRONG_WORD_THRESHOLD, limit=MAX_STRONG_WORDS):
    """점수가 높은 단어를 골라 점수 내림차순으로 돌려준다(칭찬의 근거로 쓴다)."""
    strong = []
    for word in words_detail or []:
        if not isinstance(word, dict) or not word.get("word"):
            continue
        score = word.get("accuracy_score")
        if (word.get("error_type") or "None") == "None" and isinstance(score, (int, float)) and score >= threshold:
            strong.append({"word": word["word"], "accuracy_score": score})

    strong.sort(key=lambda w: w["accuracy_score"], reverse=True)
    return strong[:limit]


def parse_feedback_sections(text):
    """
    [총평]/[잘한 점]/[개선할 점]/[연습 팁] 머리말로 나뉜 평문을 구조화한다.
    머리말이 하나도 없으면 전체를 summary로 취급한다(형식을 안 지켜도 내용은 살린다).
    """
    if not text or not text.strip():
        return None

    result = {"summary": "", "strengths": [], "improvements": [], "practice_tips": []}

    # "[총평]" / "총평:" / "**총평**" 등 표기 흔들림을 모두 받아준다.
    pattern = "|".join(re.escape(label) for label in _SECTION_KEYS)
    matches = list(re.finditer(rf"^\s*[\[\*#\-]*\s*({pattern})\s*[\]\*:：]*\s*$", text, flags=re.MULTILINE))
    if not matches:
        # 머리말 없이 한 덩어리로 답한 경우.
        result["summary"] = re.sub(r"\s+", " ", text).strip()
        return result

    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        body = text[match.end():end].strip()
        key = _SECTION_KEYS[match.group(1)]

        if key == "summary":
            result[key] = re.sub(r"\s+", " ", body).strip()
        else:
            # 불릿("- ", "• ", "1. ")을 떼고 줄 단위로 모은다.
            items = []
            for line in body.splitlines():
                line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", line).strip()
                if line:
                    items.append(line)
            result[key] = items

    result["practice_tips"] = normalize_practice_tips(result["practice_tips"])
    return result


# 프론트가 아이콘을 고를 때 쓰는 분류. 모델이 엉뚱한 값을 쓰면 general로 떨어뜨린다.
# 성량(volume)은 일부러 뺐다. 두 가지 이유가 겹친다.
#  1) 녹음 음량은 마이크와 입 사이 거리에 좌우돼서 발표자의 실제 목소리 크기를 뜻하지 않는다. (PM 판단)
#  2) 애초에 우리에겐 성량 데이터가 없다. Azure가 주는 건 정확도·유창성·완성도뿐이라,
#     성량 팁을 쓰게 두면 근거 없이 지어내는 것이 된다. 이 프로젝트가 "칭찬에도 근거가 필요하다"로
#     한 번 겪은 문제와 같은 종류다(위 STRONG_WORD_THRESHOLD 주석 참고).
_TIP_KEYS = ("consonant", "ending", "intonation", "speed")

# 피그마 Coach View Page / Feedback Page가 팁 카드를 **4개** 그린다
# (2026-08-09 갱신본 ㊹: "음성 파일 AI 피드백 (자음/끝소리/강세억양/속도)").
# 넘치면 화면 레이아웃이 깨지므로 코드로 자른다.
#
# ⚠️ 예전엔 3이었다. 8/06 시점 피그마가 카드 3개였고, 모델이 4줄을 주길래 잘랐던 것이다.
#    그런데 8/09 갱신본은 `_TIP_KEYS`와 정확히 같은 4분류를 카드로 그린다 — 3으로 두면
#    분류 하나(대개 speed)가 통째로 잘려 나간다(실측: 제로 녹음 피드백에서 속도 팁 누락).
MAX_PRACTICE_TIPS = 4


def normalize_practice_tips(tips):
    """연습 팁을 [{key, title, description}] 형태로 정규화한다.

    화면에서 아이콘 + 제목 + 설명으로 그려지므로(피그마 Coach View Page) 제목과 설명이
    분리돼 있어야 하고, 아이콘을 고르려면 안정적인 key가 필요하다.

    **옛 형식(문자열 리스트)도 그대로 받는다.** 이미 생성돼 `evaluations.feedback`에 캐시된
    피드백이 있기 때문이다(재요청해도 HCX를 다시 부르지 않고 그 값을 반환한다). 형식을 바꾸면서
    옛 캐시를 못 읽으면 지난 평가 화면이 깨진다.
    """
    normalized = []
    for tip in tips or []:
        if isinstance(tip, dict):
            key = tip.get("key") if tip.get("key") in _TIP_KEYS else "general"
            normalized.append({
                "key": key,
                "title": (tip.get("title") or "").strip(),
                "description": (tip.get("description") or "").strip(),
            })
            continue

        parts = [part.strip() for part in str(tip).split("|")]
        if len(parts) >= 3 and parts[0] in _TIP_KEYS:
            normalized.append({"key": parts[0], "title": parts[1], "description": " ".join(parts[2:]).strip()})
        elif len(parts) == 2:
            # 분류 없이 "제목 | 설명"만 준 경우.
            normalized.append({"key": "general", "title": parts[0], "description": parts[1]})
        else:
            # 옛 형식(문장 하나) 또는 형식을 안 지킨 경우 — 내용은 살리고 제목만 비운다.
            normalized.append({"key": "general", "title": "", "description": str(tip).strip()})

    return normalized[:MAX_PRACTICE_TIPS]


# ── 근거 없는 음운 단정 걸러내기 ──────────────────────────────────────────────
#
# Azure가 주는 건 **단어별 정확도 점수와 오류 유형(None/Mispronunciation/Omission)뿐**이다.
# *어떤 소리를* 어떻게 틀렸는지는 측정되지 않는다. 한국어(ko-KR)는 음소 이름도 오지 않는다
# (Azure 문서: phoneme name은 en-US(IPA)와 en-US·zh-CN(SAPI)만 지원, 그 외 로케일은 점수만).
#
# 그런데 모델은 빈칸을 상상으로 채운다. 실측(2026-08-09, 제로 녹음):
#   "'중요성을', '유연한', '마음으로' 등에서 받침을 생략하거나 잘못 발음하는 경우가 있습니다"
#   "숫자 '7'을 발음할 때 영어식 발음이 아닌 한글 표기에 맞게 발음해야 합니다"
# 둘 다 서버가 알 수 없는 내용이다. 발음 교정 제품에서 **틀린 교정을 자신 있게 말하는 것**은
# 아무 말도 안 하는 것보다 나쁘다.
#
# 프롬프트로 금지하는 게 1차 방어지만, 모델은 언젠가 다시 지어낸다. 그래서 생성된 뒤에도 거른다.
_MECHANISM_TERMS = (
    "받침", "연음", "경음화", "격음화", "비음화", "유음화", "구개음화",
    "된소리", "거센소리", "장음", "단음", "장단음", "영어식", "탈락", "축약",
    "혀", "입술", "성대", "모음", "자음", "발음기호", "흐릿", "뭉개",
    "끝소리", "첫소리", "억양", "강세", "조음", "발성",
)

# 문장 분리 — 한 항목이 여러 문장인 경우가 흔하다. 통째로 버리면 멀쩡한 내용까지 날아간다.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _asserts_unmeasured_cause(line: str) -> bool:
    """측정되지 않은 **발음 메커니즘을 서술**하는 문장인가.

    처음엔 "단어를 따옴표로 지목했고 + 음운 용어가 있을 때"만 걸렀는데, 실호출에서 이게
    샜다(2026-08-09): *"여러 단어에서 끝소리가 제대로 처리되지 않은 것으로 보입니다"* —
    단어를 지목하지 않았을 뿐 여전히 알 수 없는 내용이다. 그래서 **음운 용어가 있으면 자른다.**

    ⚠️ 이 규칙은 총평·잘한 점·개선할 점에만 적용한다. **연습 팁은 제외** — 거기는 피그마가
    자음/끝소리/강세억양/속도 네 카드를 그리는 자리라 그 단어들이 나오는 게 정상이다.
    즉 **"과거에 이렇게 틀렸다"(근거 없음)와 "앞으로 이렇게 연습하라"(일반 조언)를 칸으로
    가른다.** 조언이 개선할 점에서 잘려도 연습 팁이 같은 말을 하므로 화면에서 사라지지 않는다.
    """
    if not line:
        return False
    return any(term in line for term in _MECHANISM_TERMS)


def _grounded_improvement(weak_words) -> str:
    """전부 걸러졌을 때 대신 넣을, **점수만으로 쓴** 문장.

    빈 목록을 그대로 두면 화면의 '개선할 점'이 통째로 비어 사용자는 평가가 실패한 줄 안다.
    """
    named = [w["word"] for w in (weak_words or [])[:3] if w.get("word")]
    if not named:
        return "낮은 점수를 받은 단어가 뚜렷하지 않습니다. 전체적으로 조금 더 또박또박 읽어보세요."
    listed = ", ".join(f"'{word}'" for word in named)
    return f"{listed}의 정확도가 낮았습니다. 한 음절씩 천천히 또박또박 읽어보세요."


def drop_unsupported_claims(sections, weak_words=()):
    """측정되지 않은 원인을 단정한 문장을 걷어낸다.

    **연습 팁은 건드리지 않는다.** 피그마가 자음/끝소리/강세억양/속도 네 카드를 그리므로
    "받침이 있는 단어에서 자음을 끝까지 발음하세요" 같은 **일반 조언**은 정상이다.
    문제는 특정 단어를 지목하고 원인을 붙이는 것이지, 자음을 언급하는 것 자체가 아니다.
    """
    if not sections:
        return sections

    def _keep_clean_sentences(text: str) -> str:
        """문제 있는 문장만 떼어낸다. 항목 하나가 여러 문장인 경우가 흔하기 때문이다.

        실측 예: "몇몇 단어에서 발음이 부정확했으며, 특히 '언제든지'처럼 짧은 단어조차 낮은
        점수를 기록했습니다. 또한 여러 단어에서 끝소리가 제대로 처리되지 않은 것으로 보입니다."
        → 앞 문장은 점수 그대로라 사실이고, 뒤 문장만 근거가 없다.
        """
        kept = [s for s in _SENTENCE_SPLIT.split(text) if s.strip() and not _asserts_unmeasured_cause(s)]
        return " ".join(kept).strip()

    def _clean_item(item: str) -> str:
        """목록 항목 하나를 정리한다. **첫 문장이 잘리면 항목을 통째로 버린다.**

        뒷문장은 앞문장에 기대어 쓰인다. 앞을 잘라내고 뒤만 남기면 말이 안 되는 조각이 화면에
        남는다 — 실측(2026-08-09 재검증):

            원문: "숫자 '7'을 영어식으로 발음하셨습니다. 한국어로 자연스럽게 발음하도록
                   주의하시기 바랍니다."
            앞문장만 걸러낸 결과: "한국어로 자연스럽게 발음하도록 주의하시기 바랍니다."
            → 한국어 발표에서 "한국어로 발음하라"는 말만 남아 무슨 뜻인지 알 수 없다.

        반대로 **첫 문장이 살아남았다면 뒤만 잘라내도 괜찮다.** 첫 문장이 그 항목의 주장을
        담고 있고 뒷문장은 부연이기 때문이다:

            "…특히 '언제든지'처럼 짧은 단어조차 낮은 점수를 기록했습니다."  ← 남긴다
            "또한 여러 단어에서 끝소리가 제대로 처리되지 않은 것으로 보입니다."  ← 버린다
        """
        sentences = [s.strip() for s in _SENTENCE_SPLIT.split(item) if s.strip()]
        if not sentences or _asserts_unmeasured_cause(sentences[0]):
            return ""
        return " ".join(s for s in sentences if not _asserts_unmeasured_cause(s))

    for key in ("strengths", "improvements"):
        cleaned = (_clean_item(line) for line in sections.get(key) or [])
        sections[key] = [line for line in cleaned if line]

    # 총평은 목록이 아니라 이어지는 산문이라 문장 단위로만 거른다. 첫 문장을 이유로 통째로
    # 버리면 총평 칸이 비는데, 그건 조각이 남는 것보다 나쁘다.
    sections["summary"] = _keep_clean_sentences(sections.get("summary") or "")

    # 지적이 통째로 사라지면 화면이 빈다 — 점수만으로 쓸 수 있는 문장으로 채운다.
    if not sections["improvements"]:
        sections["improvements"] = [_grounded_improvement(weak_words)]

    return sections


class PronunciationFeedbackGenerator:
    def __init__(self):
        self.api_key = os.getenv("HCX_API_KEY")
        self.model_name = os.getenv("HCX_MODEL_NAME", "HCX-005")
        self.endpoint = f"https://clovastudio.stream.ntruss.com/v3/chat-completions/{self.model_name}"
        self.use_fallback = _is_placeholder_key(self.api_key)
        if self.use_fallback:
            print("⚠️ [경고] HCX_API_KEY가 설정되지 않았습니다. 발음 피드백은 안전 모드(None 반환)로 동작합니다.")

    _SYSTEM_PROMPT = """
        당신은 한국어 발표 발음을 지도하는 전문 스피치 코치입니다.
        학습자의 발음 평가 결과(점수와 틀린 단어)를 보고, 실제로 도움이 되는 코칭을 해주세요.

        [작성 규칙]
        1. 점수를 그대로 나열하지 말고, 그 점수가 무슨 의미인지 사람 말로 풀어주세요.
        2. 지적도 칭찬도 반드시 아래에 주어진 단어 목록만 근거로 삼으세요.
           목록에 없는 단어를 골라 "잘 발음했습니다"라고 하거나 지적하지 마세요.
           대본은 맥락 파악용일 뿐이며, 대본에서 단어를 골라 평가하면 안 됩니다.
        3. ⚠️ **가장 중요합니다 — 우리는 "어느 단어가 몇 점인지"만 압니다.**
           그 단어를 **어떻게** 잘못 발음했는지는 측정되지 않았습니다. 그러므로 특정 단어를
           지목하면서 원인을 단정하지 마세요. 아래는 전부 **금지**입니다.
             ✗ "'유연한'에서 받침을 생략했습니다"      ← 받침을 생략했는지 알 수 없음
             ✗ "'마음으로'의 연음이 부자연스럽습니다"   ← 연음 여부는 측정되지 않음
             ✗ "숫자 '7'을 영어식으로 발음했습니다"     ← 어떻게 읽었는지 알 수 없음
           대신 **관찰된 사실 + 행동**으로 쓰세요.
             ✓ "'유연한', '마음으로'가 낮은 정확도를 받았습니다. 한 음절씩 또박또박 읽어보세요."
             ✓ "'언제든지'는 특히 낮았습니다(28점). 천천히 반복해서 연습해보세요."
        4. 격려하되 과장하지 마세요. 점수가 낮으면 낮다고 정직하게 말하세요.
        5. 존댓말(~습니다/~하세요)로 쓰고, 각 항목은 한 문장으로 간결하게 쓰세요.

        출력은 아래 네 개의 머리말을 그대로 쓰고, 그 아래에 내용을 적으세요. 다른 머리말이나 해설은 덧붙이지 마세요.

        [총평]
        (2~3문장)

        [잘한 점]
        - (1~3개)

        [개선할 점]
        - (1~3개)

        [연습 팁]
        정확히 4줄. 각 줄은 `분류 | 제목 | 설명` 형식으로 쓰세요.
        분류는 consonant(자음) / ending(끝소리) / intonation(강세·억양) / speed(속도)
        **네 가지를 각각 한 줄씩** 쓰세요(순서는 이대로). 제목은 10자 이내입니다.
        연습 팁은 **일반적인 연습 방법**이므로 여기서는 자음·끝소리를 언급해도 됩니다.
        다만 **특정 단어를 지목하지는 마세요** (그 단어를 어떻게 틀렸는지는 모르기 때문입니다).
        **목소리 크기(성량)에 대한 조언은 쓰지 마세요.** 녹음 음량은 마이크 거리에 좌우돼서
        발표자의 실제 성량을 알 수 없고, 주어진 점수에도 성량 정보가 없습니다.
        예) consonant | 명확한 자음 발음 | 'ㄷ, ㅈ, ㅅ' 계열 자음을 더 또렷하게 발음해보세요.
        """

    def generate_feedback(self, overall_scores, weak_words, script_excerpt="", strong_words=None):
        """
        점수와 단어별 결과를 근거로 코칭 피드백을 생성한다.
        성공하면 {"summary", "strengths", "improvements", "practice_tips"} dict, 실패하면 None.
        """
        if self.use_fallback:
            return None

        user_prompt = self._build_user_prompt(
            overall_scores or {}, weak_words or [], script_excerpt, strong_words or []
        )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        payload = {
            "messages": [
                {"role": "system", "content": [{"type": "text", "text": self._SYSTEM_PROMPT}]},
                {"role": "user", "content": [{"type": "text", "text": user_prompt}]},
            ],
            "topP": 0.8,
            "topK": 0,
            "maxTokens": 1000,
            "temperature": 0.4,
            "repeatPenalty": 3.0,
        }

        try:
            response = post_with_retry(self.endpoint, headers, payload, REQUEST_TIMEOUT_SECONDS, label="feedback")
            result = response.json()
            usage = result.get("result", {}).get("usage", {})
            log_hcx_call(
                "feedback",
                usage.get("promptTokens", 0),
                usage.get("completionTokens", 0),
                usage.get("totalTokens", 0),
            )
            sections = parse_feedback_sections(result["result"]["message"]["content"])
            # 프롬프트로 금지해도 모델은 언젠가 다시 지어낸다. 생성된 뒤에도 한 번 더 거른다.
            return drop_unsupported_claims(sections, weak_words or [])
        except Exception as e:
            print(f"❌ 발음 피드백 생성 API 호출 중 에러가 발생했습니다: {e}")
            return None

    def _build_user_prompt(self, overall_scores, weak_words, script_excerpt, strong_words=()):
        def score_line(label, key):
            value = overall_scores.get(key)
            return f"- {label}: {value if value is not None else '측정 안 됨'}"

        if weak_words:
            weak_lines = "\n".join(
                f"- {w['word']} (정확도 {w.get('accuracy_score')}, 유형 {w.get('error_type')})"
                for w in weak_words
            )
        else:
            weak_lines = "- 특별히 낮은 점수를 받은 단어는 없습니다."

        # 대본은 맥락 참고용으로 앞부분만. 전체를 넣으면 입력이 길어지고 피드백이 산만해진다.
        excerpt = (script_excerpt or "").strip()
        excerpt_block = excerpt[:500] if excerpt else "제공되지 않음"

        if strong_words:
            strong_lines = "\n".join(f"- {w['word']} (정확도 {w.get('accuracy_score')})" for w in strong_words)
        else:
            strong_lines = "- 특별히 높은 점수를 받은 단어는 없습니다."

        return f"""
        [발음 평가 점수] (0~100)
        {score_line('정확도', 'accuracy')}
        {score_line('유창성', 'fluency')}
        {score_line('완성도', 'completeness')}
        {score_line('종합 발음 점수', 'pronunciation_score')}

        [점수가 낮았던 단어] — 개선할 점의 근거로만 쓰세요
        {weak_lines}

        [잘 발음한 단어] — 잘한 점의 근거로만 쓰세요
        {strong_lines}

        [발표 대본 일부 (맥락 참고용 — 여기서 단어를 골라 평가하지 마세요)]
        {excerpt_block}
        """
