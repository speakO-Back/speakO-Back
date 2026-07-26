import pypdf


def extract_structured_data(file_path: str) -> dict:
    """
    PDF 파일 경로를 입력받아 ppt_extractor.PptExtractor.extract_structured_data와
    동일한 형태({"metadata": {...}, "slides": [...]})로 반환한다.
    슬라이드를 PDF로 내보낸 경우를 가정해 페이지 1개 = 슬라이드 1개로 취급한다.
    """
    reader = pypdf.PdfReader(file_path)
    slides = []
    for i, page in enumerate(reader.pages):
        text = (page.extract_text() or "").strip()
        if text:
            slides.append({"slide_number": i + 1, "content": text})

    topic = slides[0]["content"].splitlines()[0][:100] if slides else ""
    return {"metadata": {"topic": topic, "keywords": []}, "slides": slides}


def extract_full_text(file_path: str) -> str:
    """
    PDF의 모든 페이지 텍스트를 페이지 구분 없이 하나로 이어붙인다.
    슬라이드 덱이 아니라 이미 완성된 발표 대본(문서)을 PDF로 올린 경우(발음 코칭 플로우)에 쓴다.
    """
    reader = pypdf.PdfReader(file_path)
    pages_text = [(page.extract_text() or "").strip() for page in reader.pages]
    return "\n\n".join(text for text in pages_text if text)
