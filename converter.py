"""
PDF2MD 변환 로직 (화면과 무관한 순수 처리 부분)
- 텍스트 PDF: 직접 추출
- 스캔본 PDF: EasyOCR로 한글+영문 인식
- 책 페이지 라벨이 있으면 [p. 45] · PDF 55 형식으로 함께 표시
"""

import io
from pathlib import Path

import fitz  # PyMuPDF

_reader = None


class ConversionCancelled(Exception):
    pass


def get_ocr_reader():
    """EasyOCR 리더를 한 번만 만들어 재사용한다. 처음에는 모델을 내려받는다."""
    global _reader
    if _reader is None:
        import easyocr
        _reader = easyocr.Reader(["ko", "en"], gpu=False, verbose=False)
    return _reader


def ocr_ready() -> bool:
    return _reader is not None


def get_page_label(doc, page_idx: int) -> str:
    try:
        return (doc[page_idx].get_label() or "").strip()
    except Exception:
        return ""


def has_meaningful_labels(doc) -> bool:
    for i in range(len(doc)):
        label = get_page_label(doc, i)
        if label and label != str(i + 1):
            return True
    return False


def page_header(page_num: int, label: str, has_labels: bool, fmt: str) -> str:
    if has_labels:
        if label and label != str(page_num):
            tag = f"[p. {label}] · PDF {page_num}"
        elif label:
            tag = f"[p. {page_num}]"
        else:
            tag = f"[PDF p. {page_num}]"
    else:
        tag = f"[p. {page_num}]"
    return f"\n---\n\n## {tag}\n" if fmt == "md" else f"\n----- {tag} -----\n"


def needs_ocr(doc, sample: int = 10, threshold: int = 200) -> bool:
    n = min(sample, len(doc))
    if n == 0:
        return False
    indices = [int(i * len(doc) / n) for i in range(n)]
    text_pages = sum(1 for i in indices if len(doc[i].get_text("text").strip()) >= threshold)
    return text_pages < n / 2


def clean_text(text: str) -> str:
    cleaned, blank = [], 0
    for line in text.split("\n"):
        s = line.rstrip()
        if not s:
            blank += 1
            if blank <= 1:
                cleaned.append("")
        else:
            blank = 0
            cleaned.append(s)
    return "\n".join(cleaned)


def ocr_page(reader, image_bytes: bytes) -> str:
    import numpy as np
    from PIL import Image

    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    results = reader.readtext(np.array(img), detail=1, paragraph=False)
    if not results:
        return ""

    items = []
    for box, text, _ in results:
        ys = [p[1] for p in box]
        xs = [p[0] for p in box]
        items.append({
            "text": text,
            "y_top": min(ys),
            "y_center": sum(ys) / 4,
            "x_left": min(xs),
            "height": max(ys) - min(ys),
        })

    items.sort(key=lambda d: d["y_top"])
    lines, cur, cur_y = [], [], None
    for it in items:
        if cur_y is None or abs(it["y_center"] - cur_y) < it["height"] * 0.7:
            cur.append(it)
            cur_y = it["y_center"]
        else:
            cur.sort(key=lambda d: d["x_left"])
            lines.append(" ".join(d["text"] for d in cur))
            cur, cur_y = [it], it["y_center"]
    if cur:
        cur.sort(key=lambda d: d["x_left"])
        lines.append(" ".join(d["text"] for d in cur))
    return "\n".join(lines)


def convert_pdf(pdf_path, fmt: str = "md", ocr_mode: str = "auto", dpi: int = 200,
                progress_cb=None, cancel_cb=None, before_ocr_cb=None):
    """
    PDF 파일 하나를 변환해 (텍스트, 페이지 수, OCR 사용 여부, 라벨 사용 여부)를 돌려준다.
    progress_cb(현재, 전체), cancel_cb() -> True면 중단, before_ocr_cb()는 OCR 모델 준비 직전 호출.
    """
    pdf_path = Path(pdf_path)
    doc = fitz.open(pdf_path)
    try:
        if ocr_mode == "force":
            will_ocr = True
        elif ocr_mode == "skip":
            will_ocr = False
        else:
            will_ocr = needs_ocr(doc)

        page_count = len(doc)
        title = pdf_path.stem
        has_labels = has_meaningful_labels(doc)

        parts = []
        if fmt == "md":
            parts.append(f"# {title}\n")
            parts.append(f"> 원본 파일: `{pdf_path.name}`")
            parts.append(f"> 총 {page_count}페이지")
            parts.append(f"> {'EasyOCR로 텍스트 인식' if will_ocr else '직접 텍스트 추출'}")
            if has_labels:
                parts.append("> 책 페이지 라벨 감지: `[p. 45] · PDF 55` = 책 페이지 / PDF 페이지")
            else:
                parts.append("> 책 페이지 라벨 없음: `[p. N]`은 PDF 페이지 번호입니다")
            parts.append("")
        else:
            parts.append(f"=== {title} ===")
            parts.append(f"원본: {pdf_path.name} | 총 {page_count}페이지")
            if has_labels:
                parts.append("페이지 라벨 감지됨")
            parts.append("")

        reader = None
        if will_ocr:
            if before_ocr_cb and not ocr_ready():
                before_ocr_cb()
            reader = get_ocr_reader()

        for page_num, page in enumerate(doc, 1):
            if cancel_cb and cancel_cb():
                raise ConversionCancelled()
            label = get_page_label(doc, page_num - 1) if has_labels else ""
            if will_ocr:
                pix = page.get_pixmap(dpi=dpi)
                text = ocr_page(reader, pix.tobytes("png"))
            else:
                text = page.get_text("text")
            parts.append(page_header(page_num, label, has_labels, fmt))
            parts.append(clean_text(text))
            if progress_cb:
                progress_cb(page_num, page_count)

        return "\n".join(parts), page_count, will_ocr, has_labels
    finally:
        doc.close()


def unique_output_path(folder: Path, stem: str, ext: str) -> Path:
    """같은 이름의 파일이 있으면 덮어쓰지 않고 '이름 (1).md'처럼 번호를 붙인다."""
    candidate = folder / f"{stem}.{ext}"
    n = 1
    while candidate.exists():
        candidate = folder / f"{stem} ({n}).{ext}"
        n += 1
    return candidate
