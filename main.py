"""
PDF2MD - PDF를 Markdown/TXT로 변환하는 데스크톱 프로그램
=======================================================
창에 PDF 파일(또는 PDF가 든 폴더)을 끌어다 놓고 '변환 시작'을 누르면 된다.

개발 중 실행: python main.py
자가 점검:    python main.py --selftest 결과.txt [--with-ocr]
"""

import logging
import multiprocessing
import os
import subprocess
import sys
import time
from pathlib import Path

APP_NAME = "PDF2MD"
FROZEN = getattr(sys, "frozen", False)


# ---------------------------------------------------------------------------
# 공통 준비: 로그, 표준 출력, 인증서
# ---------------------------------------------------------------------------
def log_dir() -> Path:
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA", str(Path.home())))
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Logs"
    else:
        root = Path.home() / ".cache"
    d = root / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def prepare_environment() -> None:
    log_path = log_dir() / "pdf2md.log"
    logging.basicConfig(filename=str(log_path), level=logging.INFO, encoding="utf-8",
                        format="%(asctime)s %(levelname)s %(message)s")
    # 창 프로그램으로 묶이면 표준 출력이 없어서, 라이브러리가 print하면 멈출 수 있다
    if FROZEN or sys.stdout is None or sys.stderr is None:
        stream = open(log_path, "a", encoding="utf-8", buffering=1)
        sys.stdout = stream
        sys.stderr = stream
    # 독립 실행 앱(특히 macOS)에서 OCR 모델을 내려받을 때 인증서 오류를 막는다
    try:
        import certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
        os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
    except Exception:
        pass


def open_in_file_manager(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)


