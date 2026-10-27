"""hokenchat を起動する（Mac・Windows 共通）。デスクトップのアイコンから呼ばれる

  ローカルLLM（Ollama）→ hokenchat 本体 → （.env に LINE の設定があれば）LINE 受付サーバー → アプリ窓（Chrome／Edge）

すでに動いているものは起動し直さない。ログは data/logs/ に出る。
使い方: python launcher.py   （アイコンは install_shortcut.py で作る）
"""
import os, shutil, socket, subprocess, sys, time, urllib.request, webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
URL = "http://127.0.0.1:8765"
LOGS = ROOT / "data" / "logs"
WIN = sys.platform == "win32"


def alive(url):
    try:
        urllib.request.urlopen(url, timeout=2)
        return True
    except Exception:
        return False


def port_open(port):
    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


def venv_python(window=False):
    """.venv の Python（setup.py が作る）。Windows で裏で動かすときは黒い窓が出ない pythonw を使う"""
    p = ROOT / ".venv" / ("Scripts" if WIN else "bin") / (("pythonw.exe" if window else "python.exe") if WIN else "python")
    return str(p) if p.exists() else sys.executable


def spawn(args, log, env=None):
    LOGS.mkdir(parents=True, exist_ok=True)
    out = open(LOGS / log, "a", encoding="utf-8")
    kw = {"creationflags": 0x08000000} if WIN else {"start_new_session": True}  # 0x08000000 = CREATE_NO_WINDOW
    subprocess.Popen(args, cwd=ROOT, stdout=out, stderr=out, stdin=subprocess.DEVNULL,
                     env={**os.environ, "PYTHONUTF8": "1", **(env or {})}, **kw)


def notify(msg):
    """エラーを画面に出す（アイコンから起動したときはコンソールがないため）"""
    print(msg)
    if WIN:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, "hokenchat", 0x10)
    elif sys.platform == "darwin":
        subprocess.run(["osascript", "-e", f'display dialog "{msg}" with title "hokenchat" buttons {{"OK"}} with icon stop'])


def wait(check, seconds):
    for _ in range(seconds * 2):
        if check():
            return True
        time.sleep(0.5)
    return False


def find(cands):
    return next((str(c) for c in cands if c and Path(c).exists()), None)


def start_ollama():
    if alive("http://127.0.0.1:11434/api/tags"):
        return True
    la = os.environ.get("LOCALAPPDATA", "")
    exe = shutil.which("ollama") or find([Path(la) / "Programs" / "Ollama" / "ollama.exe",
                                          "/Applications/Ollama.app/Contents/Resources/ollama",
                                          "/opt/homebrew/bin/ollama", "/usr/local/bin/ollama"])
    if not exe:
        notify("Ollama（ローカルLLM）が見つかりません。SETUP.md の手順で入れてください。")
        return False
    env = {"OLLAMA_HOST": "127.0.0.1:11434", "OLLAMA_FLASH_ATTENTION": "1"}  # 社外から使えないよう localhost のみ
    if os.environ.get("HOKENCHAT_MODELS"):
        env["OLLAMA_MODELS"] = os.environ["HOKENCHAT_MODELS"]
    spawn([exe, "serve"], "ollama.log", env)
    return wait(lambda: alive("http://127.0.0.1:11434/api/tags"), 30)


def start_app():
    if alive(URL + "/api/health"):
        return True
    spawn([venv_python(window=True), "app.py"], "app.log")
    return wait(lambda: alive(URL + "/api/health"), 180)  # 初回は文書の取り込みがあるので長めに待つ


def start_line():
    env = ROOT / ".env"
    keys = env.read_text(encoding="utf-8") if env.exists() else ""
    configured = all(f"{k}=" in keys and keys.split(f"{k}=", 1)[1].split("\n", 1)[0].strip()
                      for k in ("LINE_CHANNEL_SECRET", "LINE_CHANNEL_ACCESS_TOKEN"))
    if configured and not port_open(8766):
        spawn([venv_python(window=True), "line_webhook.py"], "line_webhook.log")


def open_window():
    pf, pf86, la = (os.environ.get(k, "") for k in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"))
    exe = find([Path(pf) / "Google/Chrome/Application/chrome.exe", Path(pf86) / "Google/Chrome/Application/chrome.exe",
                Path(la) / "Google/Chrome/Application/chrome.exe",
                Path(pf86) / "Microsoft/Edge/Application/msedge.exe", Path(pf) / "Microsoft/Edge/Application/msedge.exe"]
               if WIN else ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"])
    if not exe:  # Chrome も Edge もなければ普通のブラウザで開く（アドレスバーは出る）
        webbrowser.open(URL)
        return
    kw = {"creationflags": 0x08000000} if WIN else {"start_new_session": True}
    subprocess.Popen([exe, f"--app={URL}", "--window-size=1280,820"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kw)
    if sys.platform == "darwin":
        name = "Google Chrome" if "Chrome" in exe else "Microsoft Edge"
        subprocess.run(["osascript", "-e", f'tell application "{name}" to activate'], capture_output=True)


if __name__ == "__main__":
    if not start_ollama():
        sys.exit(1)
    if not start_app():
        notify("hokenchat の起動に失敗しました。data/logs/app.log を確認してください。")
        sys.exit(1)
    start_line()
    open_window()
