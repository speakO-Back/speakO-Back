import os
import requests
from dotenv import load_dotenv

from utils.usage_tracker import log_clova_voice_call

# .env 파일 로드
load_dotenv()

REQUEST_TIMEOUT_SECONDS = 30

class ClovaVoiceClient:
    def __init__(self):
        # 네이버 클라우드 플랫폼(NCP) Clova Voice Premium API 인증 정보
        self.client_id = os.getenv("CLOVA_VOICE_CLIENT_ID")
        self.client_secret = os.getenv("CLOVA_VOICE_CLIENT_SECRET")
        
        # Clova Voice Premium 엔드포인트 URL
        self.endpoint = "https://naveropenapi.apigw.ntruss.com/tts-premium/v1/tts"
        
        # API 키가 세팅되지 않았을 때를 위한 안전 모드(Fallback) 플래그
        self.use_fallback = not self.client_id or "여기에_" in self.client_id
        
        if self.use_fallback:
            print("⚠️ [경고] Clova Voice API 키가 설정되지 않았습니다.")
            print("⚠️ 서버 구동 및 테스트를 위해 안전 모드(Mock)로 작동합니다.\n")

    def synthesize_word(self, text: str, output_filename: str = "output.mp3") -> str:
        """
        텍스트를 입력받아 Clova Voice로 합성한 뒤 MP3 파일로 저장하고 경로를 반환합니다.
        """
        if self.use_fallback:
            return self._mock_synthesis(text, output_filename)

        headers = {
            "X-NCP-APIGW-API-KEY-ID": self.client_id,
            "X-NCP-APIGW-API-KEY": self.client_secret,
            "Content-Type": "application/x-www-form-urlencoded"
        }

        # speaker 'ndain'은 신뢰감 있는 톤의 한국어 여성 아나운서 음색입니다.
        data = {
            "speaker": "ndain",
            "volume": "0",
            "speed": "0",
            "pitch": "0",
            "format": "mp3",
            "text": text
        }

        print(f"🚀 Clova Voice에 발음 합성을 요청합니다: '{text}'")
        
        try:
            response = requests.post(self.endpoint, headers=headers, data=data, timeout=REQUEST_TIMEOUT_SECONDS)
            
            if response.status_code == 200:
                # 응답으로 온 바이너리 데이터를 mp3 파일로 저장
                with open(output_filename, 'wb') as f:
                    f.write(response.content)
                log_clova_voice_call(len(text))
                print(f"✅ 음성 합성 완료! 파일 저장됨: {output_filename}")
                return output_filename
            else:
                print(f"❌ Clova Voice API 에러: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            print(f"❌ 음성 합성 중 내부 시스템 오류 발생: {str(e)}")
            return None

    def _mock_synthesis(self, text: str, output_filename: str) -> str:
        """안전 모드(Fallback): API 호출 없이 빈 MP3 파일을 생성하는 척 합니다."""
        import time
        time.sleep(1) # 실제 API 통신처럼 1초 딜레이
        
        # 실제로는 가짜(빈) 파일을 만들어서 에러를 막습니다.
        with open(output_filename, 'wb') as f:
            f.write(b"mock_mp3_data_for_testing")
            
        print(f"✅ [Mock] 가짜 음성 합성 완료 (과금 안됨): '{text}' -> {output_filename}")
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