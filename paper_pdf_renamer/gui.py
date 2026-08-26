from __future__ import annotations

import base64
import binascii
import json
import os
import socket
import subprocess
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from .config import Settings, validate_format_template
from .crossref import CrossrefClient, resolve_metadata
from .desktop_window import open_app_window
from .history import HistoryLog
from .instance import SingleInstanceLock, clear_server_state, read_server_port, write_server_state
from .models import RenameCandidate
from .operations import BatchScanner, PollingWatcher, RenameService, metadata_from_history
from .pdf_extract import extract_pdf
from .startup import is_supported as startup_is_supported
from .startup import set_enabled as startup_set_enabled
from .undo import undo_last
from .watch_state import WatchManifest


STATUS_LABELS = {"ready": "候補", "held": "要確認", "failed": "失敗", "renamed": "変更済み"}
REASON_LABELS = {
    "doi-missing": "DOIが見つかりません",
    "title-missing": "タイトルがありません",
    "author-missing": "著者がありません",
    "author-count-unknown": "履歴に著者一覧がなく、2名か3名以上か判定できません",
    "year-missing": "出版年がありません",
    "author-mismatch": "著者が一致しません",
    "title-mismatch": "タイトルが一致しません",
    "title-search-match-too-low": "タイトル候補の一致率が低いです",
    "verified-metadata-unavailable": "検証済みメタデータを取得できません",
    "paper-type-unconfirmed": "論文種別を確認できません",
    "non-paper-type": "論文以外の種別です",
    "low-confidence": "信頼度が基準未満です",
    "already-correct-name": "すでに候補名です",
    "source-file-missing": "別の処理で先に変更された可能性があります",
    "added-while-stopped": "監視停止中に追加されたPDFです。確認後に実行してください",
}
WARNING_LABELS = {
    "author-mismatch": "著者は補助照合で不一致ですが、DOI・タイトル等を優先しました",
    "verified-by-openalex": "Crossref未収録のためOpenAlexでも照合しました",
    "doi-missing-verified-by-openalex": "DOIはありませんが、OpenAlexでタイトル・著者・年が一致しました",
}


def _reason_text(reasons: list[str] | tuple[str, ...]) -> str:
    return "、".join(REASON_LABELS.get(reason, reason) for reason in reasons)


def _warning_text(warnings: list[str] | tuple[str, ...]) -> str:
    return "、".join(WARNING_LABELS.get(warning, warning) for warning in warnings)


def _candidate_json(candidate_id: str, candidate: RenameCandidate) -> dict[str, Any]:
    value = candidate.to_dict()
    value["id"] = candidate_id
    value["status_label"] = STATUS_LABELS.get(candidate.status, candidate.status)
    value["reason_text"] = _reason_text(candidate.reasons)
    value["warning_text"] = _warning_text(candidate.metadata.warnings)
    return value


def select_windows_folder() -> str | None:
    """Open the native Windows folder picker without sending PDF contents anywhere."""
    if os.name != "nt":
        raise OSError("フォルダ選択はWindowsで利用できます")

    script = r"""
Add-Type -AssemblyName System.Windows.Forms
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = '監視対象フォルダを選択してください'
$dialog.ShowNewFolderButton = $false
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    [Console]::WriteLine([Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($dialog.SelectedPath)))
}
"""
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Sta",
                "-WindowStyle",
                "Hidden",
                "-EncodedCommand",
                encoded,
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError as exc:
        raise OSError("PowerShellを起動できませんでした") from exc
    except subprocess.TimeoutExpired as exc:
        raise OSError("フォルダ選択がタイムアウトしました") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise OSError(detail or "フォルダ選択に失敗しました") from exc

    result = completed.stdout.strip()
    if not result:
        return None
    try:
        selected = base64.b64decode(result, validate=True).decode("utf-8").strip()
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise OSError("フォルダ選択結果を読み取れませんでした") from exc
    return selected or None


