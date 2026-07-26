import os
import threading
import wave
import azure.cognitiveservices.speech as speechsdk
from dotenv import load_dotenv

from utils.usage_tracker import log_azure_speech_call

# .env 파일 로드
load_dotenv()

CONTINUOUS_RECOGNITION_TIMEOUT_SECONDS = 300  # 여러 슬라이드를 이어 읽는 긴 녹음까지 대응


def _get_wav_duration_seconds(path: str) -> float:
    try:
        with wave.open(path, 'rb') as wf:
            rate = wf.getframerate()
            return wf.getnframes() / float(rate) if rate else 0.0
    except Exception:
        return 0.0


class PronunciationEvaluator:
    def __init__(self):
        self.speech_key = os.getenv("AZURE_SPEECH_KEY")
        self.service_region = os.getenv("AZURE_SPEECH_REGION")

        # API 키가 없거나 '여기에_' 같은 기본값이면 안전 모드(Fallback)로 작동
        self.use_fallback = not self.speech_key or "여기에_" in self.speech_key

        if self.use_fallback:
            print("⚠️ [경고] Azure Speech API 키가 설정되지 않았습니다.")
            print("⚠️ 프론트엔드 테스트를 위해 임시 모의(Mock) 데이터를 반환합니다.\n")

    def evaluate_audio(self, audio_file_path: str, reference_text: str):
        """
        사용자의 음성 파일(WAV)과 읽어야 할 대본 텍스트를 받아 발음을 평가합니다.

        연속 인식(continuous recognition)을 사용해서, 녹음 중간에 자연스러운 pause가 있어도
        거기서 멈추지 않고 파일 전체를 끝까지 듣는다. reference_text가 실제로 말한 것보다
        길어도(예: 여러 슬라이드 중 일부만 읽고 멈춘 경우) enable_miscue 옵션 덕분에 실제
        말한 부분까지만 정확히 점수를 매기고 나머지는 "Omission"으로 표시된다 — 어디까지
        읽었는지 미리 알려줄 필요 없이, 인식 결과를 보고 자동으로 판단한다.
        """
        if self.use_fallback:
            return self._mock_evaluation()

        audio_seconds = _get_wav_duration_seconds(audio_file_path)

        try:
            # 1. 오디오 설정 및 스피치 설정
            audio_config = speechsdk.audio.AudioConfig(filename=audio_file_path)
            speech_config = speechsdk.SpeechConfig(subscription=self.speech_key, region=self.service_region)

            # 한국어 설정
            speech_config.speech_recognition_language = "ko-KR"

            # 2. 발음 평가 옵션 설정 (기준 텍스트 제공, 실제로 말한 만큼만 채점하도록 miscue 허용)
            pronunciation_config = speechsdk.PronunciationAssessmentConfig(
                reference_text=reference_text,
                grading_system=speechsdk.PronunciationAssessmentGradingSystem.HundredMark,
                granularity=speechsdk.PronunciationAssessmentGranularity.Word,
                enable_miscue=True,
            )

            # 3. 인식기 생성 및 평가 모듈 결합
            speech_recognizer = speechsdk.SpeechRecognizer(speech_config=speech_config, audio_config=audio_config)
            pronunciation_config.apply_to(speech_recognizer)

            print(f"🚀 Azure 서버에 발음 평가를 요청합니다 (연속 인식, 기준 문장 길이: {len(reference_text)}자)...")

            words_data = []
            segment_scores = []
            done = threading.Event()
            cancel_messages = []

            def on_recognized(evt):
                if evt.result.reason == speechsdk.ResultReason.RecognizedSpeech and evt.result.text:
                    pron_result = speechsdk.PronunciationAssessmentResult(evt.result)
                    for word in pron_result.words:
                        words_data.append({
                            "word": word.word,
                            "accuracy_score": word.accuracy_score,
                            "error_type": word.error_type
                        })
                    segment_scores.append({
                        "accuracy": pron_result.accuracy_score,
                        "fluency": pron_result.fluency_score,
                        "completeness": pron_result.completeness_score,
                        "pronunciation_score": pron_result.pronunciation_score,
                        "word_count": len(pron_result.words) or 1,
                    })

            def on_canceled(evt):
                if evt.reason == speechsdk.CancellationReason.Error:
                    cancel_messages.append(evt.error_details)
                done.set()

            def on_session_stopped(evt):
                done.set()

            speech_recognizer.recognized.connect(on_recognized)
            speech_recognizer.canceled.connect(on_canceled)
            speech_recognizer.session_stopped.connect(on_session_stopped)

            # 4. 음성 인식 및 평가 실행 (파일 끝까지 연속 인식)
            speech_recognizer.start_continuous_recognition()
            done.wait(timeout=CONTINUOUS_RECOGNITION_TIMEOUT_SECONDS)
            speech_recognizer.stop_continuous_recognition()

            # 5. 결과 집계
            if cancel_messages:
                log_azure_speech_call(audio_seconds, "canceled")
                return {"status": "error", "message": f"평가 취소됨: {cancel_messages[0]}"}

            if not segment_scores:
                log_azure_speech_call(audio_seconds, "no_match")
                return {"status": "error", "message": "음성을 인식할 수 없습니다."}

            total_words = sum(s["word_count"] for s in segment_scores)

            def weighted_avg(key):
                return round(sum(s[key] * s["word_count"] for s in segment_scores) / total_words, 1)

            log_azure_speech_call(audio_seconds, "success")

            return {
                "status": "success",
                "overall_scores": {
                    "accuracy": weighted_avg("accuracy"),
                    "fluency": weighted_avg("fluency"),
                    "completeness": weighted_avg("completeness"),
                    "pronunciation_score": weighted_avg("pronunciation_score")
                },
                "words_detail": words_data
            }

        except Exception as e:
            log_azure_speech_call(audio_seconds, "error")
            return {"status": "error", "message": f"평가 중 오류 발생: {str(e)}"}

    def _mock_evaluation(self):
        """안전 모드(Fallback): API 키가 없을 때 반환하는 가짜 테스트 데이터"""
        import time
        time.sleep(1.5) # 실제 네트워크 호출처럼 약간의 딜레이

        return {
            "status": "success",
            "overall_scores": {
                "accuracy": 85.0,
                "fluency": 90.0,
                "completeness": 100.0,
                "pronunciation_score": 88.5
            },
            "words_detail": [
                {"word": "메타버스와", "accuracy_score": 95.0, "error_type": "None"},
                {"word": "인프라", "accuracy_score": 98.0, "error_type": "None"},
                {"word": "구축의", "accuracy_score": 60.0, "error_type": "Mispronunciation"},
                {"word": "특징을", "accuracy_score": 50.0, "error_type": "Mispronunciation"},
                {"word": "살펴봅시다.", "accuracy_score": 92.0, "error_type": "None"}
            ]
        }

# ==========================================
# 🧪 [테스트 코드]
# ==========================================
if __name__ == "__main__":
    evaluator = PronunciationEvaluator()

    # 더미 오디오 경로 (실제 실행 시에는 존재하는 wav 파일을 넣어야 합니다)
    test_audio_path = "test_recording.wav"
    test_text = "메타버스와 인프라 구축의 특징을 살펴봅시다."

    result = evaluator.evaluate_audio(test_audio_path, test_text)

    import json
    print("\n✨ [Azure 발음 평가 결과] ✨")
    print(json.dumps(result, indent=2, ensure_ascii=False))
