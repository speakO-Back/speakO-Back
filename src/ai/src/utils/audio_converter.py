import subprocess

FFMPEG_TIMEOUT_SECONDS = 60
FFPROBE_TIMEOUT_SECONDS = 15


def probe_duration_seconds(input_path: str):
    """ffprobe로 오디오 길이(초)를 잰다. 알 수 없으면 None.

    ## 왜 변환 전에 재나
    길이 상한을 넘는 파일을 변환하고 Azure까지 보내면 **그 비용이 다 나간 뒤에** 거절하게 된다.
    ffprobe는 컨테이너 헤더만 읽으므로 파일이 커도 즉시 끝난다(실측: 5분 m4a에 0.0초대).

    ## None을 돌려주는 경우 호출부는 통과시켜야 한다
    포맷을 못 읽는 건 "길이가 길다"는 뜻이 아니다. 여기서 막으면 ffprobe가 모르는 정상 파일이
    통째로 거절된다. 길이를 모르는 파일은 그대로 보내고, 그때는 Azure 인식 타임아웃이
    최후 방어선 역할을 한다(azure_client.CONTINUOUS_RECOGNITION_TIMEOUT_SECONDS).
    """
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", input_path],
            capture_output=True,
            timeout=FFPROBE_TIMEOUT_SECONDS,
        )
        if result.returncode != 0:
            return None
        return float(result.stdout.decode("utf-8", "replace").strip())
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError) as e:
        print(f"⚠️ 오디오 길이 측정 실패(길이 검사를 건너뜁니다): {e}")
        return None


def convert_to_wav(input_path: str, output_path: str) -> bool:
    """
    ffmpeg로 오디오 파일을 Azure Pronunciation Assessment가 요구하는
    16kHz mono PCM WAV로 변환한다. 성공하면 True, 실패하면 False를 반환한다.
    """
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", input_path, "-ar", "16000", "-ac", "1", "-f", "wav", output_path],
            capture_output=True,
            timeout=FFMPEG_TIMEOUT_SECONDS,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        print(f"❌ ffmpeg 오디오 변환 실패: {e}")
        return False
