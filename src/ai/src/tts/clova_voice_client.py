import os
import requests
from dotenv import load_dotenv

from utils.usage_tracker import log_clova_voice_call

# .env 파일 로드
load_dotenv()

REQUEST_TIMEOUT_SECONDS = 30

# 안전 모드에서 돌려주는 가짜 오디오. 실제 MP3가 아니므로 브라우저에서 재생되지 않지만,
# 엔드포인트/캐시 경로를 키 없이 테스트하는 데는 충분하다.
MOCK_AUDIO_BYTES = b"mock_mp3_data_for_testing"


class ClovaVoiceClient:
    def __init__(self):
        # 네이버 클라우드 플랫폼(NCP) Clova Voice Premium API 인증 정보
        self.client_id = os.getenv("CLOVA_VOICE_CLIENT_ID")
        self.client_secret = os.getenv("CLOVA_VOICE_CLIENT_SECRET")

        # Clova Voice Premium 엔드포인트 URL
        self.endpoint = "https://naveropenapi.apigw.ntruss.com/tts-premium/v1/tts"

        # API 키가 세팅되지 않았을 때를 위한 안전 모드(Fallback) 플래그.
        # ID만 보고 판단하면 시크릿이 비었을 때 실호출로 새서 401을 맞으므로 둘 다 본다.
        self.use_fallback = (
            not self.client_id
            or not self.client_secret
            or "여기에_" in self.client_id
            or "여기에_" in self.client_secret
        )

        if self.use_fallback:
            print("⚠️ [경고] Clova Voice API 키가 설정되지 않았습니다.")
            print("⚠️ 서버 구동 및 테스트를 위해 안전 모드(Mock)로 작동합니다.\n")

    def synthesize_bytes(self, text: str, speaker: str = "ndain", speed: int = 0) -> bytes:
        """텍스트를 합성해 MP3 **바이트**를 돌려준다. 실패하면 None.

        API 응답으로 온 오디오를 굳이 파일로 떨어뜨렸다가 다시 읽을 이유가 없어서, HTTP
        핸들러가 쓰는 정식 경로는 이쪽이다. `synthesize_word`는 이 위에 파일 저장을 얹은 것이다.

        speed: Clova 기준 -5(2배 빠름)~10(0.5배 느림), 0=보통. 제품이 여는 범위(-5~+5)의
        검증은 요청 모델이 하고, 여기는 받은 값을 그대로 전달만 한다.

        ⚠️ requests는 완전 블로킹이다. `async def` 핸들러에서 직접 부르지 말고
        `run_in_threadpool`로 감쌀 것 (tests/test_blocking_offload.py가 이 계약을 지킨다).
        """
        if self.use_fallback:
            print(f"✅ [Mock] 가짜 음성 합성 (과금 안됨): '{text}'")
            return MOCK_AUDIO_BYTES

        headers = {
            "X-NCP-APIGW-API-KEY-ID": self.client_id,
            "X-NCP-APIGW-API-KEY": self.client_secret,
            "Content-Type": "application/x-www-form-urlencoded"
        }

        # speaker 'ndain'은 신뢰감 있는 톤의 한국어 여성 아나운서 음색입니다.
        data = {
            "speaker": speaker,
            "volume": "0",
            "speed": str(speed),
            "pitch": "0",
            "format": "mp3",
            "text": text
        }

        print(f"🚀 Clova Voice에 발음 합성을 요청합니다: '{text}'")

        try:
            response = requests.post(self.endpoint, headers=headers, data=data, timeout=REQUEST_TIMEOUT_SECONDS)

            if response.status_code == 200:
                log_clova_voice_call(len(text))
                print(f"✅ 음성 합성 완료! ({len(response.content)} bytes)")
                return response.content

            print(f"❌ Clova Voice API 에러: {response.status_code} - {response.text}")
            return None

        except Exception as e:
            print(f"❌ 음성 합성 중 내부 시스템 오류 발생: {str(e)}")
            return None

    def synthesize_word(self, text: str, output_filename: str = "output.mp3", speaker: str = "ndain") -> str:
        """
        텍스트를 입력받아 Clova Voice로 합성한 뒤 MP3 파일로 저장하고 경로를 반환합니다.
        (CLI/스크립트용. 서버 핸들러는 `synthesize_bytes`를 쓴다.)
        """
        audio = self.synthesize_bytes(text, speaker=speaker)
        if audio is None:
            return None

        with open(output_filename, 'wb') as f:
            f.write(audio)
        print(f"✅ 파일 저장됨: {output_filename}")
        return output_filename


# ==========================================
# 🧪 [테스트 코드]
# ==========================================
if __name__ == "__main__":
    tts_client = ClovaVoiceClient()

    # 합성할 테스트 단어
    test_word = "특징"
    save_path = "test_pronunciation.mp3"

    result_path = tts_client.synthesize_word(text=test_word, output_filename=save_path)

    if result_path:
        print(f"\n✨ 테스트 성공! 생성된 파일 경로: {result_path} ✨")
