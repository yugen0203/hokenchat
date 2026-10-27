"""hokenchat をアイコンから起動できるようにする（Mac・Windows）

  Mac    : ~/Applications/hokenchat.app を作る（Launchpad・Spotlight から起動でき、Dock にも置ける）＋デスクトップに置く
  Windows: デスクトップとスタートメニューに「hokenchat」のショートカットを作る

使い方: .venv の Python で  python install_shortcut.py      （setup.py の最後でも自動で実行される）
"""
import os, plistlib, shutil, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets"


def mac():
    py = ROOT / ".venv" / "bin" / "python"
    app = Path.home() / "Applications" / "hokenchat.app"
    shutil.rmtree(app, ignore_errors=True)
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Resources").mkdir()
    shutil.copy(ASSETS / "icon.icns", app / "Contents" / "Resources" / "icon.icns")
    with open(app / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump({"CFBundleName": "hokenchat", "CFBundleDisplayName": "hokenchat", "CFBundleIdentifier": "local.hokenchat",
                       "CFBundleExecutable": "hokenchat", "CFBundleIconFile": "icon", "CFBundlePackageType": "APPL",
                       "CFBundleShortVersionString": "0.1", "LSMinimumSystemVersion": "12.0"}, f)
    exe = app / "Contents" / "MacOS" / "hokenchat"
    exe.write_text(f'#!/bin/sh\nexec "{py if py.exists() else sys.executable}" "{ROOT / "launcher.py"}"\n', encoding="utf-8")
    exe.chmod(0o755)
    subprocess.run(["touch", str(app)])  # アイコンの表示を更新させる
    link = Path.home() / "Desktop" / "hokenchat.app"
    if link.is_symlink() or link.exists():
        link.unlink() if link.is_symlink() else shutil.rmtree(link)
    link.symlink_to(app)
    print("作成しました:", app, "（デスクトップにも置きました）")


def windows():
    pyw = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if not pyw.exists():
        pyw = Path(sys.executable).with_name("pythonw.exe")
    # パスに日本語や空白があっても崩れないよう、値は環境変数で PowerShell に渡す
    env = {**os.environ, "HC_TARGET": str(pyw), "HC_ARGS": f'"{ROOT / "launcher.py"}"', "HC_DIR": str(ROOT),
           "HC_ICON": str(ASSETS / "icon.ico")}
    ps = r"""
$sh = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
  $s = $sh.CreateShortcut((Join-Path $dir 'hokenchat.lnk'))
  $s.TargetPath = $env:HC_TARGET; $s.Arguments = $env:HC_ARGS; $s.WorkingDirectory = $env:HC_DIR
  $s.IconLocation = $env:HC_ICON; $s.Description = 'hokenchat'; $s.Save()
  Write-Output (Join-Path $dir 'hokenchat.lnk')
}"""
    r = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                       env=env, capture_output=True, text=True)
    if r.returncode:
        sys.exit("ショートカットを作れませんでした: " + r.stderr)
    print("作成しました:\n" + r.stdout.strip())


if __name__ == "__main__":
    {"darwin": mac, "win32": windows}.get(sys.platform, lambda: sys.exit("Mac と Windows のみ対応です"))()
