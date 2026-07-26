"""
HCX 비전(이미지 텍스트 인식) 요청 포맷 회귀 테스트.

배경: dataUri.data에 "data:<mime>;base64," 접두어를 빼고 순수 base64만 보내면
HCX가 400(40001 Invalid parameter)으로 전부 거절한다. 그런데 호출부는 실패해도
빈 문자열을 돌려주므로(안전 모드), 겉으로는 "이미지에 글자가 없었다"와 구분되지 않는다.
그래서 이미지 전용 PPT 6개의 대본 추출이 조용히 실패하고 있었다.
포맷이 다시 깨지면 여기서 잡는다.
"""

import base64

from clova.vision import image_text_extractor as vision_module
from clova.vision.image_text_extractor import ImageTextExtractor

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-image-payload"


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


def _ok_payload(content="슬라이드에 적힌 글자"):
    return {
        "result": {
            "message": {"content": content},
            "usage": {"promptTokens": 10, "completionTokens": 5, "totalTokens": 15},
        }
    }


def _extractor():
    extractor = ImageTextExtractor()
    extractor.api_key = "test-key"
    extractor.use_fallback = False
    return extractor


def _captured_data_uri(monkeypatch, mime_type="image/png", image_bytes=PNG_BYTES):
    sent = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        sent["payload"] = json
        return _FakeResponse(payload=_ok_payload())

    monkeypatch.setattr(vision_module.requests, "post", fake_post)
    text = _extractor().extract_text_from_image(image_bytes, "", mime_type)
    if "payload" not in sent:
        return None, text
    content = sent["payload"]["messages"][0]["content"]
    image_part = next(part for part in content if part["type"] == "image_url")
    return image_part["dataUri"]["data"], text


def test_data_uri_has_mime_prefix(monkeypatch):
    """순수 base64가 아니라 data URI 형태로 보내야 HCX가 이미지를 받아준다."""
    data_uri, text = _captured_data_uri(monkeypatch)

    assert data_uri.startswith("data:image/png;base64,")
    assert data_uri.split(",", 1)[1] == base64.b64encode(PNG_BYTES).decode("utf-8")
    assert text == "슬라이드에 적힌 글자"


def test_data_uri_uses_actual_mime_type(monkeypatch):
    """PPT에서 온 실제 content_type을 그대로 써야 한다(전부 png로 우기면 안 됨)."""
    data_uri, _ = _captured_data_uri(monkeypatch, mime_type="image/jpeg", image_bytes=b"\xff\xd8\xff-jpeg")

    assert data_uri.startswith("data:image/jpeg;base64,")


def test_http_error_body_is_surfaced(monkeypatch, capsys):
    """400이 나면 응답 본문을 로그로 남겨야 원인 파악이 된다(조용한 실패 방지)."""

    def fake_post(url, headers=None, json=None, timeout=None):
        return _FakeResponse(status_code=400, text='{"status":{"code":"40001","message":"Invalid parameter"}}')

    monkeypatch.setattr(vision_module.requests, "post", fake_post)

    assert _extractor().extract_text_from_image(PNG_BYTES, "", "image/png") == ""
    assert "40001" in capsys.readouterr().out


def test_unsupported_format_is_converted_or_skipped(monkeypatch):
    """HCX가 못 읽는 포맷(EMF 등)은 PNG로 변환하거나, 안 되면 호출 없이 건너뛴다."""
    data_uri, text = _captured_data_uri(monkeypatch, mime_type="image/x-emf", image_bytes=b"not-a-real-emf")

    # 변환 실패 → 네트워크 호출 자체를 하지 않고 빈 문자열
    assert data_uri is None
    assert text == ""


def test_oversized_image_is_skipped(monkeypatch):
    """20MB 제한을 넘는 이미지는 보내봐야 거절당하므로 미리 거른다."""
    huge = b"\x89PNG\r\n\x1a\n" + b"x" * vision_module.MAX_IMAGE_BYTES
    data_uri, text = _captured_data_uri(monkeypatch, image_bytes=huge)

    assert data_uri is None
    assert text == ""


def test_fallback_mode_makes_no_network_call(monkeypatch):
    """키가 없으면 호출 없이 빈 문자열(기존 안전 모드 유지)."""

    def exploding_post(*args, **kwargs):
        raise AssertionError("안전 모드에서는 네트워크 호출을 하면 안 됩니다.")

    monkeypatch.setattr(vision_module.requests, "post", exploding_post)

    extractor = ImageTextExtractor()
    extractor.use_fallback = True
    assert extractor.extract_text_from_image(PNG_BYTES, "", "image/png") == ""
