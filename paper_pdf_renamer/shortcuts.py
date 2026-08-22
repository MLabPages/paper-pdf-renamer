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


def create_desktop_shortcut(
    project_dir: str | Path | None = None,
    shortcut_path: str | Path | None = None,
    name: str = DEFAULT_SHORTCUT_NAME,
) -> Path:
    """現在のPython環境でローカル画面を起動するWindowsショートカットを作る。"""

    if os.name != "nt":
        raise OSError("Windowsでのみショートカットを作成できます")
    project = Path(project_dir or Path(__file__).resolve().parent.parent).resolve()
    target = Path(shortcut_path) if shortcut_path else _desktop_directory() / name
    if target.suffix.casefold() != ".lnk":
        target = target.with_suffix(".lnk")
    target.parent.mkdir(parents=True, exist_ok=True)
    executable = pythonw_executable().resolve()
    def ps_literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    script = f"""
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut({ps_literal(str(target))})
$shortcut.TargetPath = {ps_literal(str(executable))}
$shortcut.Arguments = {ps_literal(GUI_ARGUMENTS)}
$shortcut.WorkingDirectory = {ps_literal(str(project))}
$shortcut.Description = '論文PDFファイル名整理 - ローカル画面'
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
