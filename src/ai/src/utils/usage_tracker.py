import os
import json
import threading
from datetime import datetime

# 대본 생성이 슬라이드별로 병렬 호출되면서 log_hcx_call이 여러 스레드에서 동시에 불린다.
# 각 log 함수는 상태 파일을 read-modify-write 하므로, 락이 없으면 갱신이 유실되거나
# .usage_state.json이 깨진다. 모든 로깅을 이 락으로 직렬화한다.
_state_lock = threading.Lock()

# speako-ai-server/
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
USAGE_LOG_PATH = os.path.join(BASE_DIR, "Token.md")
USAGE_STATE_PATH = os.path.join(BASE_DIR, ".usage_state.json")

# 정확한 단가(원/달러)를 콘솔에서 확인하면 여기 채워 넣으세요. None이면 로그에 "TBD"로 표시됩니다.
# HCX-005 (2026-07-19 사용자가 콘솔에서 직접 확인한 값, VAT 별도)
HCX_INPUT_PRICE_PER_1M_TOKENS_KRW = 1250
HCX_OUTPUT_PRICE_PER_1M_TOKENS_KRW = 5000
KRW_VAT_RATE = 0.10  # 국내 부가세 10% — 콘솔 단가가 "VAT 별도"라 계산 시 더해서 함께 표시

AZURE_SPEECH_PRICE_PER_HOUR_USD = None
CLOVA_VOICE_PRICE_PER_CHAR_KRW = None

_DEFAULT_STATE = {
    "hcx": {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    "etri": {"calls": 0},
    "azure_speech": {"calls": 0, "audio_seconds": 0.0},
    "clova_voice": {"calls": 0, "characters": 0},
    "stdict": {"calls": 0},
    "rows": [],
}


def _load_state():
    state = json.loads(json.dumps(_DEFAULT_STATE))
    if os.path.exists(USAGE_STATE_PATH):
        try:
            with open(USAGE_STATE_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            # 이전 버전 state 파일에 없던 키(예: 새로 추가된 서비스)를 기본값으로 채운다.
            for key, value in saved.items():
                state[key] = value
        except (json.JSONDecodeError, OSError):
            pass
    return state


def _append_row(state, service, detail):
    state["rows"].append({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "service": service,
        "detail": detail,
    })


def _cost_or_tbd(value):
    return "TBD (단가 미확인)" if value is None else value


def _hcx_cost_str(hcx):
    cost_excl_vat = (
        hcx["prompt_tokens"] / 1_000_000 * HCX_INPUT_PRICE_PER_1M_TOKENS_KRW
        + hcx["completion_tokens"] / 1_000_000 * HCX_OUTPUT_PRICE_PER_1M_TOKENS_KRW
    )
    cost_incl_vat = cost_excl_vat * (1 + KRW_VAT_RATE)
    return f"{cost_excl_vat:,.1f}원 (VAT 별도) / {cost_incl_vat:,.1f}원 (VAT 포함)"


def _rewrite_log(state):
    hcx = state["hcx"]
    azure = state["azure_speech"]
    voice = state["clova_voice"]
    etri = state["etri"]
    stdict = state["stdict"]

    hcx_cost = _hcx_cost_str(hcx)
    azure_cost = (
        _cost_or_tbd(AZURE_SPEECH_PRICE_PER_HOUR_USD)
        if AZURE_SPEECH_PRICE_PER_HOUR_USD is None
        else round(azure["audio_seconds"] / 3600 * AZURE_SPEECH_PRICE_PER_HOUR_USD, 4)
    )
    voice_cost = (
        _cost_or_tbd(CLOVA_VOICE_PRICE_PER_CHAR_KRW)
        if CLOVA_VOICE_PRICE_PER_CHAR_KRW is None
        else round(voice["characters"] * CLOVA_VOICE_PRICE_PER_CHAR_KRW)
    )

    lines = [
        "# API 사용량 로그",
        "",
        "각 외부 API를 호출할 때마다 자동으로 기록됩니다. HCX는 실제 단가가 적용되어 비용이 계산되고,",
        "Azure Speech / Clova Voice는 단가를 `src/utils/usage_tracker.py`에 채우기 전까지 TBD로 표시됩니다.",
        "이미지 속 텍스트 인식(HCX 비전)도 HCX 호출량에 함께 집계됩니다.",
        "",
        "## 누적 합계",
        "",
        "| 서비스 | 호출 수 | 사용량 | 예상 비용 |",
        "|---|---|---|---|",
        f"| HCX (CLOVA Studio, 비전 포함) | {hcx['calls']} | 총 {hcx['total_tokens']:,} tokens (prompt {hcx['prompt_tokens']:,} / completion {hcx['completion_tokens']:,}) | {hcx_cost} |",
        f"| Azure Speech (발음 평가) | {azure['calls']} | 총 오디오 {azure['audio_seconds']:.1f}초 | {azure_cost} |",
        f"| Clova Voice (TTS) | {voice['calls']} | 총 {voice['characters']:,}자 | {voice_cost} |",
        f"| ETRI (형태소 분석) | {etri['calls']} | - | 무료 |",
        f"| 표준국어대사전 (장단음 조회) | {stdict['calls']} | - | 무료 |",
        "",
        "## 호출 기록",
        "",
        "| 시각 | 서비스 | 상세 |",
        "|---|---|---|",
    ]
    for row in state["rows"]:
        lines.append(f"| {row['time']} | {row['service']} | {row['detail']} |")
    lines.append("")

    with open(USAGE_LOG_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def _save_and_write(state):
    with open(USAGE_STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    _rewrite_log(state)


def log_hcx_call(kind: str, prompt_tokens: int, completion_tokens: int, total_tokens: int):
    with _state_lock:
        state = _load_state()
        state["hcx"]["calls"] += 1
        state["hcx"]["prompt_tokens"] += prompt_tokens
        state["hcx"]["completion_tokens"] += completion_tokens
        state["hcx"]["total_tokens"] += total_tokens
        _append_row(state, "HCX", f"{kind} 생성 — prompt {prompt_tokens} + completion {completion_tokens} = {total_tokens} tokens")
        _save_and_write(state)


def log_etri_call():
    with _state_lock:
        state = _load_state()
        state["etri"]["calls"] += 1
        _append_row(state, "ETRI", "형태소 분석 호출 (무료)")
        _save_and_write(state)


def log_azure_speech_call(audio_seconds: float, status: str):
    with _state_lock:
        state = _load_state()
        state["azure_speech"]["calls"] += 1
        state["azure_speech"]["audio_seconds"] += audio_seconds
        _append_row(state, "Azure Speech", f"발음 평가 — 오디오 {audio_seconds:.1f}초 ({status})")
        _save_and_write(state)


def log_stdict_call():
    with _state_lock:
        state = _load_state()
        state["stdict"]["calls"] += 1
        _append_row(state, "표준국어대사전", "장단음 조회 호출 (무료)")
        _save_and_write(state)


def log_clova_voice_call(characters: int):
    with _state_lock:
        state = _load_state()
        state["clova_voice"]["calls"] += 1
        state["clova_voice"]["characters"] += characters
        _append_row(state, "Clova Voice", f"TTS 합성 — {characters}자")
        _save_and_write(state)
