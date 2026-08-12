"""피그마 화면이 요구하는 응답 형태를 엔드포인트 수준에서 고정한다.

근거 화면:
- `Coach View Page-4` (단어 목록 탭): 카테고리 뱃지 + 단어 › [발음] + 설명 문구, 발음에 장음(ː)
- `Coach View Page` (발음 팁): 아이콘 + 제목 + 설명 3개
- `Feedback Page`: 틀린 부분을 원본·인식 **양쪽에** 강조 → 위치 정보 필요
"""
import io

from fastapi.testclient import TestClient

import main
from main import app
from db import models

client = TestClient(app)


def _project_with_script(db_session_factory, script):
    db = db_session_factory()
    try:
        project = models.Project(name="피그마 테스트", filename=None, topic=None, keywords=[])
        project.slides = [models.Slide(slide_number=1, source_content="원문", script=script)]
        db.add(project)
        db.commit()
        db.refresh(project)
        return project.id
    finally:
        db.close()


# ------------------------------------------------------- 단어 목록 (Coach View Page-4)

def test_word_list_includes_description(db_session_factory):
    """피그마는 단어마다 설명 문구를 보여준다. 카테고리 이름만으로는 화면을 채울 수 없다."""
    project_id = _project_with_script(db_session_factory, "국물과 신라의 발음을 연습합니다.")

    response = client.post("/api/analysis/words", json={"project_id": project_id})
    assert response.status_code == 200

    words = response.json()["data"]["words"]
    assert words, "단어가 하나도 안 나왔습니다"
    assert all("description" in w for w in words)

    by_word = {w["word"]: w for w in words}
    if "국물" in by_word:
        assert by_word["국물"]["description"].startswith("비음화:")
    if "신라" in by_word:
        assert by_word["신라"]["description"].startswith("유음화:")


def test_long_vowel_mark_appears_in_phoneme(monkeypatch, db_session_factory):
    """장단음으로 분류해놓고 어디를 길게 읽는지 안 알려주면 그 카테고리가 무의미하다."""
    project_id = _project_with_script(db_session_factory, "구성 요소를 살펴봅니다.")
    monkeypatch.setattr(
        main.stdict_client, "long_vowel_positions",
        lambda word: (0,) if word == "구성" else (),
    )

    response = client.post("/api/analysis/words", json={"project_id": project_id})
    by_word = {w["word"]: w for w in response.json()["data"]["words"]}

    assert "구성" in by_word, "테스트 대상 단어가 추출되지 않았습니다"
    assert by_word["구성"]["phoneme"] == "[구ː성]"
    assert by_word["구성"]["category"] == "장단음"
    assert "첫 음절" in by_word["구성"]["description"]


def test_description_is_persisted_and_returned_on_project_read(db_session_factory):
    """단어 목록은 저장된 스냅샷으로도 조회된다. 조회 때마다 사전을 다시 때리지 않도록 함께 저장한다."""
    project_id = _project_with_script(db_session_factory, "국물을 준비합니다.")
    client.post("/api/analysis/words", json={"project_id": project_id})

    detail = client.get(f"/api/projects/{project_id}")
    stored = detail.json()["data"]["difficult_words"]
    assert stored, "저장된 단어가 없습니다"
    assert all("description" in w for w in stored)


# --------------------------------------------------- 오답 위치 (Feedback Page)

def _evaluate_with(monkeypatch, db_session_factory, script, recognized, words_detail):
    project_id = _project_with_script(db_session_factory, script)

    def _fake_convert(input_path, output_path):
        with open(output_path, "wb") as f:
            f.write(b"wav")
        return True

    monkeypatch.setattr(main.audio_converter, "convert_to_wav", _fake_convert)
    monkeypatch.setattr(
        main.azure_evaluator, "evaluate_audio",
        lambda audio_file_path, reference_text: {
            "status": "success",
            "overall_scores": {"accuracy": 80.0, "fluency": 80.0, "completeness": 80.0, "pronunciation_score": 80.0},
            "words_detail": words_detail,
            "recognized_text": recognized,
        },
    )
    return client.post(
        "/api/evaluation/audio",
        data={"project_id": str(project_id)},
        files={"audio_file": ("rec.webm", io.BytesIO(b"fake"), "audio/webm")},
    )


