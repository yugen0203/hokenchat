"""hokenchat のセットアップ（Mac・Windows 共通）。何度実行してもよい（済んでいる手順は飛ばす）

  Mac:     python3 setup.py
  Windows: py -3 setup.py      （または python setup.py）

手順: 1) Python の確認  2) .venv の作成と依存の導入  3) Ollama の確認・起動  4) AI モデルの準備
      5) 文字認識の確認  6) 文書の取り込み  7) 自己テスト  8) アイコンの作成
オプション: --no-shortcut（アイコンを作らない）  --skip-models（モデルのダウンロードを飛ばす）
失敗したら [NG] の行に直し方を出して止まる。直してからもう一度実行すればよい。
"""
import os, subprocess, sys, time, urllib.request, venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WIN, MAC = sys.platform == "win32", sys.platform == "darwin"
VPY = ROOT / ".venv" / ("Scripts/python.exe" if WIN else "bin/python")
MODELS = ["qwen3:8b", "bge-m3"]  # app.py の CHAT_MODEL / EMBED_MODEL と同じ
sys.path.insert(0, str(ROOT))
import launcher  # Ollama の場所探し・起動を共通化


def ok(msg): print(f"[OK] {msg}", flush=True)
def ng(msg, fix): sys.exit(f"[NG] {msg}\n     直し方: {fix}")
def run(args, **kw): return subprocess.run(args, cwd=ROOT, env={**os.environ, "PYTHONUTF8": "1"}, **kw)


def step_python():
    if sys.version_info < (3, 10):
        ng(f"Python {sys.version.split()[0]} は古すぎます（3.10 以上が必要）",
           "Windows: winget install -e --id Python.Python.3.12 ／ Mac: brew install python@3.12")
    ok(f"Python {sys.version.split()[0]}")


def step_venv():
    if not VPY.exists():
        venv.create(ROOT / ".venv", with_pip=True)
    r = run([str(VPY), "-m", "pip", "install", "-q", "-r", "requirements.txt"])
    if r.returncode:
        ng("依存ライブラリ（requirements.txt）を入れられませんでした", "ネット接続を確認して、もう一度実行してください")
    ok(".venv と依存ライブラリ（PyMuPDF）")


def step_ollama():
    if not launcher.start_ollama():
        ng("Ollama（ローカルLLM）が見つからない、または起動しません",
           "Windows: winget install -e --id Ollama.Ollama ／ Mac: brew install ollama（または https://ollama.com からインストール）。入れたらもう一度実行")
    ok("Ollama が 127.0.0.1:11434 で動いています")


def step_models(skip):
    have = urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=5).read().decode()
    for m in MODELS:
        if f'"{m}' in have or f'"{m}:latest' in have:
            ok(f"モデル {m}（ダウンロード済み）")
        elif skip:
            print(f"[--] モデル {m} は未ダウンロード（--skip-models）")
        else:
            print(f"モデル {m} をダウンロードします（qwen3:8b は約5GB、bge-m3 は約1.2GB）…", flush=True)
            exe = launcher.shutil.which("ollama") or launcher.find([Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Ollama/ollama.exe",
                                                                     "/Applications/Ollama.app/Contents/Resources/ollama"])
            if run([exe, "pull", m]).returncode:
                ng(f"モデル {m} をダウンロードできませんでした", "ネット接続と空き容量（10GB 以上）を確認して、もう一度実行")
            ok(f"モデル {m}")


def step_ocr():
    sample = ROOT / "tests" / "samples" / "社内マニュアル_来客対応_写真.png"
    r = run([str(VPY), "-c", f"import app; print(app.ocr(r'{sample}'))"], capture_output=True, text=True, encoding="utf-8")
    if r.returncode == 0 and "駐車場" in r.stdout:
        ok("文字認識（写真・スキャンしたPDFの取り込み）")
    elif WIN:
        print("[注意] 文字認識が使えません。写真・スキャンPDFの取り込みだけが使えず、他は動きます。\n"
              "       直し方: 設定 → 時刻と言語 → 言語と地域 で「日本語」を追加（「光学式文字認識」を含める）\n"
              f"       詳細: {(r.stderr or r.stdout)[-300:]}")
    else:
        print("[注意] 文字認識が使えません。写真・スキャンPDFの取り込みだけが使えず、他は動きます。\n"
              "       直し方: ターミナルで  xcode-select --install  を実行（Swift のコンパイラが入ります）\n"
              f"       詳細: {(r.stderr or r.stdout)[-300:]}")


def step_ingest():
    if run([str(VPY), "app.py", "--ingest-only"]).returncode:
        ng("文書の取り込みに失敗しました", "Ollama とモデルの状態を確認してから、もう一度実行")
    ok("文書の取り込み（data/docs）")


def step_test():
    if run([str(VPY), "eval.py", "--quick"]).returncode:
        ng("自己テストに失敗しました", "表示されたエラーを確認してください")
    ok("自己テスト（入力ガード・文書の分割）")


if __name__ == "__main__":
    print(f"hokenchat セットアップ（{'Windows' if WIN else 'Mac' if MAC else sys.platform}）\n", flush=True)
    if not (WIN or MAC):
        ng("この OS には対応していません", "Mac または Windows で実行してください")
    step_python(); step_venv(); step_ollama(); step_models("--skip-models" in sys.argv)
    step_ocr(); step_ingest(); step_test()
    if "--no-shortcut" not in sys.argv:
        if run([str(VPY), "install_shortcut.py"]).returncode:
            ng("アイコンを作れませんでした", "表示されたエラーを確認してください")
        ok("アイコン（" + ("デスクトップとスタートメニュー" if WIN else "アプリケーションフォルダとデスクトップ") + "）")
    print("\nセットアップ完了。デスクトップの「hokenchat」をダブルクリックすると起動します。")
