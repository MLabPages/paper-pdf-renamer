from __future__ import annotations

import os
import sys
from pathlib import Path


VALUE_NAME = "PaperPdfRenamer"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
GUI_ARGUMENTS = "-m paper_pdf_renamer.gui"


def pythonw_executable() -> Path:
    executable = Path(sys.executable)
    if executable.name.casefold() == "python.exe":
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.exists():
            executable = pythonw
    return executable


def _command() -> str:
    return f'"{pythonw_executable()}" {GUI_ARGUMENTS}'


def is_supported() -> bool:
    return os.name == "nt"


def is_enabled() -> bool:
    if not is_supported():
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(value)
    except FileNotFoundError:
        return False


def set_enabled(enabled: bool) -> bool:
    """HKCUだけを変更し、管理者権限を要求せず起動時設定を切り替える。"""

    if not is_supported():
        return False
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _command())
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
    return True
