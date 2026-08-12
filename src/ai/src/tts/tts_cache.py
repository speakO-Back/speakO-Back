"""합성된 MP3 디스크 캐시.

## 왜 필요한가
Clova Voice는 **글자 수만큼 과금**된다. 그런데 발음 주의 단어는 프로젝트마다 겹친다 —
'역할', '권리', '책임' 같은 단어는 발표 주제가 달라도 반복해서 나오고, 사용자가 단어 목록에서
스피커 버튼을 여러 번 누르면 같은 요청이 그대로 또 나간다. 캐시가 없으면 **완전히 같은 소리를
만드는 데 매번 돈이 나간다.**

## 왜 파일인가
프로세스 메모리에 두면 (1) 재시작하면 날아가고 (2) 워커를 늘리면 워커마다 따로 캐시를 쌓아
적중률이 떨어진다. 오디오는 단어 하나에 8KB 안팎이라 디스크에 두는 비용이 사실상 없다.
(같은 이유로 `job_store`도 메모리에서 DB로 옮긴 적이 있다 — db/models.py ScriptJob 주석 참고)

## 무한정 쌓이지 않게
파일 개수 상한(`MAX_ENTRIES`)을 넘으면 오래된 것부터 지운다. mtime 기준이라, 자주 눌리는
단어는 조회할 때마다 갱신되어(touch) 살아남는다.
"""
import hashlib
import os
import threading

# speako-ai-server/
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CACHE_DIR = os.path.join(BASE_DIR, "data", "tts_cache")

# 단어 하나가 8KB 안팎이므로 2000개여도 20MB 이하다. 넉넉히 잡아도 부담이 없다.
MAX_ENTRIES = 2000

_lock = threading.Lock()


def _cache_path(text: str, speaker: str, speed: int = 0) -> str:
    """같은 글자라도 화자·속도가 다르면 다른 소리이므로 둘 다 키에 포함한다.

    speed가 키에 없으면 -5로 들은 사용자가 +5 오디오를 받는(또는 그 반대) 스테일 적중이 난다.
    키 형식이 바뀌며 기존 캐시는 자연히 미스가 되는데, 단어당 한 번 재합성되고 마는 비용이라
    감수한다(배포 서버는 어차피 빈 캐시에서 시작).
    """
    digest = hashlib.sha256(f"{speaker}\x00{speed}\x00{text}".encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, f"{digest}.mp3")


def get(text: str, speaker: str, speed: int = 0) -> bytes:
    """캐시된 오디오를 돌려준다. 없으면 None."""
    path = _cache_path(text, speaker, speed)
    try:
        with open(path, "rb") as f:
            audio = f.read()
    except OSError:
        return None

    if not audio:
        return None

    # 최근 사용 표시(LRU). 실패해도 캐시 적중 자체는 유효하므로 무시한다.
    try:
        os.utime(path, None)
    except OSError:
        pass
    return audio


def put(text: str, speaker: str, audio: bytes, speed: int = 0) -> None:
    """오디오를 캐시에 저장한다. 저장에 실패해도 호출부는 계속 진행해야 한다(캐시는 부가 기능)."""
    if not audio:
        return

    path = _cache_path(text, speaker, speed)
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        # 임시 파일에 다 쓴 뒤 교체한다. 그냥 쓰면 쓰는 도중에 다른 요청이 읽어서
        # 잘린 오디오를 받을 수 있다(os.replace는 같은 파일시스템에서 원자적).
        temp_path = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        with open(temp_path, "wb") as f:
            f.write(audio)
        os.replace(temp_path, path)
    except OSError as e:
        print(f"⚠️ TTS 캐시 저장 실패(무시하고 진행): {e}")
        return

    _evict_if_needed()


def _evict_if_needed() -> None:
    with _lock:
        try:
            entries = [e for e in os.scandir(CACHE_DIR) if e.name.endswith(".mp3")]
        except OSError:
            return

        if len(entries) <= MAX_ENTRIES:
            return

        # 오래 안 쓴 것부터 지운다. 삭제 중 다른 워커가 먼저 지웠을 수 있으므로 개별 실패는 넘어간다.
        entries.sort(key=lambda e: e.stat().st_mtime)
        for entry in entries[: len(entries) - MAX_ENTRIES]:
            try:
                os.remove(entry.path)
            except OSError:
                pass


def clear() -> None:
    """테스트용. 캐시 디렉터리를 비운다."""
    with _lock:
        try:
            for entry in os.scandir(CACHE_DIR):
                try:
                    os.remove(entry.path)
                except OSError:
                    pass
        except OSError:
            pass
