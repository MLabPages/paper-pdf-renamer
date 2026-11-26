"""Send approved originals to the existing PC-only MLab reader queue."""

import http.client
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode


ENGINE = "plamo-0.8b"
PORTS = (8765, *range(8767, 8776))


def _request(port, token, target, body=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    try:
        headers = {"X-Token": token}
        if body is not None:
            headers["Content-Type"] = "application/pdf"
        connection.request("GET" if body is None else "POST", target, body, headers)
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(f"論文リーダーが処理できませんでした（HTTP {response.status}）")
        return response.read()
    finally:
        connection.close()


def reader_connection():
    settings = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    settings /= "PLaMoPaperReader/settings.json"
    try:
        token = json.loads(settings.read_text(encoding="utf-8"))["share_token"]
    except (OSError, ValueError, KeyError, TypeError):
        token = None
    if isinstance(token, str) and token:
        for port in PORTS:
            try:
                state = json.loads(_request(port, token, "/api/state"))
                engine = next(e for e in state["engines"] if e["id"] == ENGINE and e["kind"] == "local")
            except (OSError, ValueError, KeyError, TypeError, StopIteration, RuntimeError, http.client.HTTPException):
                continue
            if not engine.get("available"):
                raise RuntimeError("MLab論文リーダーで PLaMo 0.8B を準備してください")
            return port, token
    raise RuntimeError("MLab論文リーダーを起動してからリネームしてください")


def translate_pdf(source: Path) -> bytes:
    port, token = reader_connection()
    query = urlencode({"engine": ENGINE, "name": source.name, "refs": "0"})
    doc_id = json.loads(_request(port, token, "/api/translate?" + query, source.read_bytes()))["id"]
    if not isinstance(doc_id, str) or not re.fullmatch(r"[0-9a-f]{12}", doc_id):
        raise RuntimeError("論文リーダーから正しい受付番号が返りませんでした")
    endpoint = "/api/doc/" + doc_id
    deadline = time.monotonic() + 4 * 3600
    while time.monotonic() < deadline:
        job = json.loads(_request(port, token, endpoint))["translations"][ENGINE]
        if job["status"] == "done":
            pdf = _request(port, token, f"{endpoint}/translated/{ENGINE}")
            if not pdf.startswith(b"%PDF-"):
                raise RuntimeError("翻訳結果がPDFではありません")
            return pdf
        if job["status"] not in ("queued", "running"):
            raise RuntimeError(job.get("error") or "翻訳が中止されました")
        time.sleep(2)
    raise RuntimeError("翻訳の完了を確認できませんでした")


def save_translation(destination: Path, pdf: bytes) -> bool:
    """Publish a complete PDF without replacing any existing destination."""
    with tempfile.NamedTemporaryFile(dir=destination.parent, suffix=".tmp", delete=False) as stream:
        temporary = Path(stream.name)
    try:
        temporary.write_bytes(pdf)
        try:
            if os.name == "nt":
                temporary.rename(destination)
            else:
                os.link(temporary, destination)
        except FileExistsError:
            return False
        return True
    finally:
        temporary.unlink(missing_ok=True)
