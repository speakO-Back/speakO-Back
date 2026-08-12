"""HCX 429(분당 한도) 재시도 동작을 고정한다.

배경: 슬라이드 한 장당 한 번씩 HCX를 부르는 구조라 발표 하나로도 분당 한도에 닿는다.
실측으로 94장 4개 발표를 돌렸더니 4개 중 3개가 통째로 실패했고, 사용자 화면에서는
`missing_slide_numbers`만 남았다. 재시도가 빠지면 그 상태로 되돌아간다.
"""
import pytest
import requests

from clova import hcx_request
from clova.hcx_request import post_with_retry


class _Response:
    def __init__(self, status_code, text="{}", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch):
    """테스트가 실제로 기다리면 안 된다. 대신 잔 시간을 기록해 검증에 쓴다."""
    slept = []
    monkeypatch.setattr(hcx_request.time, "sleep", slept.append)
    # 지터를 고정해서 대기시간을 예측 가능하게 만든다.
    monkeypatch.setattr(hcx_request.random, "uniform", lambda low, high: low)
    return slept


def _stub_post(monkeypatch, responses):
    """responses를 순서대로 돌려주는 가짜 requests.post. 예외 인스턴스는 그대로 raise한다."""
    calls = []

    def fake_post(endpoint, headers=None, json=None, timeout=None):
        calls.append(endpoint)
        item = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    monkeypatch.setattr(hcx_request.requests, "post", fake_post)
    return calls


def _call():
    return post_with_retry("https://example/hcx", {}, {"messages": []}, 30, label="test")


def test_rate_limited_call_is_retried_and_succeeds(monkeypatch, _no_real_sleeping):
    """429 뒤에 성공하면 슬라이드가 누락되지 않아야 한다."""
    calls = _stub_post(monkeypatch, [
        _Response(429, '{"status":{"code":"42901"}}'),
        _Response(200, '{"result":{}}'),
    ])

    response = _call()

    assert response.status_code == 200
    assert len(calls) == 2, "429를 맞고 다시 부르지 않았습니다"
    assert _no_real_sleeping, "재시도 전에 쉬지 않았습니다 — 곧바로 다시 던지면 또 429를 맞는다"


def test_retry_after_header_is_honored(monkeypatch, _no_real_sleeping):
    """서버가 알려준 대기시간이 우리가 추측한 백오프보다 정확하다."""
    _stub_post(monkeypatch, [
        _Response(429, "{}", headers={"Retry-After": "7"}),
        _Response(200),
    ])

    _call()

    assert _no_real_sleeping[0] == pytest.approx(7.0)


def test_backoff_grows_between_attempts(monkeypatch, _no_real_sleeping):
    """같은 간격으로 계속 두드리면 한도가 풀리기 전에 재시도를 다 써버린다."""
    _stub_post(monkeypatch, [_Response(429)] * 10)

    with pytest.raises(RuntimeError):
        _call()

    assert len(_no_real_sleeping) == hcx_request.MAX_RETRIES
    assert _no_real_sleeping == sorted(_no_real_sleeping), f"대기시간이 늘지 않습니다: {_no_real_sleeping}"


def test_sleep_is_capped(monkeypatch, _no_real_sleeping):
    """Retry-After가 터무니없이 길어도 요청이 영영 안 끝나면 안 된다."""
    _stub_post(monkeypatch, [_Response(429, headers={"Retry-After": "9999"}), _Response(200)])

    _call()

    assert _no_real_sleeping[0] <= hcx_request.MAX_SLEEP_SECONDS


def test_bad_request_is_not_retried(monkeypatch, _no_real_sleeping):
    """키가 틀렸거나 요청이 잘못된 경우는 다시 불러도 같은 답이 온다. 시간과 토큰만 버린다."""
    calls = _stub_post(monkeypatch, [_Response(400, '{"status":{"code":"40001"}}')])

    with pytest.raises(RuntimeError, match="400"):
        _call()

    assert len(calls) == 1
    assert not _no_real_sleeping


def test_server_error_is_retried(monkeypatch, _no_real_sleeping):
    calls = _stub_post(monkeypatch, [_Response(502), _Response(200)])
    assert _call().status_code == 200
    assert len(calls) == 2


def test_network_error_is_retried(monkeypatch, _no_real_sleeping):
    """연결이 끊기는 것도 다시 부르면 성공할 수 있다."""
    calls = _stub_post(monkeypatch, [requests.ConnectionError("연결 끊김"), _Response(200)])
    assert _call().status_code == 200
    assert len(calls) == 2


def test_final_failure_message_carries_the_cause(monkeypatch, _no_real_sleeping):
    """실패해도 원인을 알 수 있어야 한다. 상태코드만으론 429인지 키 문제인지 구분이 안 된다."""
    _stub_post(monkeypatch, [_Response(429, '{"status":{"code":"42901"}}')] * 10)

    with pytest.raises(RuntimeError) as excinfo:
        _call()

    assert "42901" in str(excinfo.value)


def test_generator_recovers_from_rate_limit(monkeypatch, _no_real_sleeping):
    """단위 함수만이 아니라 대본 생성 경로 전체가 429에서 복구돼야 한다."""
    from clova.full_generation.generator import FullScriptGenerator

    body = '{"result":{"message":{"content":"이 슬라이드를 설명드리겠습니다."},"usage":{}}}'

    class _Json(_Response):
        def json(self):
            import json as _j
            return _j.loads(self.text)

    state = {"first": True}

    def fake_post(endpoint, headers=None, json=None, timeout=None):
        if state["first"]:
            state["first"] = False
            return _Json(429, '{"status":{"code":"42901"}}')
        return _Json(200, body)

    monkeypatch.setattr(hcx_request.requests, "post", fake_post)

    generator = FullScriptGenerator()
    generator.api_key = "test-key"
    generator.use_fallback = False
    result = generator.generate_full_script("Slide 1: 첫 장\nSlide 2: 둘째 장", 2, "격식체")

    assert result["missing_slide_numbers"] == [], "429 한 번에 슬라이드가 누락됐습니다"


def test_total_backoff_outlasts_the_per_minute_window():
    """HCX 한도는 '분당' 기준이다. 백오프 합계가 1분을 못 넘기면 재시도를 다 쓰고도 그대로 429다.
    실측(2026-08-06): 3회 / base 2초(합계 ≈16초)로 뒀더니 정확히 그렇게 실패했다."""
    total = sum(
        min(hcx_request.MAX_SLEEP_SECONDS, hcx_request.RETRY_BASE_SECONDS * (2 ** attempt))
        for attempt in range(hcx_request.MAX_RETRIES)
    )
    assert total >= 60, f"백오프 합계가 {total:.0f}초라 분당 한도 창을 못 넘깁니다"
