import re
from collections import Counter

# 빈도 기반 추출 시 제외할 일반 어미/접속사류
STOPWORDS = {
    "그리고", "그러나", "하지만", "그래서", "따라서", "또한", "즉", "먼저", "마지막으로",
    "합니다", "습니다", "있습니다", "됩니다", "그것", "이것", "저것", "이번",
    "오늘", "여러분", "우리", "대한", "위한", "통해", "대해", "에서", "그런",
}


def extract_frequent_terms(texts, exclude=None, top_n=8, min_length=2):
    """
    자주 등장하는 단어를 빈도 기준으로 추려낸다.
    목차 슬라이드가 없는 PPT의 키워드 추출, ETRI 키가 없을 때의 발음 주의 단어 추출 등
    정교한 NLP 분석이 없을 때의 대체 휴리스틱으로 쓰인다.
    """
    exclude = exclude or set()
    tokens = []
    for text in texts:
        for token in re.split(r"[\s,.\-·:;()\[\]/\\|!?\"']+", text):
            token = token.strip()
            if len(token) >= min_length and token not in STOPWORDS and token not in exclude:
                tokens.append(token)

    if not tokens:
        return []

    counts = Counter(tokens)
    return [word for word, _ in counts.most_common(top_n)]
