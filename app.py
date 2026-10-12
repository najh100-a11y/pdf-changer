"""
PDF2MD - PDF를 Markdown/TXT로 변환하는 드래그앤드롭 도구
=========================================================
- 텍스트 PDF: 직접 추출
- 스캔본 PDF: EasyOCR로 한글+영문 인식
- 책 페이지 라벨(예: p.45)이 있으면 자동 감지해 함께 표시
- 출력 폴더를 지정하면 자동 저장, 비워두면 다운로드 버튼 제공

이 파일은 launcher.py가 Streamlit 서버로 실행한다.
개발 중 단독 실행: streamlit run app.py
"""

import io
import os
import subprocess
import sys
import tempfile
import warnings
import zipfile
from pathlib import Path

import streamlit as st

warnings.filterwarnings("ignore")

st.set_page_config(page_title="PDF2MD", page_icon="📄", layout="wide")

try:
    import fitz  # PyMuPDF
except ImportError:
    st.error("PDF 처리 모듈(PyMuPDF)을 불러오지 못했습니다. 프로그램을 다시 설치해 주세요.")
    st.stop()


# ---------------------------------------------------------------------------
# OCR 모델 (한 번만 로딩)
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner="OCR 모델을 준비하는 중입니다 (처음 한 번은 약 100MB를 내려받습니다)...")
def get_ocr_reader():
    try:
        import easyocr
    except ImportError:
        st.error("OCR 모듈(EasyOCR)을 불러오지 못했습니다. 프로그램을 다시 설치해 주세요.")
        st.stop()
    return easyocr.Reader(["ko", "en"], gpu=False, verbose=False)


# ---------------------------------------------------------------------------
# 페이지 라벨
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# 변환 로직
# ---------------------------------------------------------------------------
def needs_ocr(pdf_path: Path, sample: int = 10, threshold: int = 200) -> bool:
    doc = fitz.open(pdf_path)
    n = min(sample, len(doc))
    if n == 0:
        doc.close()
        return False
    indices = [int(i * len(doc) / n) for i in range(n)]
    text_pages = sum(1 for i in indices if len(doc[i].get_text("text").strip()) >= threshold)
    doc.close()
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


def convert_pdf_bytes(pdf_bytes: bytes, filename: str, fmt: str = "md",
                      ocr_mode: str = "auto", dpi: int = 200, progress_cb=None):
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        tmp_path = Path(f.name)

    try:
        if ocr_mode == "force":
            will_ocr = True
        elif ocr_mode == "skip":
            will_ocr = False
        else:
            will_ocr = needs_ocr(tmp_path)

        doc = fitz.open(tmp_path)
        page_count = len(doc)
        title = Path(filename).stem
        has_labels = has_meaningful_labels(doc)

        parts = []
        if fmt == "md":
            parts.append(f"# {title}\n")
            parts.append(f"> 원본 파일: `{filename}`")
            parts.append(f"> 총 {page_count}페이지")
            parts.append(f"> {'EasyOCR로 텍스트 인식' if will_ocr else '직접 텍스트 추출'}")
            if has_labels:
                parts.append("> 책 페이지 라벨 감지: `[p. 45] · PDF 55` = 책 페이지 / PDF 페이지")
            else:
                parts.append("> 책 페이지 라벨 없음: `[p. N]`은 PDF 페이지 번호입니다")
            parts.append("")
        else:
            parts.append(f"=== {title} ===")
            parts.append(f"원본: {filename} | 총 {page_count}페이지")
            if has_labels:
                parts.append("페이지 라벨 감지됨")
            parts.append("")

        reader = get_ocr_reader() if will_ocr else None

        for page_num, page in enumerate(doc, 1):
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

        doc.close()
        return "\n".join(parts), page_count, will_ocr, has_labels
    finally:
        tmp_path.unlink(missing_ok=True)


