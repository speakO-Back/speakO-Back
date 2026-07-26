from utils import stdict_client as stdict_module
from utils.stdict_client import StdictClient


class _FakeResponse:
    def __init__(self, json_data=None, text=""):
        self._json_data = json_data
        self.text = text
        # 클라이언트는 이제 response.content(bytes)를 파싱하며, 빈 본문이면 매칭 없음으로 처리한다.
        # JSON 응답(search.do)은 본문이 비어있지 않으므로 content가 non-empty가 되도록 한다.
        if text:
            self.content = text.encode("utf-8") if isinstance(text, str) else text
        elif json_data is not None:
            self.content = b"{...json...}"
        else:
            self.content = b""

    def raise_for_status(self):
        pass

    def json(self):
        return self._json_data


SEARCH_JSON = {"channel": {"item": [{"word": "눈", "target_code": "71074"}]}}

VIEW_XML_LONG = """<channel><item><target_code>71074</target_code><word_info>
<pronunciation_info><pronunciation>눈ː</pronunciation></pronunciation_info>
</word_info></item></channel>"""

VIEW_XML_SHORT = """<channel><item><target_code>409998</target_code><word_info>
<pronunciation_info><pronunciation>눈</pronunciation></pronunciation_info>
</word_info></item></channel>"""


def test_has_long_vowel_disabled_without_api_key(monkeypatch):
    monkeypatch.delenv("STDICT_API_KEY", raising=False)
    client = StdictClient()
    assert client.use_fallback is True
    assert client.has_long_vowel("눈") is False


def test_has_long_vowel_true_when_pronunciation_has_length_mark(monkeypatch):
    client = StdictClient()
    client.use_fallback = False
    client.api_key = "test-key"

    responses = [_FakeResponse(json_data=SEARCH_JSON), _FakeResponse(text=VIEW_XML_LONG)]
    monkeypatch.setattr(stdict_module.requests, "get", lambda *a, **k: responses.pop(0))

    assert client.has_long_vowel("눈") is True


def test_has_long_vowel_false_when_pronunciation_has_no_length_mark(monkeypatch):
    client = StdictClient()
    client.use_fallback = False
    client.api_key = "test-key"

    responses = [_FakeResponse(json_data=SEARCH_JSON), _FakeResponse(text=VIEW_XML_SHORT)]
    monkeypatch.setattr(stdict_module.requests, "get", lambda *a, **k: responses.pop(0))

    assert client.has_long_vowel("눈") is False


def test_has_long_vowel_false_when_search_finds_nothing(monkeypatch):
    client = StdictClient()
    client.use_fallback = False
    client.api_key = "test-key"

    monkeypatch.setattr(stdict_module.requests, "get", lambda *a, **k: _FakeResponse(json_data={"channel": {}}))

    assert client.has_long_vowel("존재하지않는단어") is False