# ---------------------------------------------------------------------------
# 화면
# ---------------------------------------------------------------------------
def build_gui():
    """PySide6는 자가 점검에서도 쓰므로, 화면 관련 클래스는 함수 안에서 정의해 돌려준다."""
    from PySide6.QtCore import QObject, QSettings, Qt, QThread, Signal
    from PySide6.QtWidgets import (
        QAbstractItemView, QButtonGroup, QComboBox, QFileDialog, QFormLayout, QFrame,
        QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow, QMessageBox,
        QProgressBar, QPushButton, QRadioButton, QSpinBox, QTableWidget, QTableWidgetItem,
        QVBoxLayout, QWidget,
    )

    import converter

    class Worker(QObject):
        file_started = Signal(int)
        file_progress = Signal(int, int, int)
        file_done = Signal(int, str, str)
        file_failed = Signal(int, str)
        status = Signal(str)
        finished_all = Signal(int, int, bool)

        def __init__(self, paths, fmt, ocr_mode, dpi, out_dir):
            super().__init__()
            self.paths = list(paths)
            self.fmt, self.ocr_mode, self.dpi, self.out_dir = fmt, ocr_mode, dpi, out_dir
            self._cancel = False

        def cancel(self):
            self._cancel = True

        def run(self):
            ok = failed = 0
            for idx, pdf in enumerate(self.paths):
                if self._cancel:
                    break
                self.file_started.emit(idx)
                try:
                    text, pages, used_ocr, has_labels = converter.convert_pdf(
                        pdf, fmt=self.fmt, ocr_mode=self.ocr_mode, dpi=self.dpi,
                        progress_cb=lambda cur, tot, i=idx: self.file_progress.emit(i, cur, tot),
                        cancel_cb=lambda: self._cancel,
                        before_ocr_cb=lambda: self.status.emit(
                            "OCR 모델을 준비하는 중입니다. 처음 한 번은 내려받느라 몇 분 걸릴 수 있습니다."),
                    )
                    folder = Path(self.out_dir) if self.out_dir else Path(pdf).parent
                    folder.mkdir(parents=True, exist_ok=True)
                    out = converter.unique_output_path(folder, Path(pdf).stem, self.fmt)
                    out.write_text(text, encoding="utf-8")
                    info = f"완료 · {pages}쪽 · {'OCR' if used_ocr else '직접 추출'}"
                    if has_labels:
                        info += " · 책 페이지 라벨"
                    ok += 1
                    self.file_done.emit(idx, str(out), info)
                    self.status.emit(f"{Path(pdf).name} 변환 완료")
                except converter.ConversionCancelled:
                    break
                except Exception as exc:
                    logging.exception("변환 실패: %s", pdf)
                    failed += 1
                    self.file_failed.emit(idx, str(exc))
            self.finished_all.emit(ok, failed, self._cancel)

    class DropArea(QFrame):
        def __init__(self, on_click):
            super().__init__()
            self.setObjectName("dropArea")
            self.setMinimumHeight(110)
            self.setCursor(Qt.PointingHandCursor)
            self._on_click = on_click
            layout = QVBoxLayout(self)
            title = QLabel("PDF 파일이나 폴더를 여기에 끌어다 놓으세요")
            title.setAlignment(Qt.AlignCenter)
            title.setStyleSheet("font-size: 16px; font-weight: 600;")
            sub = QLabel("또는 이곳을 눌러 파일을 고르세요 · 여러 개를 한꺼번에 넣을 수 있습니다")
            sub.setAlignment(Qt.AlignCenter)
            sub.setStyleSheet("color: gray;")
            layout.addStretch()
            layout.addWidget(title)
            layout.addWidget(sub)
            layout.addStretch()
            self.set_highlight(False)

        def set_highlight(self, on: bool):
            color = "#3b82f6" if on else "#9ca3af"
            self.setStyleSheet(f"#dropArea {{ border: 2px dashed {color}; border-radius: 10px; }}")

        def mousePressEvent(self, event):
            self._on_click()

    class MainWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("PDF2MD - PDF를 Markdown으로")
            self.resize(860, 640)
            self.setAcceptDrops(True)
            self.settings = QSettings(APP_NAME, APP_NAME)
            self.paths = []
            self.thread = None
            self.worker = None
            self.last_output_folder = None

            central = QWidget()
            self.setCentralWidget(central)
            root = QVBoxLayout(central)

            self.drop_area = DropArea(self.choose_files)
            root.addWidget(self.drop_area)

            self.table = QTableWidget(0, 2)
            self.table.setHorizontalHeaderLabels(["파일", "상태"])
            self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
            self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
            self.table.verticalHeader().setVisible(False)
            self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            root.addWidget(self.table, 1)

            list_buttons = QHBoxLayout()
            self.btn_remove = QPushButton("선택 항목 빼기")
            self.btn_clear = QPushButton("목록 비우기")
            self.btn_remove.clicked.connect(self.remove_selected)
            self.btn_clear.clicked.connect(self.clear_list)
            list_buttons.addWidget(self.btn_remove)
            list_buttons.addWidget(self.btn_clear)
            list_buttons.addStretch()
            root.addLayout(list_buttons)

            # 설정
            self.settings_box = QGroupBox("설정")
            form = QFormLayout(self.settings_box)
            self.cmb_format = QComboBox()
            self.cmb_format.addItem("Markdown (.md) - 추천", "md")
            self.cmb_format.addItem("텍스트 (.txt)", "txt")
            form.addRow("출력 형식", self.cmb_format)

            self.cmb_ocr = QComboBox()
            self.cmb_ocr.addItem("자동 감지 (권장) - 스캔본만 OCR", "auto")
            self.cmb_ocr.addItem("항상 OCR", "force")
            self.cmb_ocr.addItem("OCR 안 함 (빠름)", "skip")
            form.addRow("OCR", self.cmb_ocr)

            self.spin_dpi = QSpinBox()
            self.spin_dpi.setRange(100, 400)
            self.spin_dpi.setSingleStep(50)
            self.spin_dpi.setSuffix(" DPI")
            self.spin_dpi.setToolTip("높을수록 정확하지만 느립니다. 200이면 충분합니다.")
            form.addRow("OCR 해상도", self.spin_dpi)

            save_row = QHBoxLayout()
            self.rb_same = QRadioButton("원본 PDF와 같은 폴더")
            self.rb_custom = QRadioButton("지정한 폴더:")
            self.save_group = QButtonGroup(self)
            self.save_group.addButton(self.rb_same)
            self.save_group.addButton(self.rb_custom)
            self.edit_folder = QLineEdit()
            self.edit_folder.setReadOnly(True)
            self.edit_folder.setPlaceholderText("폴더를 고르세요")
            self.btn_browse = QPushButton("찾아보기...")
            self.btn_browse.clicked.connect(self.choose_folder)
            save_row.addWidget(self.rb_same)
            save_row.addWidget(self.rb_custom)
            save_row.addWidget(self.edit_folder, 1)
            save_row.addWidget(self.btn_browse)
            form.addRow("저장 위치", save_row)
            root.addWidget(self.settings_box)

            # 진행 및 실행
            self.progress = QProgressBar()
            self.progress.setValue(0)
            root.addWidget(self.progress)
            self.lbl_status = QLabel("PDF를 넣고 '변환 시작'을 누르세요.")
            self.lbl_status.setWordWrap(True)
            root.addWidget(self.lbl_status)

            run_buttons = QHBoxLayout()
            self.btn_help = QPushButton("도움말")
            self.btn_help.clicked.connect(self.show_help)
            self.btn_open = QPushButton("저장 폴더 열기")
            self.btn_open.setEnabled(False)
            self.btn_open.clicked.connect(self.open_output_folder)
            self.btn_stop = QPushButton("중지")
            self.btn_stop.setEnabled(False)
            self.btn_stop.clicked.connect(self.stop_conversion)
            self.btn_start = QPushButton("변환 시작")
            self.btn_start.setDefault(True)
            self.btn_start.setMinimumWidth(140)
            self.btn_start.clicked.connect(self.start_conversion)
            run_buttons.addWidget(self.btn_help)
            run_buttons.addStretch()
            run_buttons.addWidget(self.btn_open)
            run_buttons.addWidget(self.btn_stop)
            run_buttons.addWidget(self.btn_start)
            root.addLayout(run_buttons)

            self.load_settings()
            self.rb_same.toggled.connect(self.update_folder_controls)
            self.update_folder_controls()
            self.update_buttons()

        # ----- 설정 저장/불러오기 -----
        def load_settings(self):
            def pick(combo, value):
                i = combo.findData(value)
                combo.setCurrentIndex(i if i >= 0 else 0)
            pick(self.cmb_format, self.settings.value("format", "md"))
            pick(self.cmb_ocr, self.settings.value("ocr_mode", "auto"))
            self.spin_dpi.setValue(int(self.settings.value("dpi", 200)))
            folder = self.settings.value("folder", "")
            self.edit_folder.setText(folder)
            if self.settings.value("save_mode", "same") == "custom" and folder:
                self.rb_custom.setChecked(True)
            else:
                self.rb_same.setChecked(True)

        def save_settings(self):
            self.settings.setValue("format", self.cmb_format.currentData())
            self.settings.setValue("ocr_mode", self.cmb_ocr.currentData())
            self.settings.setValue("dpi", self.spin_dpi.value())
            self.settings.setValue("folder", self.edit_folder.text())
            self.settings.setValue("save_mode", "custom" if self.rb_custom.isChecked() else "same")

        def update_folder_controls(self):
            custom = self.rb_custom.isChecked()
            self.edit_folder.setEnabled(custom)
            self.btn_browse.setEnabled(custom)

        # ----- 파일 목록 -----
        def add_paths(self, items):
            added = 0
            for item in items:
                p = Path(item)
                candidates = sorted(p.glob("*.pdf")) + sorted(p.glob("*.PDF")) if p.is_dir() else [p]
                for c in candidates:
                    if c.suffix.lower() == ".pdf" and c.is_file() and c not in self.paths:
                        self.paths.append(c)
                        row = self.table.rowCount()
                        self.table.insertRow(row)
                        name = QTableWidgetItem(c.name)
                        name.setToolTip(str(c))
                        self.table.setItem(row, 0, name)
                        self.table.setItem(row, 1, QTableWidgetItem("대기"))
                        added += 1
            if added:
                self.lbl_status.setText(f"{added}개 파일을 추가했습니다. 모두 {len(self.paths)}개.")
            elif items:
                self.lbl_status.setText("추가할 PDF가 없습니다 (PDF가 아니거나 이미 목록에 있음).")
            self.update_buttons()
            return added

        def choose_files(self):
            if self.is_running():
                return
            files, _ = QFileDialog.getOpenFileNames(self, "PDF 파일 선택", "", "PDF 파일 (*.pdf)")
            self.add_paths(files)

        def choose_folder(self):
            folder = QFileDialog.getExistingDirectory(self, "저장할 폴더 선택", self.edit_folder.text())
            if folder:
                self.edit_folder.setText(folder)
                self.rb_custom.setChecked(True)

        def remove_selected(self):
            rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
            for r in rows:
                self.table.removeRow(r)
                del self.paths[r]
            self.update_buttons()

        def clear_list(self):
            self.table.setRowCount(0)
            self.paths.clear()
            self.progress.setValue(0)
            self.update_buttons()

        def set_status(self, row, text):
            self.table.setItem(row, 1, QTableWidgetItem(text))

        # ----- 끌어다 놓기 -----
        def dragEnterEvent(self, event):
            if not self.is_running() and event.mimeData().hasUrls():
                event.acceptProposedAction()
                self.drop_area.set_highlight(True)

        def dragLeaveEvent(self, event):
            self.drop_area.set_highlight(False)

        def dropEvent(self, event):
            self.drop_area.set_highlight(False)
            paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
            self.add_paths(paths)
            event.acceptProposedAction()

        # ----- 변환 -----
        def is_running(self):
            return self.thread is not None

        def update_buttons(self):
            running = self.is_running()
            self.btn_start.setEnabled(bool(self.paths) and not running)
            self.btn_stop.setEnabled(running)
            for w in (self.btn_remove, self.btn_clear, self.settings_box):
                w.setEnabled(not running)
            self.drop_area.setEnabled(not running)

        def output_dir(self):
            return self.edit_folder.text() if self.rb_custom.isChecked() else None

        def start_conversion(self, run_in_thread=True):
            if not self.paths or self.is_running():
                return
            if self.rb_custom.isChecked() and not self.edit_folder.text():
                QMessageBox.information(self, APP_NAME, "저장할 폴더를 먼저 골라 주세요.")
                return
            self.save_settings()
            for row in range(self.table.rowCount()):
                self.set_status(row, "대기")
            self.progress.setRange(0, len(self.paths))
            self.progress.setValue(0)
            self.btn_open.setEnabled(False)
            self.last_output_folder = None
            self.started_at = time.time()

            self.worker = Worker(self.paths, self.cmb_format.currentData(), self.cmb_ocr.currentData(),
                                 self.spin_dpi.value(), self.output_dir())
            self.worker.file_started.connect(lambda i: self.set_status(i, "변환 중..."))
            self.worker.file_progress.connect(lambda i, c, t: self.set_status(i, f"변환 중 {c}/{t}쪽"))
            self.worker.file_done.connect(self.on_file_done)
            self.worker.file_failed.connect(self.on_file_failed)
            self.worker.status.connect(self.lbl_status.setText)
            self.worker.finished_all.connect(self.on_finished)

            self.lbl_status.setText("변환하는 중입니다...")
            if run_in_thread:
                self.thread = QThread()
                self.worker.moveToThread(self.thread)
                self.thread.started.connect(self.worker.run)
                self.update_buttons()
                self.thread.start()
            else:  # 자가 점검용: 같은 스레드에서 바로 실행
                self.thread = "inline"
                self.update_buttons()
                self.worker.run()

        def on_file_done(self, idx, out_path, info):
            self.set_status(idx, info)
            self.table.item(idx, 1).setToolTip(out_path)
            self.last_output_folder = Path(out_path).parent
            self.progress.setValue(self.progress.value() + 1)

        def on_file_failed(self, idx, message):
            self.set_status(idx, f"실패: {message}")
            self.progress.setValue(self.progress.value() + 1)

        def on_finished(self, ok, failed, cancelled):
            if isinstance(self.thread, QThread):
                self.thread.quit()
                self.thread.wait()
            self.thread = None
            self.worker = None
            elapsed = time.time() - self.started_at
            if cancelled:
                for row in range(self.table.rowCount()):
                    if self.table.item(row, 1).text().startswith(("대기", "변환 중")):
                        self.set_status(row, "중지됨")
                msg = f"중지했습니다. 완료 {ok}개"
            else:
                msg = f"끝났습니다. 완료 {ok}개"
            if failed:
                msg += f", 실패 {failed}개 (실패 사유는 상태 칸에 있습니다)"
            msg += f" · {elapsed:.0f}초"
            self.lbl_status.setText(msg)
            self.btn_open.setEnabled(self.last_output_folder is not None)
            self.update_buttons()

        def stop_conversion(self):
            if self.worker:
                self.worker.cancel()
                self.lbl_status.setText("지금 처리 중인 페이지까지 마치고 멈춥니다...")
                self.btn_stop.setEnabled(False)

        def open_output_folder(self):
            if self.last_output_folder:
                open_in_file_manager(self.last_output_folder)

        def show_help(self):
            QMessageBox.information(self, "도움말", (
                "페이지 표시 방식\n"
                "PDF에 책 페이지 번호가 따로 매겨져 있으면 둘 다 표시합니다.\n"
                "  [p. 45] · PDF 55  책 45쪽 (PDF 파일에서는 55번째 페이지)\n"
                "  [PDF p. 1]  책 번호가 없는 페이지 (표지 등)\n"
                "  [p. 1]  페이지 번호가 아예 없는 PDF\n\n"
                "OCR\n"
                "스캔본을 처음 변환할 때 OCR 모델(100MB 안팎)을 한 번 내려받습니다. "
                "한글 인식률은 깨끗한 스캔에서 90~95%, 흐릿한 스캔에서 75~85% 정도이니, "
                "인용할 때는 반드시 원본 PDF에서 해당 페이지를 확인하세요.\n\n"
                "예상 시간\n"
                "글자가 살아 있는 PDF는 한 권에 몇 초, 스캔본 OCR은 한 페이지에 2~5초입니다.\n\n"
                f"문제가 생기면 다음 폴더의 pdf2md.log를 확인하세요.\n{log_dir()}"
            ))

        def closeEvent(self, event):
            if self.is_running():
                answer = QMessageBox.question(self, APP_NAME, "변환 중입니다. 중지하고 닫을까요?")
                if answer != QMessageBox.Yes:
                    event.ignore()
                    return
                self.worker.cancel()
                if isinstance(self.thread, QThread):
                    self.thread.quit()
                    if not self.thread.wait(3000):
                        self.save_settings()
                        os._exit(0)
            self.save_settings()
            event.accept()

    return MainWindow


