from __future__ import annotations

import os
import base64
import subprocess
from pathlib import Path

from .startup import GUI_ARGUMENTS, pythonw_executable


DEFAULT_SHORTCUT_NAME = "論文PDFファイル名整理.lnk"


def _desktop_directory() -> Path:
    if os.name != "nt":
        raise OSError("Windowsでのみショートカットを作成できます")
    command = "[Environment]::GetFolderPath('Desktop')"
    try:
        output = subprocess.check_output(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            text=True,
            encoding="utf-8",
            errors="replace",
        ).strip()
    except (OSError, subprocess.SubprocessError):
        output = ""
    return Path(output) if output else Path.home() / "Desktop"


def _start_menu_directory() -> Path:
    if os.name != "nt":
        raise OSError("Windowsでのみスタートメニューショートカットを作成できます")
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def _create_shortcut(
    executable: str | Path,
    shortcut_path: str | Path,
    *,
    arguments: str = "",
    working_directory: str | Path | None = None,
    description: str = "論文PDFファイル名整理",
) -> Path:
    """指定した実行ファイルを指す、ユーザー単位のWindowsショートカットを作る。"""

    if os.name != "nt":
        raise OSError("Windowsでのみショートカットを作成できます")
    target = Path(shortcut_path)
    if target.suffix.casefold() != ".lnk":
        target = target.with_suffix(".lnk")
    target.parent.mkdir(parents=True, exist_ok=True)
    executable_path = Path(executable).resolve()
    workdir = Path(working_directory or executable_path.parent).resolve()

    def ps_literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    script = f"""
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut({ps_literal(str(target))})
$shortcut.TargetPath = {ps_literal(str(executable_path))}
$shortcut.Arguments = {ps_literal(arguments)}
$shortcut.WorkingDirectory = {ps_literal(str(workdir))}
$shortcut.Description = {ps_literal(description)}
$shortcut.IconLocation = {ps_literal(str(executable_path) + ',0')}
$shortcut.WindowStyle = 1
$shortcut.Save()
"""
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    try:
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise RuntimeError(f"ショートカット作成に失敗しました: {detail}") from exc
    return target


def create_desktop_shortcut(
    project_dir: str | Path | None = None,
    shortcut_path: str | Path | None = None,
    name: str = DEFAULT_SHORTCUT_NAME,
) -> Path:
    """現在のPython環境でローカル画面を起動するWindowsショートカットを作る。"""

    project = Path(project_dir or Path(__file__).resolve().parent.parent).resolve()
    target = Path(shortcut_path) if shortcut_path else _desktop_directory() / name
    return _create_shortcut(
        pythonw_executable(), target, arguments=GUI_ARGUMENTS,
        working_directory=project, description="論文PDFファイル名整理 - ローカル画面",
    )


def create_app_shortcuts(
    executable: str | Path,
    *,
    desktop: bool = True,
    start_menu: bool = True,
    name: str = DEFAULT_SHORTCUT_NAME,
) -> list[Path]:
    """パッケージ済みWindowsアプリのデスクトップ／スタートメニュー登録を行う。"""

    executable_path = Path(executable).resolve()
    if not executable_path.is_file():
        raise FileNotFoundError(f"実行ファイルが見つかりません: {executable_path}")
    paths: list[Path] = []
    if desktop:
        paths.append(_create_shortcut(
            executable_path, _desktop_directory() / name,
            description="論文PDFファイル名整理",
        ))
    if start_menu:
        paths.append(_create_shortcut(
            executable_path, _start_menu_directory() / name,
            description="論文PDFファイル名整理",
        ))
    return paths
