# 2026-10-07 ローカルHTTPの保護
- 外部サイトからの設定保存・リネーム・Undo・起動済み画面表示を、全POST共通の接続先・接続元・起動ごとのCSRFトークン検査で拒否する。
- JSONだけを受け付け、重複ヘッダー、不正な長さ、転送符号化、1MB超過を処理前に拒否する。画面とAPIは保存キャッシュを禁止する。
- ブラウザの共通通信関数と2重起動時の画面表示にトークンを付ける。PDF処理、承認、一括リネーム、履歴、Undoは変更しない。
- 回帰テスト: tests/test_http_security.py。別サイト、null Origin、接続先の差し替え、重複ヘッダー、欠落・旧・非ASCIIトークンを拒否し、localhost/127.0.0.1の正規要求とネイティブ要求を許可する。
- 検証: .venv/Scripts/python.exe -m pytest = 74 passed / 11 subtests passed、git diff --check成功。
- packaging/windows/build_windows.ps1 -SkipInstallでWindows EXEのビルド成功。既存インストール先への配置と実資料のリネームは実施していない。