def open_folder(path: Path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
    except Exception as e:
        st.warning(f"폴더 열기 실패: {e}")


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
st.title("📄 PDF → Markdown 변환기")
st.caption("PDF를 가벼운 텍스트 파일로 변환합니다 · 스캔본은 한글+영문 OCR 자동 인식 · "
           "책 페이지 라벨 자동 감지")

with st.sidebar:
    st.header("⚙️ 설정")

    output_format = st.radio(
        "출력 형식",
        options=["md", "txt"],
        format_func=lambda x: "Markdown (.md) - 추천" if x == "md" else "Plain Text (.txt)",
    )

    ocr_mode = st.selectbox(
        "OCR 모드",
        options=["auto", "force", "skip"],
        format_func=lambda x: {"auto": "자동 감지 (권장)", "force": "강제 OCR",
                               "skip": "OCR 안함 (빠름)"}[x],
        help="자동 감지: 스캔본만 OCR로 처리합니다",
    )

    dpi = st.slider("OCR 해상도 (DPI)", min_value=100, max_value=400, value=200, step=50,
                    help="높을수록 정확하지만 느립니다. 기본 200이면 충분합니다")

    st.divider()
    st.markdown("**📁 저장 옵션**")
    placeholder = (r"예: C:\Users\이름\Documents\converted" if sys.platform == "win32"
                   else "예: ~/Documents/converted")
    output_dir_str = st.text_input(
        "출력 폴더", value="", placeholder=placeholder,
        help="경로를 입력하면 변환 파일이 그 폴더에 자동 저장됩니다. "
             "비워두면 다운로드 버튼으로 받습니다.",
    )

    output_dir = None
    if output_dir_str.strip():
        try:
            output_dir = Path(output_dir_str.strip()).expanduser()
            output_dir.mkdir(parents=True, exist_ok=True)
            st.success(f"자동 저장: `{output_dir}`")
        except Exception as e:
            output_dir = None
            st.error(f"폴더를 사용할 수 없습니다: {e}")
    else:
        st.caption("폴더 미지정: 다운로드 버튼으로 받기")

    st.divider()
    st.markdown("**예상 처리 시간**")
    st.markdown("- 텍스트 PDF: 1~3초/권\n- 스캔본 OCR: 2~5초/페이지\n- 300쪽 책 OCR: 약 15~25분")


uploaded_files = st.file_uploader(
    "**PDF 파일을 끌어다 놓으세요** (여러 개 가능)",
    type=["pdf"], accept_multiple_files=True,
)

if not uploaded_files:
    st.info("👆 위 영역에 PDF를 끌어다 놓으면 변환을 시작할 수 있습니다.")
    with st.expander("📖 사용 안내"):
        st.markdown("""
**페이지 표시 방식**

PDF에 인쇄된 책 페이지 라벨이 있으면 자동으로 감지해 둘 다 표시합니다.

- `## [p. 45] · PDF 55` - 책 페이지 45쪽 (PDF 파일에서는 55번째 페이지)
- `## [PDF p. 1]` - 책 라벨이 없는 페이지 (표지 등)
- `## [p. 1]` - 라벨이 아예 없는 PDF

**한글 OCR 품질**

깨끗한 출판 스캔은 90~95%, 흐릿한 스캔은 75~85% 정도입니다.
인용할 때는 반드시 원본 PDF에서 해당 페이지를 직접 확인하세요.
        """)
else:
    total_size = sum(len(f.getvalue()) for f in uploaded_files)
    st.success(f"📚 {len(uploaded_files)}개 파일 준비됨 (총 {total_size/1024/1024:.1f} MB)")
    for f in uploaded_files:
        st.markdown(f"- **{f.name}** ({len(f.getvalue())/1024/1024:.1f} MB)")

    if st.button("🚀 변환 시작", type="primary", use_container_width=True):
        results = []
        overall = st.progress(0, text="시작...")

        for idx, uploaded in enumerate(uploaded_files):
            file_status = st.empty()
            file_progress = st.progress(0)
            file_status.text(f"{uploaded.name}: 분석 중...")
            pdf_bytes = uploaded.getvalue()

            def update(current, total, _status=file_status, _bar=file_progress, _name=uploaded.name):
                _bar.progress(current / total)
                _status.text(f"{_name}: 페이지 {current}/{total}")

            try:
                text, pages, used_ocr, has_labels = convert_pdf_bytes(
                    pdf_bytes, uploaded.name, fmt=output_format,
                    ocr_mode=ocr_mode, dpi=dpi, progress_cb=update,
                )
                out_name = Path(uploaded.name).stem + f".{output_format}"
                method = "OCR" if used_ocr else "직접 추출"
                label_tag = " · 책 페이지 라벨 사용" if has_labels else ""
                saved_path = None
                if output_dir is not None:
                    saved_path = output_dir / out_name
                    saved_path.write_text(text, encoding="utf-8")
                results.append({
                    "name": out_name,
                    "text": text,
                    "saved_path": str(saved_path) if saved_path else None,
                    "message": f"{uploaded.name}: {pages}페이지 변환 완료 ({method}) · "
                               f"{len(pdf_bytes)/1024:.0f} KB → "
                               f"{len(text.encode('utf-8'))/1024:.1f} KB{label_tag}",
                })
            except Exception as e:
                results.append({"name": uploaded.name, "error": str(e)})

            file_status.empty()
            file_progress.empty()
            overall.progress((idx + 1) / len(uploaded_files),
                             text=f"{idx + 1}/{len(uploaded_files)} 완료")

        overall.empty()
        # 결과를 세션에 보관해, 다운로드나 폴더 열기 버튼을 눌러 화면이 새로 그려져도 유지되게 한다
        st.session_state["conversion"] = {
            "results": results,
            "output_dir": str(output_dir) if output_dir is not None else None,
            "fmt": output_format,
        }


# ---------------------------------------------------------------------------
# 변환 결과 (세션에서 다시 그림)
# ---------------------------------------------------------------------------
conv = st.session_state.get("conversion")
if conv:
    st.divider()
    ok = [r for r in conv["results"] if "error" not in r]
    failed = [r for r in conv["results"] if "error" in r]

    for r in ok:
        st.success(r["message"])
    for r in failed:
        st.error(f"{r['name']}: 변환 실패 ({r['error']})")

    if ok and conv["output_dir"]:
        st.info(f"✨ {len(ok)}개 파일이 저장되었습니다: `{conv['output_dir']}`")
        if st.button("📂 출력 폴더 열기"):
            open_folder(Path(conv["output_dir"]))
    elif ok:
        mime = "text/markdown" if conv["fmt"] == "md" else "text/plain"
        if len(ok) > 1:
            zip_buf = io.BytesIO()
            with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for r in ok:
                    zf.writestr(r["name"], r["text"])
            st.download_button(
                f"📦 전체 ZIP 다운로드 ({len(ok)}개 파일)",
                data=zip_buf.getvalue(), file_name=f"converted_{conv['fmt']}.zip",
                mime="application/zip", type="primary", use_container_width=True,
            )
        for i, r in enumerate(ok):
            st.download_button(f"💾 {r['name']}", data=r["text"].encode("utf-8"),
                               file_name=r["name"], mime=mime, key=f"dl_{i}")

    if st.button("결과 지우기"):
        st.session_state.pop("conversion", None)
        st.rerun()
