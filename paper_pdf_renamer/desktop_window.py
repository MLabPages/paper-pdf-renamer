from __future__ import annotations

import os
import subprocess
import webbrowser
from pathlib import Path


def _edge_candidates() -> tuple[Path, ...]:
    roots = [
        os.environ.get("PROGRAMFILES(X86)"),
        os.environ.get("PROGRAMFILES"),
        os.environ.get("LOCALAPPDATA"),
    ]
    return tuple(
        Path(root) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        for root in roots
        if root
    )


def open_app_window(url: str) -> bool:
    """Open the local UI in a dedicated Edge app window, not a browser tab."""

    if os.name == "nt":
        edge = next((path for path in _edge_candidates() if path.is_file()), None)
        if edge is not None:
            subprocess.Popen(
                [str(edge), f"--app={url}"],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                close_fds=True,
            )
            return True
    return bool(webbrowser.open(url))