def test_error_spans_point_into_both_texts(monkeypatch, db_session_factory):
    script = "안녕하세요 여러분 반갑습니다"
    recognized = "안녕하세요 여러분 반값습니다"
    words = [
        {"word": "안녕하세요", "accuracy_score": 95.0, "error_type": "None"},
        {"word": "여러분", "accuracy_score": 92.0, "error_type": "None"},
        {"word": "반갑습니다", "accuracy_score": 40.0, "error_type": "Mispronunciation"},
    ]
    response = _evaluate_with(monkeypatch, db_session_factory, script, recognized, words)
    assert response.status_code == 200
    body = response.json()

    # 오프셋은 응답의 reference_text/recognized_text 기준이다. 대본을 이어붙일 때 "Slide N:"
    # 접두어가 붙으므로, 프론트가 가진 원본 문자열로 자르면 엉뚱한 곳이 나온다.
    detail = {w["word"]: w for w in body["words_detail"]}
    start, end = detail["여러분"]["reference_span"]
    assert body["reference_text"][start:end] == "여러분"
    start, end = detail["여러분"]["recognized_span"]
    assert body["recognized_text"][start:end] == "여러분"


def test_reference_text_is_returned_so_offsets_are_usable(monkeypatch, db_session_factory):
    """reference_span이 가리키는 문자열을 같이 주지 않으면 오프셋이 쓸모없다."""
    words = [{"word": "안녕하세요", "accuracy_score": 95.0, "error_type": "None"}]
    response = _evaluate_with(monkeypatch, db_session_factory, "안녕하세요", "안녕하세요", words)
    assert "reference_text" in response.json()


def test_repeated_word_gets_distinct_spans(monkeypatch, db_session_factory):
    """같은 단어가 여러 번 나오면 error_type만으론 어느 것을 칠할지 알 수 없다 — 이게 위치가 필요한 이유다."""
    script = "발표 준비 발표 시작"
    words = [
        {"word": "발표", "accuracy_score": 95.0, "error_type": "None"},
        {"word": "준비", "accuracy_score": 90.0, "error_type": "None"},
        {"word": "발표", "accuracy_score": 30.0, "error_type": "Mispronunciation"},
    ]
    response = _evaluate_with(monkeypatch, db_session_factory, script, script, words)
    body = response.json()
    reference = body["reference_text"]
    spans = [w["reference_span"] for w in body["words_detail"]]

    assert spans[0] != spans[2], "두 번째 '발표'가 첫 번째와 같은 위치를 가리킵니다"
    assert reference[spans[0][0]:spans[0][1]] == "발표"
    assert reference[spans[2][0]:spans[2][1]] == "발표"
    assert spans[2][0] > spans[0][0], "두 번째 '발표'가 앞쪽을 가리킵니다"


def test_omission_and_insertion_only_appear_on_their_own_side(monkeypatch, db_session_factory):
    script = "안녕하세요 여러분"
    recognized = "안녕하세요 그리고"
    words = [
        {"word": "안녕하세요", "accuracy_score": 95.0, "error_type": "None"},
        {"word": "여러분", "accuracy_score": 0.0, "error_type": "Omission"},
        {"word": "그리고", "accuracy_score": 0.0, "error_type": "Insertion"},
    ]
    response = _evaluate_with(monkeypatch, db_session_factory, script, recognized, words)
    detail = {w["word"]: w for w in response.json()["words_detail"]}

    # 빠뜨린 단어는 원본에만 있다.
    assert detail["여러분"]["reference_span"] is not None
    assert detail["여러분"]["recognized_span"] is None
    # 덧붙인 단어는 실제로 말한 쪽에만 있다.
    assert detail["그리고"]["reference_span"] is None
    assert detail["그리고"]["recognized_span"] is not None


# ------------------------------------------------------ 발음 팁 (Coach View Page)

def test_practice_tips_are_structured(monkeypatch, db_session_factory):
    """아이콘을 고르려면 안정적인 key가, 화면을 채우려면 제목과 설명 분리가 필요하다."""
    from clova.feedback.generator import parse_feedback_sections

    parsed = parse_feedback_sections(
        "[총평]\n또렷합니다.\n\n[잘한 점]\n- 좋아요.\n\n[개선할 점]\n- 받침을 살리세요.\n\n"
        "[연습 팁]\n"
        "- consonant | 명확한 자음 발음 | 'ㄷ, ㅈ, ㅅ' 계열을 또렷하게 발음해보세요.\n"
        "- ending | 끝소리 주의 | 단어의 끝소리를 자연스럽게 마무리해보세요.\n"
    )
    tips = parsed["practice_tips"]
    assert tips[0] == {
        "key": "consonant",
        "title": "명확한 자음 발음",
        "description": "'ㄷ, ㅈ, ㅅ' 계열을 또렷하게 발음해보세요.",
    }
    assert tips[1]["key"] == "ending"


def test_unknown_tip_key_falls_back_to_general():
    from clova.feedback.generator import normalize_practice_tips

    tips = normalize_practice_tips(["없는분류 | 제목 | 설명입니다."])
    assert tips[0]["key"] == "general"


