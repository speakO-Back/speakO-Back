import sys

class G2pConverter:
    def __init__(self):
        """
        G2P 모델을 초기화합니다.
        Windows 환경에서 g2pkk 로드 실패 시, 서버 다운을 막기 위해 자체 안전 모드로 전환합니다.
        """
        print("⏳ G2P 모델 로드를 시도합니다...")
        self.use_fallback = False
        
        try:
            from g2pkk import G2p
            self.g2p = G2p()
            print("✅ G2P 모델 로드 완료!")
        except Exception as e:
            print(f"\n⚠️ [경고] 호환성 문제로 g2pkk 라이브러리를 로드하지 못했습니다.")
            print("⚠️ API 서버 구동을 위해 자체 G2P 사전(Fallback) 모드로 전환합니다.\n")
            self.use_fallback = True

        # MVP 시연 및 프론트엔드 연동 테스트용 자체 발음 사전
        self.fallback_dict = {
            "특징": "특찡",
            "협력": "혐녁",
            "국민": "궁민",
            "효과": "효꽈",
            "역할": "여칼",
            "발전": "발쩐",
            "전략": "절략",
            "성공": "성공",
            "인프라": "인프라",
            "메타버스": "메타버스",
            # 비음화
            "국물": "궁물",
            "앞날": "암날",
            "대통령": "대통녕",
            "능력": "능녁",
            "종로": "종노",
            "왕십리": "왕심니",
            # 유음화
            "신라": "실라",
            "설날": "설랄",
            "칼날": "칼랄",
            "물난리": "물랄리",
            # 격음화
            "좋고": "조코",
            "많다": "만타",
            "않고": "안코",
            "축하": "추카",
            "국화": "구콰",
            "입학": "이팍",
            "백화점": "배콰점",
            # 구개음화
            "굳이": "구지",
            "같이": "가치",
            "밭이": "바치",
            # 경음화
            "갈등": "갈뜽"
        }

    def convert_words(self, words_list):
        """
        단어 리스트를 입력받아 발음 기호로 변환합니다.
        g2pkk가 실패한 경우, 자체 정의된 사전(fallback_dict)을 통해 값을 반환합니다.
        """
        converted_result = []
        
        for word in words_list:
            phoneme = word # 기본값은 원본 단어
            
            if not self.use_fallback:
                try:
                    phoneme = self.g2p(word)
                except:
                    phoneme = self._fallback_convert(word)
            else:
                phoneme = self._fallback_convert(word)
            
            # 발음 데이터 구조화
            word_data = {
                "word": word,
                "phoneme": f"[{phoneme}]",
                "is_different": word != phoneme 
            }
            converted_result.append(word_data)
            
        return converted_result
        
    def _fallback_convert(self, word):
        """안전 모드 시 작동하는 텍스트 변환기"""
        return self.fallback_dict.get(word, word)

# ==========================================
# 🧪 [테스트 코드]
# ==========================================
if __name__ == "__main__":
    converter = G2pConverter()
    
    sample_words = ["인프라", "특징", "협력", "국민", "효과", "메타버스"]
    results = converter.convert_words(sample_words)
    
    if results:
        print("\n✨ [G2P 발음 기호 변환 결과] ✨")
        for res in results:
            icon = "🔴" if res["is_different"] else "🟢"
            print(f"{icon} 단어: {res['word']} -> 발음: {res['phoneme']}")