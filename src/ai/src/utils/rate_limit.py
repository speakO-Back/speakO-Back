"""요청 횟수 제한 미들웨어 (유료 외부 API 비용 방어).

입력 길이 상한은 "요청 1건이 얼마나 비쌀 수 있는가"를 막는다. 이건 "그 요청을 몇 번
보낼 수 있는가"를 막는다. 둘 다 있어야 비용 상한이 실제로 걸린다.

## 왜 slowapi를 안 쓰나
slowapi도 기본 저장소가 프로세스 메모리라, 단일 워커 배포에서는 여기 구현과 차이가 없다.
새 의존성을 늘리는 대신 필요한 만큼만 직접 둔다. 워커를 여러 개로 늘리게 되면 이 모듈과
`job_store` 둘 다 외부 저장소로 옮겨야 한다(같은 제약).

## ⚠️ 클라이언트 식별의 한계
프론트는 스프링 서버를 거쳐 이 API를 부른다. 그러면 **모든 요청의 소켓 주소가 스프링 한 대**가
되어, IP 기준 제한이 사실상 전역 상한으로 동작한다. 그래서 `X-Forwarded-For`가 있으면 그
첫 항목(최초 클라이언트)을 우선 쓴다. 스프링이 이 헤더를 넘겨주지 않으면 전역 상한이 되므로,
기본값은 "한 명이 막히지 않을 만큼" 넉넉하게 잡되 폭주는 확실히 끊는 선으로 둔다.
XFF는 신뢰 경계 밖에서 위조 가능하지만, 여기 목적은 인증이 아니라 비용 방어다.
"""
import os
import threading
import time

# 유료 외부 API(HCX/Azure/ETRI/표준국어대사전)를 태우는 경로. 나머지(조회/폴링)보다 빡빡하게 건다.
# GET은 제외한다 — GET /api/projects(목록)과 GET /api/script/jobs/{id}(폴링)는 돈이 안 들고,
# 특히 폴링은 프론트가 1~2초마다 부르므로 여기 걸리면 정상 흐름이 깨진다.
_EXPENSIVE_PREFIXES = (
    "/api/projects",    # PPTX 이미지 장표 → HCX 비전
    "/api/script/",     # HCX 대본 생성
    "/api/analysis/",   # ETRI + 표준국어대사전
    "/api/evaluation/",  # Azure 발음 평가 + HCX 피드백
    "/api/tts/",         # Clova Voice 합성 (캐시 미스 시 글자 수만큼 과금)
)


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


class RateLimiter:
    """(클라이언트, 등급)별 고정 윈도 카운터."""

    def __init__(self, default_per_minute: int, expensive_per_minute: int, window_seconds: int = 60):
        self.default_per_minute = default_per_minute
        self.expensive_per_minute = expensive_per_minute
        self.window_seconds = window_seconds
        self._counters = {}
        self._lock = threading.Lock()

    def check(self, client: str, tier: str, now: float):
        """(허용 여부, 재시도까지 남은 초)를 돌려준다."""
        limit = self.expensive_per_minute if tier == "expensive" else self.default_per_minute
        window_start = now - (now % self.window_seconds)
        key = (client, tier)

        with self._lock:
            # 윈도가 넘어갔으면 새로 시작한다.
            started_at, count = self._counters.get(key, (window_start, 0))
            if started_at != window_start:
                started_at, count = window_start, 0

            if count >= limit:
                retry_after = int(started_at + self.window_seconds - now) + 1
                return False, max(1, retry_after)

            self._counters[key] = (started_at, count + 1)

            # 오래된 항목이 무한정 쌓이지 않도록 가끔 청소한다.
            if len(self._counters) > 10_000:
                cutoff = window_start
                for stale in [k for k, (s, _) in self._counters.items() if s < cutoff]:
                    del self._counters[stale]

        return True, 0

    def reset(self):
        with self._lock:
            self._counters.clear()


# 기본값: 스프링을 거쳐 한 IP로 몰릴 수 있으므로 넉넉하되, 폭주는 끊는 선.
# expensive 60/분이면 최악의 경우에도 분당 유료 호출이 60건으로 묶인다.
limiter = RateLimiter(
    default_per_minute=_env_int("RATE_LIMIT_PER_MINUTE", 300),
    expensive_per_minute=_env_int("RATE_LIMIT_EXPENSIVE_PER_MINUTE", 60),
)


def _client_id(scope) -> str:
    for name, value in scope.get("headers") or []:
        if name == b"x-forwarded-for":
            first = value.decode("latin-1").split(",")[0].strip()
            if first:
                return first
    client = scope.get("client")
    return client[0] if client else "unknown"


def _tier(method: str, path: str) -> str:
    if method == "GET" or method == "OPTIONS":
        return "default"
    return "expensive" if path.startswith(_EXPENSIVE_PREFIXES) else "default"


class RateLimitMiddleware:
    def __init__(self, app, enabled: bool = True):
        self.app = app
        self.enabled = enabled

    async def __call__(self, scope, receive, send):
        if not self.enabled or scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET")

        # CORS preflight는 비용이 없고, 막으면 브라우저가 본 요청을 아예 못 보낸다.
        if not path.startswith("/api/") or method == "OPTIONS":
            await self.app(scope, receive, send)
            return

        allowed, retry_after = limiter.check(_client_id(scope), _tier(method, path), time.time())
        if allowed:
            await self.app(scope, receive, send)
            return

        import json
        body = json.dumps(
            {"detail": f"요청이 너무 잦습니다. {retry_after}초 후에 다시 시도해주세요."},
            ensure_ascii=False,
        ).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 429,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("latin-1")),
                (b"retry-after", str(retry_after).encode("latin-1")),
            ],
        })
        await send({"type": "http.response.body", "body": body})
