from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TextIO

from .config import app_data_dir


class SingleInstanceLock:
    """GUIを同時に複数起動しないための、プロセス終了で解放されるロック。"""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else app_data_dir() / "gui.lock"
        self._stream: TextIO | None = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.path.open("a+", encoding="utf-8")
        if self.path.stat().st_size == 0:
            stream.write("1")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, ImportError):
            stream.close()
            return False
        self._stream = stream
        return True

    def release(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        except (OSError, ImportError):
            pass
        finally:
            stream.close()

    def __enter__(self) -> "SingleInstanceLock":
        if not self.acquire():
            raise RuntimeError("同じアプリがすでに起動しています")
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


def server_state_path() -> Path:
    return app_data_dir() / "server.json"


def write_server_state(port: int) -> Path:
    target = server_state_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f"{target.name}.tmp")
    temporary.write_text(json.dumps({"port": port}), encoding="utf-8")
    temporary.replace(target)
    return target


def read_server_port() -> int | None:
    try:
        payload = json.loads(server_state_path().read_text(encoding="utf-8"))
        port = int(payload.get("port"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return port if 1 <= port <= 65535 else None


def clear_server_state(port: int | None = None) -> None:
    target = server_state_path()
    if port is not None and read_server_port() != port:
        return
    try:
        target.unlink()
    except FileNotFoundError:
        pass
