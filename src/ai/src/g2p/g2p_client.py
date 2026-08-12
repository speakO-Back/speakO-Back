import sys


class _KiwiMecabShim:
    """
    g2pkk가 요구하는 형태소 분석기(mecab) 자리를 Kiwi로 대신 채운다.

    왜 필요한가: g2pkk는 내부에서 mecab(Windows는 eunjeon)을 부르는데, eunjeon은 사전 파일이
    함께 설치되지 않아 로드에 실패한다(실측: MeCab dictionary does not exist). 그러면 G2P가
    통째로 폴백 사전으로 떨어져서, 발음기호가 철자와 똑같이 나온다(실측: 실시간 → [실시간]).

    g2pkk가 실제로 쓰는 건 `pos(문자열) -> [(형태소, 품사태그)]` 하나뿐이고(utils.annotate),
    토큰을 이어붙인 게 원문과 다르면 주석 없이 넘어가도록 이미 방어돼 있다. 즉 태그가 조금
    달라도 음운 규칙 처리는 정상 동작한다. Kiwi는 이미 이 프로젝트가 쓰는 분석기라 새 의존성도 없다.
    """

    def __init__(self):
        from kiwipiepy import Kiwi
        self._kiwi = Kiwi()

    def pos(self, string):
        return [(token.form, token.tag) for token in self._kiwi.tokenize(string)]


class G2pConverter:
    def __init__(self):
        """
        G2P 모델을 초기화합니다.
        g2pkk가 요구하는 mecab이 없으면 Kiwi로 대체해 살려 보고, 그것도 안 되면
        서버 다운을 막기 위해 자체 안전 모드(폴백 사전)로 전환합니다.
        """
        print("⏳ G2P 모델 로드를 시도합니다...")
        self.use_fallback = False

        try:
            from g2pkk import G2p

            # 1) 정상 경로로 세워 본다.
            try:
                self.g2p = G2p()
            except Exception as load_error:
                # Windows(eunjeon)는 사전이 없으면 여기서 예외가 난다.
                print(f"⚠️ g2pkk 형태소 분석기 로드 실패({load_error}). Kiwi로 대체합니다.")
                self.g2p = G2p.__new__(G2p)
                self._init_g2p_without_mecab(self.g2p)

            # 2) 실제로 변환이 되는지 확인한다. Linux 경로에서는 mecab이 없어도 예외 없이
            #    self.mecab = None인 채로 생성돼서, 호출할 때마다 터진다(생성 시점엔 안 보임).
            if not self._works(self.g2p):
                print("⚠️ g2pkk가 형태소 분석기 없이 올라왔습니다. Kiwi로 대체합니다.")
                self.g2p.mecab = _KiwiMecabShim()
                if not self._works(self.g2p):
                    raise RuntimeError("Kiwi로 대체했지만 g2pkk 변환이 여전히 실패합니다.")
                print("✅ G2P 모델 로드 완료! (형태소 분석기는 Kiwi로 대체)")
            else:
                print("✅ G2P 모델 로드 완료!")
        except Exception as e:
            print(f"\n⚠️ [경고] 호환성 문제로 g2pkk 라이브러리를 로드하지 못했습니다: {e}")
            print("⚠️ API 서버 구동을 위해 자체 G2P 사전(Fallback) 모드로 전환합니다.\n")
            self.use_fallback = True

        # 폴백 사전 — g2pkk가 아예 안 올라올 때만 쓰인다(발음기호가 철자와 같아지므로 최후 수단).
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

    @staticmethod
    def _works(g2p):
        """대표 단어 하나를 실제로 변환해 보고, 규칙이 적용되는지까지 확인한다."""
        try:
            return g2p("국물") == "궁물"
        except Exception:
            return False

    @staticmethod
    def _init_g2p_without_mecab(g2p):
        """G2p.__init__에서 mecab 준비 단계만 빼고 나머지 초기화(발음 규칙 표, 사전 등)를 그대로 수행한다."""
        import os
        from nltk.corpus import cmudict
        from g2pkk.regular import link1  # noqa: F401 (g2pkk 패키지 초기화 보장)
        from g2pkk.utils import parse_table, get_rule_id2text

        g2p.table = parse_table()
        g2p.cmu = cmudict.dict()
        g2p.rule2text = get_rule_id2text()
        g2p.idioms_path = os.path.join(os.path.dirname(os.path.abspath(sys.modules["g2pkk"].__file__)), "idioms.txt")

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