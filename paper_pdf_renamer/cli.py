from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import FORMAT_TEMPLATE
from .crossref import CrossrefClient, resolve_metadata
from .history import HistoryLog
from .operations import BatchScanner, PollingWatcher, RenameService, default_resolver
from .pdf_extract import extract_pdf
from .undo import undo_last


def _json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _make_service(args: argparse.Namespace) -> RenameService:
    client = CrossrefClient(mailto=args.mailto)
    resolver = lambda path: resolve_metadata(extract_pdf(path), client=client)
    return RenameService(
        resolver=resolver,
        history=HistoryLog(args.history_dir),
        min_confidence=args.min_confidence,
        max_title_length=args.max_title_length,
        format_template=args.format_template,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="論文PDFを安全側に自動リネームするCLI")
    parser.add_argument("--history-dir", default=".paper-pdf-renamer", help="JSON/CSV履歴の保存先")
    parser.add_argument("--min-confidence", type=float, default=0.90)
    parser.add_argument("--max-title-length", type=int, default=100)
    parser.add_argument("--format-template", default=FORMAT_TEMPLATE, help="ファイル名形式（{author} {year} {title} {doi}を使用可能）")
    parser.add_argument("--mailto", help="CrossrefのUser-Agentに付ける連絡先（任意）")
    sub = parser.add_subparsers(dest="command", required=True)

    inspect = sub.add_parser("inspect", help="PDFの候補と信頼度を表示（変更しない）")
    inspect.add_argument("pdf", type=Path)

    rename = sub.add_parser("rename", help="安全ゲートを通ったPDFだけ1件リネーム")
    rename.add_argument("pdf", type=Path)

    scan = sub.add_parser("scan", help="一括候補を作成して表示（変更しない）")
    scan.add_argument("folder", type=Path)
    scan.add_argument("--non-recursive", action="store_true")
    scan.add_argument("--plan-file", type=Path, help="レビュー用JSONプランの保存先")

    apply = sub.add_parser("apply", help="レビュー済みプランの承認パスだけ実行")
    apply.add_argument("--plan-file", type=Path, required=True)
    apply.add_argument("--approve", action="append", type=Path, default=[], help="承認する元PDF（複数指定可）")
    apply.add_argument("--approve-all-ready", action="store_true", help="プラン内のreadyを全件承認する")

    watch = sub.add_parser("watch", help="フォルダをポーリングして新規PDFを処理")
    watch.add_argument("folder", type=Path)
    watch.add_argument("--interval", type=float, default=5.0)
    watch.add_argument("--recursive", action="store_true")
    watch.add_argument("--once", action="store_true", help="1回だけポーリング")

    sub.add_parser("gui", help="最小GUIを起動")
    shortcut = sub.add_parser("shortcut", help="デスクトップにローカル画面のショートカットを作成")
    shortcut.add_argument("--path", type=Path, help="ショートカットの保存先（既定はデスクトップ）")
    shortcut.add_argument("--name", default="論文PDFファイル名整理.lnk")
    sub.add_parser("undo", help="直近の成功したリネームを元に戻す")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "inspect":
            _json(resolve_metadata(extract_pdf(args.pdf), CrossrefClient(mailto=args.mailto)).to_dict())
            return 0
        if args.command == "undo":
            _json(undo_last(HistoryLog(args.history_dir)))
            return 0
        if args.command == "gui":
            from .gui import main as gui_main

            return gui_main()
        if args.command == "shortcut":
            from .shortcuts import create_desktop_shortcut

            print(create_desktop_shortcut(shortcut_path=args.path, name=args.name))
            return 0

        service = _make_service(args)
        if args.command == "rename":
            _json(service.process(args.pdf, auto=True).to_dict())
            return 0
        if args.command == "scan":
            candidates = BatchScanner(service).scan(args.folder, recursive=not args.non_recursive)
            if args.plan_file:
                BatchScanner.save_plan(candidates, args.plan_file)
            _json({"plan_file": str(args.plan_file) if args.plan_file else None, "items": [item.to_dict() for item in candidates]})
            return 0
        if args.command == "apply":
            scanner = BatchScanner(service)
            candidates = scanner.load_plan(args.plan_file)
            approvals = [item.source_path for item in candidates if item.ready] if args.approve_all_ready else args.approve
            _json({"items": [item.to_dict() for item in scanner.execute_approved(candidates, approvals)]})
            return 0
        if args.command == "watch":
            watcher = PollingWatcher(args.folder, service, recursive=args.recursive)
            if args.once:
                _json({"items": [item.to_dict() for item in watcher.poll()]})
            else:
                print(f"監視中: {args.folder}（終了はCtrl+C）")
                watcher.run(interval=args.interval)
            return 0
    except KeyboardInterrupt:
        print("監視を終了しました", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