class AppState:
    def __init__(self, settings: Settings):
        self.settings = settings.validate()
        self.history = HistoryLog(self.settings.history_dir)
        self.watch_manifest = WatchManifest()
        self._lock = threading.RLock()
        self._rename_lock = threading.Lock()
        self._candidates: dict[str, RenameCandidate] = {}
        self._watchers: list[tuple[PollingWatcher, threading.Event, threading.Thread]] = []
        self.monitoring = False
        self.message = "停止中"

    def _service(self) -> RenameService:
        client = CrossrefClient(mailto=self.settings.mailto or None)
        return RenameService(
            lambda path: resolve_metadata(extract_pdf(path), client=client),
            history=self.history,
            min_confidence=self.settings.min_confidence,
            max_title_length=self.settings.max_title_length,
            format_template=self.settings.format_template,
        )

    def _upsert(self, candidate: RenameCandidate) -> None:
        source_key = str(candidate.source_path.resolve()).casefold()
        for candidate_id, old in list(self._candidates.items()):
            if str(old.source_path.resolve()).casefold() == source_key:
                self._candidates[candidate_id] = candidate
                return
        self._candidates[uuid4().hex] = candidate

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "settings": {
                    "watch_folders": self.settings.watch_folders,
                    "monitor_enabled": self.settings.monitor_enabled,
                    "recursive": self.settings.recursive,
                    "format_template": self.settings.format_template,
                    "max_title_length": self.settings.max_title_length,
                    "min_confidence": self.settings.min_confidence,
                    "mailto": self.settings.mailto,
                    "auto_start": self.settings.auto_start,
                },
                "monitoring": self.monitoring,
                "message": self.message,
                "candidates": [_candidate_json(key, value) for key, value in self._candidates.items()],
                "history": self.history.read()[-100:],
            }

    def save_settings(self, payload: dict[str, Any]) -> None:
        with self._lock:
            folders = payload.get("watch_folders", self.settings.watch_folders)
            if not isinstance(folders, list):
                raise ValueError("watch_foldersは配列で指定してください")
            self.settings.watch_folders = [str(Path(str(folder)).expanduser()) for folder in folders if str(folder).strip()]
            self.settings.recursive = bool(payload.get("recursive", self.settings.recursive))
            self.settings.format_template = validate_format_template(str(payload.get("format_template", self.settings.format_template)))
            self.settings.max_title_length = int(payload.get("max_title_length", self.settings.max_title_length))
            self.settings.min_confidence = float(payload.get("min_confidence", self.settings.min_confidence))
            self.settings.mailto = str(payload.get("mailto", self.settings.mailto)).strip()
            self.settings.auto_start = bool(payload.get("auto_start", self.settings.auto_start))
            self.settings.validate().save()
            startup_error: OSError | None = None
            if startup_is_supported():
                try:
                    startup_set_enabled(self.settings.auto_start)
                except OSError as exc:
                    # 検証環境や企業PCでHKCUが制限されても、アプリ設定の保存は続ける。
                    startup_error = exc
            self.message = "設定を保存しました" if startup_error is None else "設定を保存しました（Windows起動時設定は変更できませんでした）"

    def scan(self) -> int:
        with self._lock:
            service = self._service()
            folders = list(self.settings.watch_folders)
            recursive = self.settings.recursive
        found: list[RenameCandidate] = []
        for folder in folders:
            path = Path(folder)
            if path.is_dir():
                found.extend(BatchScanner(service).scan(path, recursive=recursive))
        with self._lock:
            for candidate in found:
                self._upsert(candidate)
            self.message = f"スキャン完了: {len(found)}件。変更前後を確認してください"
        return len(found)

    def scan_folder(self, folder: str | Path, recursive: bool | None = None) -> int:
        """監視設定を変えずに、選んだフォルダ1つだけを確認する。"""

        path = Path(str(folder).strip())
        if not path.is_dir():
            raise ValueError(f"フォルダが見つかりません: {path}")
        with self._lock:
            service = self._service()
            include_subfolders = self.settings.recursive if recursive is None else bool(recursive)
        found = BatchScanner(service).scan(path, recursive=include_subfolders)
        with self._lock:
            for candidate in found:
                self._upsert(candidate)
            self.message = (
                f"「{path.name or path}」のスキャン完了: {len(found)}件。変更前後を確認してください"
            )
        return len(found)

    def reformat_history(self) -> int:
        """成功履歴を再整理し、過去の要確認PDFを最新ロジックで再確認する。"""

        with self._lock:
            service = self._service()
            renamed_records = self.history.latest_successful_renames()
            held_records = self.history.latest_held_reviews()
        found_by_source: dict[str, RenameCandidate] = {}
        skipped = 0
        for record in renamed_records:
            raw_path = record.get("new_path")
            metadata = metadata_from_history(record)
            if not raw_path or metadata is None:
                skipped += 1
                continue
            source = Path(str(raw_path))
            if not source.is_file():
                skipped += 1
                continue
            found_by_source[str(source.resolve()).casefold()] = service.make_candidate_from_metadata(source, metadata)
        rescanned = 0
        for record in held_records:
            raw_path = record.get("original_path")
            if not raw_path:
                skipped += 1
                continue
            source = Path(str(raw_path))
            if not source.is_file():
                skipped += 1
                continue
            found_by_source[str(source.resolve()).casefold()] = service.make_candidate(source)
            rescanned += 1
        found = list(found_by_source.values())
        with self._lock:
            for candidate in found:
                self._upsert(candidate)
            suffix = f"（{skipped}件は現在の場所を確認できません）" if skipped else ""
            self.message = (
                f"履歴から再整理・再確認候補を作成しました: {len(found)}件"
                f"（要確認の再スキャン {rescanned}件）{suffix}。変更前後を確認してください"
            )
        return len(found)

    def apply(self, candidate_ids: list[str]) -> int:
        with self._lock:
            selected = [self._candidates[item] for item in candidate_ids if item in self._candidates]
            ready = [candidate for candidate in selected if candidate.ready]
            if not ready:
                self.message = "実行可能な候補が選択されていません"
                return 0
            with self._rename_lock:
                results = BatchScanner(self._service()).execute_approved(ready, [item.source_path for item in ready])
            for result in results:
                self._upsert(result)
                if result.status == "renamed" and result.destination_path:
                    self.watch_manifest.complete(result.source_path, result.destination_path)
                    for watcher, _, _ in self._watchers:
                        watcher.mark_completed(result.source_path, result.destination_path)
            self.message = f"{len(results)}件を変更しました"
            return len(results)

    def undo(self) -> dict[str, Any]:
        with self._lock:
            with self._rename_lock:
                result = undo_last(self.history)
            self.message = "直近のリネームを元に戻しました" if result.get("status") == "undone" else "Undoできる成功履歴がありません"
            return result

    def start_monitor(self) -> None:
        self.stop_monitor(persist=False)
        service = self._service()
        with self._lock:
            folders = list(self.settings.watch_folders)
            recursive = self.settings.recursive
            interval = self.settings.poll_interval
        started = 0
        for folder in folders:
            path = Path(folder)
            if not path.is_dir():
                continue
            watcher = PollingWatcher(path, service, recursive=recursive, manifest=self.watch_manifest)
            watcher.poll()  # 開始前からあるPDFは既存資産として基準化
            stop = threading.Event()
            thread = threading.Thread(target=self._watch_loop, args=(watcher, stop, interval), daemon=True)
            self._watchers.append((watcher, stop, thread))
            thread.start()
            started += 1
        with self._lock:
            self.monitoring = started > 0
            self.settings.monitor_enabled = started > 0
            self.settings.save()
            self.message = f"監視中（{started}フォルダ）" if started else "監視対象フォルダが見つかりません"

    def stop_monitor(self, persist: bool = True) -> None:
        watchers, self._watchers = self._watchers, []
        for _, stop, thread in watchers:
            stop.set()
            thread.join(timeout=0.5)
        with self._lock:
            self.monitoring = False
            self.settings.monitor_enabled = False
            if persist:
                self.settings.save()
            self.message = "監視を停止しました"

    def _watch_loop(self, watcher: PollingWatcher, stop: threading.Event, interval: float) -> None:
        while not stop.wait(interval):
            try:
                with self._lock:
                    with self._rename_lock:
                        results = watcher.poll()
                    if results:
                        for result in results:
                            self._upsert(result)
                        self.message = f"新規PDFを処理: {len(results)}件"
            except Exception as exc:
                with self._lock:
                    self.message = f"監視エラー: {exc}"
                continue


