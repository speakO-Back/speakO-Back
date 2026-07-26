import subprocess

FFMPEG_TIMEOUT_SECONDS = 60


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
