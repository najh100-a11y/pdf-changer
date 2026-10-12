"""
PDF2MD 실행기
-------------
더블클릭하면 작은 제어 창을 띄우고, 같은 실행 파일을 '--server' 모드로 한 번 더 실행해
Streamlit 서버(app.py)를 켠 다음 브라우저를 자동으로 연다.
제어 창을 닫으면 서버도 함께 종료된다.

개발 중 실행: python launcher.py
"""

import importlib
import multiprocessing
import os
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

APP_NAME = "PDF2MD"
FROZEN = getattr(sys, "frozen", False)


def base_dir() -> Path:
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


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


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0


def parent_alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ---------------------------------------------------------------------------
# 서버 모드
# ---------------------------------------------------------------------------
def run_server(port: int, parent_pid: int) -> None:
    # 창 없는 실행 파일에서는 표준 출력이 없으므로 로그 파일로 돌린다
    log_file = open(log_dir() / "server.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = log_file
    sys.stderr = log_file
    print(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} server start (port {port}) ===")

    # 독립 실행 앱(특히 macOS)에서 OCR 모델 다운로드 시 인증서 오류를 막는다
    try:
        import certifi
        os.environ.setdefault("SSL_CERT_FILE", certifi.where())
        os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
    except Exception:
        pass

    # 제어 창이 비정상 종료되더라도 서버가 남지 않게 감시한다
    if parent_pid:
        def watch_parent():
            while True:
                time.sleep(2)
                if not parent_alive(parent_pid):
                    os._exit(0)
        threading.Thread(target=watch_parent, daemon=True).start()

    from streamlit.web import cli as stcli

    sys.argv = [
        "streamlit", "run", str(base_dir() / "app.py"),
        "--global.developmentMode=false",
        "--server.headless=true",
        f"--server.port={port}",
        "--server.address=127.0.0.1",
        "--server.fileWatcherType=none",
        "--server.maxUploadSize=2048",
        "--browser.gatherUsageStats=false",
    ]
    sys.exit(stcli.main())


# ---------------------------------------------------------------------------
# 제어 창 모드
# ---------------------------------------------------------------------------
def run_gui() -> None:
    import tkinter as tk

    port = find_free_port()
    url = f"http://127.0.0.1:{port}"

    if FROZEN:
        cmd = [sys.executable, "--server", str(port), str(os.getpid())]
    else:
        cmd = [sys.executable, str(Path(__file__).resolve()), "--server", str(port), str(os.getpid())]

    popen_kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    proc = subprocess.Popen(cmd, **popen_kwargs)

    root = tk.Tk()
    root.title(APP_NAME)
    root.geometry("420x190")
    root.resizable(False, False)

    status = tk.StringVar(value="변환기를 준비하는 중입니다.\n처음 실행할 때는 1~2분 걸릴 수 있습니다.")
    tk.Label(root, textvariable=status, wraplength=380, justify="center",
             font=("TkDefaultFont", 12)).pack(pady=(22, 12))

    buttons = tk.Frame(root)
    buttons.pack()
    open_btn = tk.Button(buttons, text="브라우저에서 열기", width=16, state="disabled",
                         command=lambda: webbrowser.open(url))
    open_btn.pack(side="left", padx=6)

    def open_logs():
        path = str(log_dir())
        if sys.platform == "win32":
            os.startfile(path)
        elif sys.platform == "darwin":
            subprocess.run(["open", path], check=False)
        else:
            subprocess.run(["xdg-open", path], check=False)

    def quit_app():
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception:
            pass
        root.destroy()

    tk.Button(buttons, text="종료", width=8, command=quit_app).pack(side="left", padx=6)
    tk.Label(root, text="이 창을 닫으면 변환기도 함께 종료됩니다.", fg="gray").pack(pady=(14, 0))
    log_link = tk.Label(root, text="문제가 있을 때: 로그 폴더 열기", fg="#3366cc", cursor="hand2")
    log_link.pack(pady=(4, 0))
    log_link.bind("<Button-1>", lambda _e: open_logs())

    root.protocol("WM_DELETE_WINDOW", quit_app)
    if sys.platform == "darwin":
        root.createcommand("tk::mac::Quit", quit_app)

    started = time.time()
    state = {"ready": False}

    def poll():
        if proc.poll() is not None:
            status.set("변환기가 예기치 않게 종료되었습니다.\n아래 '로그 폴더 열기'에서 server.log를 확인해 주세요.")
            open_btn.config(state="disabled")
            return
        if not state["ready"]:
            if port_open(port):
                state["ready"] = True
                status.set("실행 중입니다.\n브라우저 창에 PDF를 끌어다 놓으세요.")
                open_btn.config(state="normal")
                webbrowser.open(url)
            elif time.time() - started > 300:
                status.set("시작이 오래 걸리고 있습니다. 조금 더 기다리거나\n로그 폴더를 확인해 주세요.")
        root.after(500, poll)

    root.after(500, poll)
    root.mainloop()



# ---------------------------------------------------------------------------
# 자가 점검 모드 (빌드 직후 GitHub Actions에서 사용)
# ---------------------------------------------------------------------------
def run_selftest(out_path: str) -> None:
    lines, ok = [], True
    for mod in ["streamlit", "fitz", "scipy.ndimage", "skimage", "shapely", "easyocr", "torch", "cv2", "numpy", "PIL", "certifi"]:
        try:
            m = importlib.import_module(mod)
            lines.append(f"OK   {mod} {getattr(m, '__version__', '')}")
        except Exception as exc:
            ok = False
            lines.append(f"FAIL {mod}: {exc!r}")
    app_file = base_dir() / "app.py"
    lines.append(f"{'OK  ' if app_file.exists() else 'FAIL'} app.py at {app_file}")
    ok = ok and app_file.exists()
    Path(out_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    sys.exit(0 if ok else 1)

def main() -> None:
    multiprocessing.freeze_support()
    if "--selftest" in sys.argv:
        run_selftest(sys.argv[sys.argv.index("--selftest") + 1])
    elif "--server" in sys.argv:
        i = sys.argv.index("--server")
        port = int(sys.argv[i + 1])
        parent_pid = int(sys.argv[i + 2]) if len(sys.argv) > i + 2 else 0
        run_server(port, parent_pid)
    else:
        run_gui()


def _bundle_hints():  # 호출되지 않음. PyInstaller가 app.py의 의존성을 함께 묶도록 알려주는 용도
    import certifi  # noqa: F401
    import cv2  # noqa: F401
    import easyocr  # noqa: F401
    import fitz  # noqa: F401
    import numpy  # noqa: F401
    import PIL  # noqa: F401
    import scipy.ndimage  # noqa: F401
    import shapely  # noqa: F401
    import skimage  # noqa: F401
    import streamlit  # noqa: F401
    import torch  # noqa: F401


if __name__ == "__main__":
    main()