def test_speed_is_an_allowed_tip_key():
    """PM 확정: 속도는 아이콘을 추가해서 쓴다."""
    from clova.feedback.generator import normalize_practice_tips

    tips = normalize_practice_tips(["speed | 말하기 속도 | 조금 천천히 말해보세요."])
    assert tips[0]["key"] == "speed"


def test_volume_is_not_an_allowed_tip_key():
    """PM 확정: 성량은 제외. 녹음 음량은 마이크 거리에 좌우되고, 애초에 Azure가 성량 데이터를
    주지 않아 팁을 쓰면 근거 없이 지어내는 것이 된다."""
    from clova.feedback.generator import normalize_practice_tips

    tips = normalize_practice_tips(["volume | 목소리 크기 | 조금 더 크게 말해보세요."])
    assert tips[0]["key"] == "general", "성량이 정식 분류로 통과되고 있습니다"


def test_old_cached_string_tips_still_readable(db_session_factory):
    """형식을 바꾸기 전에 캐시된 피드백이 있다. 못 읽으면 지난 평가 화면이 깨진다."""
    project_id = _project_with_script(db_session_factory, "대본")
    db = db_session_factory()
    try:
        evaluation = models.PronunciationEvaluation(
            project_id=project_id, accuracy_score=80.0, fluency_score=80.0,
            completeness_score=80.0, pronunciation_score=80.0,
            feedback={"summary": "좋습니다", "strengths": [], "improvements": [],
                      "practice_tips": ["천천히 3번 읽어보세요."]},
        )
        db.add(evaluation)
        db.commit()
        db.refresh(evaluation)
        evaluation_id = evaluation.id
    finally:
        db.close()

    response = client.post(f"/api/evaluation/{evaluation_id}/feedback")
    assert response.status_code == 200
    assert response.json()["cached"] is True

    tips = response.json()["data"]["practice_tips"]
    assert tips == [{"key": "general", "title": "", "description": "천천히 3번 읽어보세요."}]


def test_word_list_excludes_words_with_no_pronunciation_issue(db_session_factory):
    """분류가 없다는 건 '발음상 주의할 게 없다'는 뜻이다(_classify_word_category 정의).
    그런 단어를 목록에 넣으면 피그마 화면에서 뱃지도 설명도 빈 줄이 된다.
    실측(2026-08-06): 제로 녹음 대본에서 40개 중 25개가 여기 해당했다."""
    project_id = _project_with_script(
        db_session_factory,
        "생각과 노력으로 국물을 준비하는 타인의 사상을 살펴봅니다.",
    )

    response = client.post("/api/analysis/words", json={"project_id": project_id})
    words = response.json()["data"]["words"]

    assert all(w["category"] for w in words), \
        f"분류 없는 단어가 목록에 있습니다: {[w['word'] for w in words if not w['category']]}"
    assert all(w["description"] for w in words), "설명이 빈 단어가 목록에 있습니다"


def test_summary_matches_returned_word_count(db_session_factory):
    """요약 집계와 실제 목록이 어긋나면 화면의 '장단음 12개' 같은 숫자가 거짓말이 된다."""
    project_id = _project_with_script(db_session_factory, "국물과 신라의 발음을 연습합니다.")

    data = client.post("/api/analysis/words", json={"project_id": project_id}).json()["data"]

    assert sum(data["summary"].values()) == len(data["words"])


def test_practice_tips_keep_all_four_categories():
    """피그마 갱신본(2026-08-09) ㊹는 팁 카드를 **4개** 그린다 — 자음/끝소리/강세억양/속도.

    ⚠️ 예전엔 3개로 자르는 테스트였다. 8/06 시점 피그마가 카드 3개였기 때문인데, 그 상한이
    남아 있으면 분류 하나(대개 speed)가 통째로 잘려 나간다(실측: 제로 녹음 피드백에서 속도 팁 누락).
    """
    from clova.feedback.generator import normalize_practice_tips

    tips = normalize_practice_tips([
        "consonant | 자음 | 설명1.",
        "ending | 끝소리 | 설명2.",
        "intonation | 억양 | 설명3.",
        "speed | 속도 | 설명4.",
    ])

    assert len(tips) == 4
    assert [t["key"] for t in tips] == ["consonant", "ending", "intonation", "speed"]


def test_practice_tips_are_capped_at_four():
    """모델이 5줄 이상 주면 레이아웃이 깨지므로 코드로 자른다."""
    from clova.feedback.generator import normalize_practice_tips

    tips = normalize_practice_tips([f"consonant | 제목{i} | 설명{i}." for i in range(8)])

    assert len(tips) == 4
