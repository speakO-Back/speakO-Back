"""
HCX 호출 공통 재시도.

## 왜 필요한가
HCX는 **분당 호출 수 제한**이 있고, 넘기면 HTTP 429 + `{"status":{"code":"42901"}}`를 돌려준다.
이 서버는 정렬을 보장하려고 **슬라이드 한 장당 한 번씩** 부르므로(full_generation/generator.py 참고)
발표 한 건만으로도 한도에 닿는다.

실측: 94장짜리 4개 발표를 동시 4개로 던졌더니 **4개 중 3개가 통째로 실패**했다.
게다가 빈 응답 재시도는 곧바로 다시 던져서 429를 한 번 더 맞았다.
재시도가 없으면 사용자 화면에서는 생성이 조용히 실패하고 `missing_slide_numbers`만 잔뜩 남는다.

## 무엇을 하는가
- 429(레이트리밋)와 5xx(일시적 서버 오류), 그리고 네트워크 예외에 대해 지수 백오프로 다시 부른다.
- 서버가 `Retry-After`를 주면 그 값을 우선한다(추측한 대기시간보다 정확하다).
- 지터를 섞는다. 여러 슬라이드를 동시에 부르는 구조라, 지터가 없으면 모두 같은 순간에
  깨어나 429를 다시 다 같이 맞는다.
- 4xx 중 429가 아닌 것(잘못된 키, 잘못된 요청)은 다시 불러도 결과가 같으므로 즉시 실패시킨다.
"""
import os
import random
import time

import requests

# ⚠️ 아래 세 값은 **HCX 한도가 '분당' 기준**이라는 사실에 맞춰 정했다.
# 처음엔 3회 / base 2초로 뒀는데, 총 대기가 16초라 1분 경계를 못 넘겨서 재시도를 다 쓰고도
# 그대로 429로 실패했다(실측). 창이 분 단위면 백오프 합계도 1분을 넘겨야 의미가 있다.
# 지금 값은 대기 합계 5+10+20+30 ≈ 65초로 창을 확실히 넘긴다.
MAX_RETRIES = max(0, int(os.getenv("HCX_MAX_RETRIES", "4")))
# 백오프 기준 시간. 대기 = BASE * 2**시도횟수 + 지터.
RETRY_BASE_SECONDS = float(os.getenv("HCX_RETRY_BASE_SECONDS", "5"))
# 한 번에 기다릴 수 있는 최대 시간. Retry-After가 터무니없이 길게 와도 요청이 영영 안 끝나면 안 된다.
# 한도 창이 1분이므로 그보다 오래 기다릴 이유는 없다.
MAX_SLEEP_SECONDS = float(os.getenv("HCX_MAX_RETRY_SLEEP_SECONDS", "30"))

RATE_LIMIT_STATUS = 429


def _retry_after_seconds(response):
    """서버가 알려준 대기시간(초). 없거나 해석 불가면 None."""
    raw = response.headers.get("Retry-After") if response is not None else None
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        # HTTP-date 형식도 규격상 허용되지만 HCX는 초 단위로 준다. 해석 못 하면 백오프로 넘긴다.
        return None


def _sleep_seconds(attempt, response):
    """이번 시도 후 얼마나 쉴지. 서버 지시 > 지수 백오프 순으로 정한다."""
    hinted = _retry_after_seconds(response)
    if hinted is not None:
        # 서버가 준 값에도 지터를 조금 얹는다. 동시에 깨어나 다시 몰리는 것을 막는다.
        return min(MAX_SLEEP_SECONDS, hinted + random.uniform(0, 1))
    backoff = RETRY_BASE_SECONDS * (2 ** attempt)
    return min(MAX_SLEEP_SECONDS, backoff + random.uniform(0, backoff / 2))


def _should_retry(response):
    """다시 부르면 결과가 달라질 수 있는 실패인가."""
    return response.status_code == RATE_LIMIT_STATUS or response.status_code >= 500


def post_with_retry(endpoint, headers, payload, timeout, label=""):
    """
    HCX에 POST하고, 일시적 실패면 잠시 쉬었다 다시 부른다.

    성공하면 Response를 그대로 돌려준다. 끝내 실패하면 RuntimeError를 던진다 —
    호출부들이 이미 예외를 잡아 안전 모드(None 반환)로 빠지므로 반환값 규약을 바꾸지 않는다.
    """
    where = f"[{label}] " if label else ""
    last_error = None

    for attempt in range(MAX_RETRIES + 1):
        try:
            response = requests.post(endpoint, headers=headers, json=payload, timeout=timeout)
        except requests.RequestException as exc:
            # 연결 끊김·타임아웃은 다시 부르면 성공할 수 있다.
            last_error = f"{type(exc).__name__} — {exc}"
            response = None
        else:
            if response.status_code < 400:
                return response
            last_error = f"HTTP {response.status_code} — {response.text[:300]}"
            if not _should_retry(response):
                # 키가 틀렸거나 요청이 잘못된 경우. 다시 불러도 같은 답이 온다.
                raise RuntimeError(last_error)

        if attempt == MAX_RETRIES:
            break

        wait = _sleep_seconds(attempt, response)
        reason = "레이트리밋(429)" if response is not None and response.status_code == RATE_LIMIT_STATUS else "일시적 오류"
        print(f"  ⏳ {where}HCX {reason} — {wait:.1f}초 후 재시도합니다 ({attempt + 1}/{MAX_RETRIES}).")
        time.sleep(wait)

    raise RuntimeError(f"{where}재시도 {MAX_RETRIES}회 후에도 실패: {last_error}")
