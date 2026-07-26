import docx


def extract_full_text(file_path: str) -> str:
    """
    DOCX 문서의 모든 문단 텍스트를 순서대로 이어붙인다.
    이미 완성된 발표 대본을 DOCX로 올린 경우(발음 코칭 플로우)에 쓴다.
    """
    document = docx.Document(file_path)
    paragraphs = [p.text.strip() for p in document.paragraphs if p.text.strip()]
    return "\n\n".join(paragraphs)