HTML = r"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>論文PDFファイル名整理</title>
<style>
:root { color-scheme: light; font-family: "Segoe UI", "Yu Gothic UI", sans-serif; color: #1f2937; background: #f5f7fb; }
* { box-sizing: border-box; }
body { margin: 0; }
header { background: #17324d; color: white; padding: 18px 24px; display: flex; gap: 16px; align-items: center; flex-wrap: wrap; }
header h1 { margin: 0; font-size: 1.2rem; }
.badge { border-radius: 999px; background: #e5e7eb; color: #374151; padding: 5px 12px; font-size: .85rem; }
.badge.on { background: #b8f0ce; color: #115c34; }
main { max-width: 1440px; margin: 0 auto; padding: 18px; }
.layout { display: grid; grid-template-columns: 330px 1fr; gap: 16px; align-items: start; }
.layout > * { min-width: 0; }
section { background: white; border: 1px solid #dbe2ea; border-radius: 12px; padding: 15px; box-shadow: 0 2px 8px #17324d0d; }
h2 { font-size: 1rem; margin: 0 0 12px; }
label { display: block; font-size: .86rem; font-weight: 600; margin: 10px 0 5px; }
input, textarea, button { font: inherit; }
input[type=text], input[type=number], textarea { width: 100%; border: 1px solid #cbd5e1; border-radius: 7px; padding: 8px; background: #fff; }
textarea { min-height: 90px; resize: vertical; }
.folder-actions { display: flex; justify-content: flex-end; margin-top: 6px; }
input[readonly] { background: #f1f5f9; }
.inline { display: flex; align-items: center; gap: 8px; margin: 12px 0; }
.inline label { margin: 0; }
.guide { background: #eff6ff; border: 1px solid #bfdbfe; border-radius: 8px; padding: 10px 12px; margin-bottom: 14px; font-size: .84rem; line-height: 1.5; }
.guide p { margin: 5px 0; }
.guide p:first-child { margin-top: 0; }
.guide p:last-child { margin-bottom: 0; }
.help { color: #64748b; font-size: .8rem; line-height: 1.45; margin: 5px 0 8px; }
button { border: 0; border-radius: 7px; padding: 8px 12px; cursor: pointer; background: #2563eb; color: white; }
button.secondary { background: #e2e8f0; color: #1e293b; }
button:disabled { opacity: .55; cursor: default; }
.actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 14px; }
.status-line { color: #475569; min-height: 1.4em; margin: 10px 0 0; font-size: .9rem; }
.table-wrap { overflow: auto; border: 1px solid #e2e8f0; border-radius: 8px; }
table { width: 100%; border-collapse: collapse; min-width: 760px; }
th, td { text-align: left; padding: 8px 9px; border-bottom: 1px solid #e2e8f0; vertical-align: top; font-size: .86rem; }
th { background: #f8fafc; white-space: nowrap; }
tr:last-child td { border-bottom: 0; }
.hold { color: #9a6700; }
.warning { color: #9a6700; }
.failed { color: #b42318; }
.ready { color: #115c34; font-weight: 600; }
.empty { color: #64748b; padding: 16px 8px; }
.wide { grid-column: 1 / -1; }
@media (max-width: 900px) { .layout { grid-template-columns: 1fr; } .wide { grid-column: auto; } main { padding: 10px; } section { padding: 12px; } }
</style>
</head>
<body>
<header><h1>論文PDFファイル名整理</h1><span id="monitor-badge" class="badge">停止中</span><span id="message"></span></header>
<main>
<div class="layout">
<section>
<h2>監視と設定</h2>
<div class="guide">
<p><strong>新しくダウンロードするPDF：</strong>フォルダを選択 → 設定を保存 → 「新しいPDFを自動監視」をON。</p>
<p><strong>すでにあるPDF：</strong>フォルダを選択 → 設定を保存 → 「既存PDFをスキャン」 → 候補を確認 → リネーム。特定のフォルダだけ整理したいときは「選んだフォルダだけスキャン」。</p>
<p><strong>変更済み／過去に要確認のPDF：</strong>「履歴から再整理／要確認を再スキャン」で、変更済みPDFは保存情報から再整理し、要確認PDFは最新の照合方法で再確認します。</p>
<p><strong>監視停止中に追加されたPDF：</strong>再起動後に候補として表示します。確認して実行するまで変更しません。</p>
<p>設定を保存しただけではファイル名は変わりません。信頼度が低いPDFは安全のため保留になります。</p>
</div>
<div class="inline"><input id="monitor" type="checkbox"><label for="monitor">新しいPDFを自動監視</label></div>
<p class="help">ONにすると、監視開始後に新しく保存されたPDFを対象にします。監視開始前からあるPDFは対象外です。</p>
<label for="folders">監視対象フォルダ（1行1フォルダ）</label>
<textarea id="folders" spellcheck="false"></textarea>
<div class="folder-actions"><button id="choose-folder" class="secondary" type="button">フォルダを選択…</button></div>
<p class="help">フォルダを選んだ後、必ず「設定を保存」を押してください。</p>
<div class="inline"><input id="recursive" type="checkbox"><label for="recursive">サブフォルダも監視</label></div>
<label for="format">ファイル名形式</label><input id="format" type="text" spellcheck="false">
<p class="help">使用項目：<code>{author}</code>、<code>{year}</code>、<code>{title}</code>、<code>{doi}</code>。例：<code>{author} ({year}). - {title}.pdf</code></p>
<label for="max-title">タイトル最大長</label><input id="max-title" type="number" min="10" max="200">
<label for="confidence">自動変更の信頼度基準（0.90以上）</label><input id="confidence" type="number" min="0.90" max="1" step="0.01">
<label for="mailto">Crossref連絡先（任意）</label><input id="mailto" type="text" placeholder="your-name@example.com">
<div class="inline"><input id="auto-start" type="checkbox"><label for="auto-start">Windows起動時に自動起動</label></div>
<div class="actions"><button id="save">設定を保存（必須）</button><button id="scan" class="secondary">既存PDFをスキャン（変更なし）</button><button id="scan-folder" class="secondary">選んだフォルダだけスキャン（変更なし）</button><button id="reformat" class="secondary">履歴から再整理／要確認を再スキャン（変更なし）</button><button id="undo" class="secondary">直近をUndo</button></div>
<p class="help">「選んだフォルダだけスキャン」は、監視対象フォルダを変えずにその場で選んだ1フォルダだけを確認します。監視対象が大きいときや、特定のフォルダだけ整理したいときに使ってください。上の「サブフォルダも監視」がONなら、その下のフォルダも見ます。</p>
<p class="status-line" id="local-status"></p>
</section>
<section>
<h2>リネーム候補（スキャン結果／要確認一覧）</h2>
<p class="help">スキャンでは変更しません。状態が「候補」の行にチェックを入れ、変更前と変更後を確認してから実行してください。警告は補助情報であり、照合の主根拠が一致していれば実行できます。</p>
<div class="actions"><button id="apply">チェックした候補をリネーム</button></div>
<div class="table-wrap"><table><thead><tr><th></th><th>状態</th><th>変更前</th><th>変更後候補</th><th>信頼度</th><th>理由</th></tr></thead><tbody id="candidates"></tbody></table></div>
</section>
<section class="wide">
<h2>処理履歴</h2>
<div class="table-wrap"><table><thead><tr><th>日時</th><th>状態</th><th>元ファイル</th><th>新ファイル</th><th>DOI</th><th>タイトル</th><th>信頼度</th></tr></thead><tbody id="history"></tbody></table></div>
</section>
</div>
</main>
<script>
const $ = (id) => document.getElementById(id);
let settingsDirty = false;
let lastServerSettings = null;
let applyInProgress = false;
async function api(url, options={}) { const response = await fetch(url, {headers:{"Content-Type":"application/json"}, ...options}); const data = await response.json(); if (!response.ok) throw new Error(data.error || "通信に失敗しました"); return data; }
function cell(row, value, className="") { const td = document.createElement("td"); td.textContent = value ?? "—"; if (className) td.className = className; row.appendChild(td); return td; }
function formSettingsSnapshot() { return {folders:$("folders").value,recursive:$("recursive").checked,format:$("format").value,maxTitle:$("max-title").value,confidence:$("confidence").value,mailto:$("mailto").value,autoStart:$("auto-start").checked}; }
function selectedCandidateIds() { return new Set([...document.querySelectorAll("#candidates input[type=checkbox]:checked")].map((input) => input.dataset.id)); }
function render(state) {
  const s = state.settings;
  const selectedIds = selectedCandidateIds();
  const userChanged = settingsDirty || (lastServerSettings !== null && JSON.stringify(formSettingsSnapshot()) !== JSON.stringify(lastServerSettings));
  if (!userChanged) {
    $("folders").value = s.watch_folders.join("\n"); $("recursive").checked = s.recursive; $("format").value = s.format_template;
    $("max-title").value = s.max_title_length; $("confidence").value = Number(s.min_confidence).toFixed(2); $("mailto").value = s.mailto || ""; $("auto-start").checked = s.auto_start;
  }
  lastServerSettings = {folders:s.watch_folders.join("\n"),recursive:s.recursive,format:s.format_template,maxTitle:String(s.max_title_length),confidence:Number(s.min_confidence).toFixed(2),mailto:s.mailto || "",autoStart:s.auto_start};
  $("monitor").checked = state.monitoring;
  const badge = $("monitor-badge"); badge.textContent = state.monitoring ? "監視中" : "停止中"; badge.className = state.monitoring ? "badge on" : "badge";
  $("message").textContent = state.message || "";
  const candidates = $("candidates"); candidates.replaceChildren();
  if (!state.candidates.length) { const row = candidates.insertRow(); const td = row.insertCell(); td.colSpan = 6; td.className = "empty"; td.textContent = "候補はありません。既存PDFなら「既存PDFをスキャン」または「履歴から再整理／要確認を再スキャン」、新規PDFなら「新しいPDFを自動監視」を使ってください。"; }
  for (const item of state.candidates.slice().reverse()) { const row = candidates.insertRow(); const check = row.insertCell(); if (item.status === "ready") { const input = document.createElement("input"); input.type = "checkbox"; input.dataset.id = item.id; input.checked = selectedIds.has(item.id); check.appendChild(input); } cell(row, item.status_label, item.status); cell(row, item.source_path && item.source_path.split(/[\\/]/).pop()); cell(row, item.destination_path && item.destination_path.split(/[\\/]/).pop()); cell(row, `${Math.round(Number(item.metadata.confidence || 0) * 100)}%`); const detail = item.reason_text || item.warning_text || "—"; const detailClass = item.status === "held" ? "hold" : item.status === "failed" ? "failed" : item.warning_text ? "warning" : ""; cell(row, detail, detailClass); }
  const history = $("history"); history.replaceChildren();
  if (!state.history.length) { const row = history.insertRow(); const td = row.insertCell(); td.colSpan = 7; td.className = "empty"; td.textContent = "まだ処理履歴はありません。"; }
  for (const item of state.history.slice().reverse()) { const row = history.insertRow(); cell(row, item.timestamp); cell(row, item.status || item.action); cell(row, item.original_filename); cell(row, item.new_filename); cell(row, item.doi); cell(row, item.title); cell(row, `${Math.round(Number(item.confidence || 0) * 100)}%`); }
}
async function refresh() { try { render(await api("/api/state")); } catch (error) { $("local-status").textContent = error.message; } }
async function saveSettings(refreshNow=true) { const folders = $("folders").value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean); await api("/api/settings", {method:"POST", body:JSON.stringify({watch_folders:folders,recursive:$("recursive").checked,format_template:$("format").value,max_title_length:Number($("max-title").value),min_confidence:Number($("confidence").value),mailto:$("mailto").value,auto_start:$("auto-start").checked})}); settingsDirty = false; lastServerSettings = null; if (refreshNow) await refresh(); }
$("save").onclick = async () => { try { await saveSettings(); $("local-status").textContent = "設定を保存しました"; } catch (error) { $("local-status").textContent = error.message; } };
$("monitor").onchange = async () => { const enabled = $("monitor").checked; try { await saveSettings(false); const result = await api("/api/monitor", {method:"POST", body:JSON.stringify({enabled})}); await refresh(); $("local-status").textContent = result.message || (enabled ? "新しいPDFの自動監視を開始しました" : "自動監視を停止しました"); } catch (error) { $("local-status").textContent = error.message; } };
$("scan").onclick = async () => { try { $("local-status").textContent = "スキャン中…（変更はまだ行いません）"; await saveSettings(); const result = await api("/api/scan", {method:"POST", body:"{}"}); $("local-status").textContent = `${result.count}件の候補を作成しました`; await refresh(); } catch (error) { $("local-status").textContent = error.message; } };
$("scan-folder").onclick = async () => {
  const button = $("scan-folder");
  if (button.disabled) return;
  try {
    $("local-status").textContent = "フォルダ選択ダイアログを開いています…";
    const chosen = await api("/api/select-folder", {method:"POST", body:"{}"});
    if (!chosen.path) { $("local-status").textContent = "フォルダ選択をキャンセルしました"; return; }
    button.disabled = true;
    $("local-status").textContent = `${chosen.path} をスキャン中…（変更はまだ行いません）`;
    const result = await api("/api/scan-folder", {method:"POST", body:JSON.stringify({folder:chosen.path, recursive:$("recursive").checked})});
    $("local-status").textContent = `${chosen.path} から${result.count}件の候補を作成しました`;
    await refresh();
  } catch (error) { $("local-status").textContent = error.message; }
  finally { button.disabled = false; }
};
$("reformat").onclick = async () => { try { $("local-status").textContent = "履歴から再整理・要確認PDFを再スキャン中…（変更はまだ行いません）"; await saveSettings(); const result = await api("/api/reformat-history", {method:"POST", body:"{}"}); $("local-status").textContent = `${result.count}件の再整理・再確認候補を作成しました`; await refresh(); } catch (error) { $("local-status").textContent = error.message; } };
$("apply").onclick = async () => {
  if (applyInProgress) return;
  const ids = [...document.querySelectorAll("#candidates input[type=checkbox]:checked")].map((input) => input.dataset.id);
  if (!ids.length) { $("local-status").textContent = "実行する候補を選択してください"; return; }
  if (!confirm(`${ids.length}件を確認済みとしてリネームしますか？`)) return;
  applyInProgress = true; $("apply").disabled = true;
  try {
    const result = await api("/api/apply", {method:"POST", body:JSON.stringify({ids})});
    $("local-status").textContent = result.count ? `${result.count}件をリネームしました` : "実行可能な候補がありません。状態が「候補」の行を選択してください";
    await refresh();
  } catch (error) { $("local-status").textContent = error.message; }
  finally { applyInProgress = false; $("apply").disabled = false; }
};
$("undo").onclick = async () => { if (!confirm("直近の成功したリネームを元に戻しますか？")) return; try { const result = await api("/api/undo", {method:"POST", body:"{}"}); $("local-status").textContent = result.result.status === "undone" ? "直近のリネームを元に戻しました" : "Undoできる履歴がありません"; await refresh(); } catch (error) { $("local-status").textContent = error.message; } };
$("choose-folder").onclick = async () => { try { $("local-status").textContent = "フォルダ選択ダイアログを開いています…"; const result = await api("/api/select-folder", {method:"POST", body:"{}"}); if (!result.path) { $("local-status").textContent = "フォルダ選択をキャンセルしました"; return; } const folders = $("folders").value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean); if (!folders.some((folder) => folder.toLowerCase() === result.path.toLowerCase())) folders.push(result.path); $("folders").value = folders.join("\n"); settingsDirty = true; $("local-status").textContent = "フォルダを追加しました。設定を保存してください"; } catch (error) { $("local-status").textContent = error.message; } };
for (const id of ["folders", "recursive", "format", "max-title", "confidence", "mailto", "auto-start"]) { $(id).addEventListener("input", () => { settingsDirty = true; }); $(id).addEventListener("change", () => { settingsDirty = true; }); }
refresh(); setInterval(refresh, 2000);
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    state: AppState

    def log_message(self, format: str, *args: object) -> None:
        return

    def _json(self, status: HTTPStatus, payload: object) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length > 1_000_000:
            raise ValueError("リクエストが大きすぎます")
        raw = self.rfile.read(length) if length else b"{}"
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("JSONオブジェクトが必要です")
        return value

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            encoded = HTML.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        elif path == "/api/state":
            self._json(HTTPStatus.OK, self.state.snapshot())
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            payload = self._body()
            if path == "/api/settings":
                self.state.save_settings(payload)
                result: object = self.state.snapshot()
            elif path == "/api/monitor":
                if bool(payload.get("enabled")):
                    self.state.start_monitor()
                else:
                    self.state.stop_monitor()
                result = self.state.snapshot()
            elif path == "/api/scan":
                result = {"count": self.state.scan()}
            elif path == "/api/scan-folder":
                folder = str(payload.get("folder") or "").strip()
                if not folder:
                    raise ValueError("スキャンするフォルダを指定してください")
                recursive = payload.get("recursive")
                result = {
                    "count": self.state.scan_folder(
                        folder, None if recursive is None else bool(recursive)
                    )
                }
            elif path == "/api/reformat-history":
                result = {"count": self.state.reformat_history()}
            elif path == "/api/apply":
                ids = payload.get("ids", [])
                if not isinstance(ids, list):
                    raise ValueError("idsは配列で指定してください")
                result = {"count": self.state.apply([str(item) for item in ids])}
            elif path == "/api/undo":
                result = {"result": self.state.undo()}
            elif path == "/api/select-folder":
                result = {"path": select_windows_folder()}
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            self._json(HTTPStatus.OK, result)
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})


def _available_port(preferred: int) -> int:
    """既存のローカルアプリと衝突しない待受ポートを選ぶ。"""

    if preferred == 0:
        return 0
    for candidate in range(preferred, preferred + 20):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", candidate))
            except OSError:
                continue
            return candidate
    raise OSError(f"ローカル画面のポートを確保できませんでした（{preferred}〜{preferred + 19}）")


def run_server(port: int = 8766, open_browser: bool = True, tray: bool | None = None) -> int:
    instance_lock = SingleInstanceLock()
    if not instance_lock.acquire():
        existing_port = read_server_port() or port
        url = f"http://127.0.0.1:{existing_port}/"
        if open_browser:
            threading.Timer(0.35, lambda: open_app_window(url)).start()
        print(f"論文PDFファイル名整理は起動済みです: {url}")
        return 0

    state = AppState(Settings.load())
    server: ThreadingHTTPServer | None = None
    actual_port: int | None = None
    try:
        if state.settings.monitor_enabled:
            state.start_monitor()
        handler = type("PaperPdfRenamerHandler", (Handler,), {"state": state})
        actual_port = _available_port(port)
        server = ThreadingHTTPServer(("127.0.0.1", actual_port), handler)
        write_server_state(actual_port)
        url = f"http://127.0.0.1:{actual_port}/"
        if open_browser:
            threading.Timer(0.35, lambda: open_app_window(url)).start()
        print(f"論文PDFファイル名整理: {url}")
        use_tray = os.name == "nt" if tray is None else tray
        if use_tray:
            from .tray import run_tray

            server_thread = threading.Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            run_tray(state, url, server.shutdown)
            server_thread.join(timeout=2.0)
        else:
            server.serve_forever()
    except KeyboardInterrupt:
        return 130
    finally:
        state.stop_monitor(persist=False)
        if server is not None:
            server.server_close()
        clear_server_state(actual_port)
        instance_lock.release()
    return 0


def main() -> int:
    return run_server(tray=os.name == "nt")


if __name__ == "__main__":
    raise SystemExit(main())
