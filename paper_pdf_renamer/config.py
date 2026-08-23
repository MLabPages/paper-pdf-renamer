from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from string import Formatter


APP_NAME = "paper-pdf-renamer"
FORMAT_TEMPLATE = "{author}_{year}_{title}.pdf"
LEGACY_FORMAT_TEMPLATE = "著者_出版年_論文タイトル.pdf"
FORMAT_FIELDS = frozenset({"author", "first_author", "year", "title", "doi"})


def validate_format_template(value: str) -> str:
    """Validate a user-editable filename template and return its trimmed form."""
    template = str(value).strip()
    if template == LEGACY_FORMAT_TEMPLATE:
        return FORMAT_TEMPLATE
    if not template:
        raise ValueError("ファイル名形式を入力してください")

    fields: set[str] = set()
    try:
        parsed = Formatter().parse(template)
        for _, field_name, format_spec, conversion in parsed:
            if field_name is None:
                continue
            field = field_name.split(".", 1)[0].split("[", 1)[0]
            if field not in FORMAT_FIELDS:
                raise ValueError(f"未対応の項目です: {{{field}}}")
            if format_spec or conversion:
                raise ValueError(f"項目の書式指定には対応していません: {{{field}}}")
            fields.add(field)
    except ValueError:
        raise

    missing = {"author", "year", "title"} - fields
    if missing:
        names = ", ".join(f"{{{field}}}" for field in sorted(missing))
        raise ValueError(f"必須項目がありません: {names}")
    return template


def safe_format_template(value: object) -> str:
    """Load a legacy/corrupt setting without preventing the app from starting."""
    try:
        return validate_format_template(str(value))
    except (TypeError, ValueError):
        return FORMAT_TEMPLATE


def app_data_dir() -> Path:
    """ユーザー単位の設定保存先を返す（管理者権限不要）。"""

    override = os.environ.get("PAPER_PDF_RENAMER_DATA_DIR")
    if override:
        return Path(override)
    base = os.environ.get("APPDATA")
    return (Path(base) if base else Path.home() / "AppData" / "Roaming") / APP_NAME


def settings_path() -> Path:
    return app_data_dir() / "settings.json"


def default_downloads() -> str:
    return str(Path.home() / "Downloads")


@dataclass
class Settings:
    watch_folders: list[str] = field(default_factory=lambda: [default_downloads()])
    monitor_enabled: bool = False
    recursive: bool = False
    format_template: str = FORMAT_TEMPLATE
    max_title_length: int = 100
    min_confidence: float = 0.90
    mailto: str = ""
    auto_start: bool = False
    poll_interval: float = 5.0
    history_dir: str = field(default_factory=lambda: str(app_data_dir() / "history"))

    def validate(self) -> "Settings":
        self.watch_folders = [str(Path(folder)) for folder in self.watch_folders if str(folder).strip()]
        if not self.watch_folders:
            self.watch_folders = [default_downloads()]
        self.format_template = safe_format_template(self.format_template)
        self.max_title_length = max(10, min(int(self.max_title_length), 200))
        # 初版の安全ゲートは90%を下限にする。より厳しい値は自由に設定できる。
        self.min_confidence = max(0.90, min(float(self.min_confidence), 1.0))
        self.poll_interval = max(1.0, min(float(self.poll_interval), 60.0))
        if not self.history_dir:
            self.history_dir = str(app_data_dir() / "history")
        return self

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Settings":
        target = Path(path) if path else settings_path()
        if not target.exists():
            return cls().validate()
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return cls().validate()
        if not isinstance(payload, dict):
            return cls().validate()
        defaults = asdict(cls())
        values = {key: payload.get(key, value) for key, value in defaults.items()}
        if not isinstance(values.get("watch_folders"), list):
            values["watch_folders"] = defaults["watch_folders"]
        return cls(**values).validate()

    def save(self, path: str | Path | None = None) -> Path:
        target = Path(path) if path else settings_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f"{target.name}.tmp")
        temporary.write_text(json.dumps(asdict(self.validate()), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(target)
        return target