def run_gui() -> None:
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    MainWindow = build_gui()
    window = MainWindow()

    def excepthook(exc_type, exc, tb):
        logging.error("처리되지 않은 오류", exc_info=(exc_type, exc, tb))
        QMessageBox.critical(window, APP_NAME, f"예기치 않은 오류가 발생했습니다.\n{exc}\n\n"
                                               f"자세한 내용: {log_dir() / 'pdf2md.log'}")
    sys.excepthook = excepthook

    window.show()
    sys.exit(app.exec())


# ---------------------------------------------------------------------------
# 자가 점검 (빌드 직후 GitHub Actions에서 사용)
# ---------------------------------------------------------------------------
def run_selftest(out_path: str, with_ocr: bool) -> None:
    import importlib
    import tempfile
    import traceback

    lines, ok = [], True

    def record(success, text):
        nonlocal ok
        ok = ok and success
        lines.append(("OK   " if success else "FAIL ") + text)

    for mod in ["PySide6.QtWidgets", "fitz", "scipy.ndimage", "skimage", "shapely",
                "easyocr", "torch", "cv2", "numpy", "PIL", "certifi"]:
        try:
            m = importlib.import_module(mod)
            record(True, f"{mod} {getattr(m, '__version__', '')}")
        except Exception as exc:
            record(False, f"{mod}: {exc!r}")

    try:
        import fitz
        import converter
        tmp = Path(tempfile.mkdtemp())
        doc = fitz.open()
        for i in range(4):
            page = doc.new_page()
            page.insert_textbox(fitz.Rect(72, 72, 520, 760), f"Selftest page {i + 1}. " * 30, fontsize=11)
        doc.set_page_labels([{"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1},
                             {"startpage": 2, "prefix": "", "style": "D", "firstpagenum": 1}])
        sample = tmp / "sample.pdf"
        doc.save(sample)
        doc.close()

        text, pages, used_ocr, labels = converter.convert_pdf(sample, fmt="md", ocr_mode="auto")
        record(pages == 4 and not used_ocr and labels and "## [p. 1] · PDF 3" in text,
               f"text conversion (pages={pages}, ocr={used_ocr}, labels={labels})")

        # 화면 없이 프로그램 창을 만들어 실제 변환 흐름을 한 번 돌린다
        os.environ["QT_QPA_PLATFORM"] = "offscreen"
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        MainWindow = build_gui()
        win = MainWindow()
        win.rb_same.setChecked(True)
        added = win.add_paths([str(tmp)])
        win.start_conversion(run_in_thread=False)
        produced = (tmp / "sample.md").exists()
        status = win.table.item(0, 1).text()
        record(added == 1 and produced and status.startswith("완료"),
               f"window flow (added={added}, output={produced}, status={status})")
        win.close()
        app.processEvents()

        if with_ocr:
            from PIL import Image, ImageDraw, ImageFont
            img = Image.new("RGB", (1200, 300), "white")
            draw = ImageDraw.Draw(img)
            try:
                font = ImageFont.load_default(size=72)
            except TypeError:
                font = ImageFont.load_default()
            draw.text((40, 100), "HELLO PDF2MD 2026", fill="black", font=font)
            img_path = tmp / "scan.png"
            img.save(img_path)
            scan = fitz.open()
            page = scan.new_page(width=600, height=150)
            page.insert_image(page.rect, filename=str(img_path))
            scan_pdf = tmp / "scan.pdf"
            scan.save(scan_pdf)
            scan.close()
            text, pages, used_ocr, _ = converter.convert_pdf(scan_pdf, fmt="md", ocr_mode="auto")
            body = text.split("## [p. 1]")[-1].strip().replace("\n", " ")
            record(used_ocr, f"OCR conversion (ocr={used_ocr}, recognized='{body[:60]}')")
    except Exception:
        record(False, "conversion/window test:\n" + traceback.format_exc())

    Path(out_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    sys.exit(0 if ok else 1)


def main() -> None:
    multiprocessing.freeze_support()
    prepare_environment()
    if "--selftest" in sys.argv:
        run_selftest(sys.argv[sys.argv.index("--selftest") + 1], "--with-ocr" in sys.argv)
    else:
        run_gui()


def _bundle_hints():  # 호출되지 않음. PyInstaller가 의존성을 빠짐없이 묶도록 알려주는 용도
    import certifi  # noqa: F401
    import cv2  # noqa: F401
    import easyocr  # noqa: F401
    import fitz  # noqa: F401
    import numpy  # noqa: F401
    import PIL  # noqa: F401
    import scipy.ndimage  # noqa: F401
    import shapely  # noqa: F401
    import skimage  # noqa: F401
    import torch  # noqa: F401


if __name__ == "__main__":
    main()
