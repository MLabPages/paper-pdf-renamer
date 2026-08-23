# paper-pdf-renamer

Google Scholar、出版社、ResearchGate、大学リポジトリなどから保存したPDFを、通常のWindowsフォルダ運用のまま安全側にリネームするためのPythonコア／CLIです。Zoteroは必須ではありません。

## 設計の要点

- PDFはローカルで読み取ります。Crossrefへ送るのはDOI、タイトルなどの文字列だけで、PDF本体は送信しません。
- DOIを最優先し、Crossrefの書誌情報とタイトル・第一著者を照合します。
- DOI不在、照合不一致、書誌情報不足、論文種別不明、低信頼の場合は自動変更せず `held`（要確認）にします。
- Windows禁止文字、末尾の空白・ピリオド、連続空白を正規化し、タイトルは既定100文字までにします。
- 同名ファイルは `(2)` のように連番にし、上書きしません。
- 一括処理は `scan` でJSONプランを作成し、`apply` で明示承認した元パスだけを実行します。
- 成功したリネームはJSONLとCSVに記録し、`undo` で直近の成功分を戻せます。
- 履歴には書誌情報も保存するため、ファイル名形式を変更した後も、PDFを再解析せずに「履歴から再整理」できます。
- 監視停止中に追加されたPDFは、前回監視時の一覧との差分として「要確認候補」にし、再起動直後に自動リネームしません。
- ReadableやAcrobatなどで日本語翻訳されたPDFは、本文の言語やファイル名の`ja`・`日本語`・`翻訳`などを検出し、`[ja]`を付けて原文PDFと区別します。

## セットアップ

標準ライブラリだけでもDOIの検出とAPIを使った照会は動きます。PDF本文・埋め込みメタデータを読むには任意依存を入れてください。

```powershell
py -m pip install -e .
py -m pip install -e ".[pdf]"
```

Windows版EXEを作る場合は、次のコマンドを使います。PyInstallerでローカルEXEを作るため、起動中に`python`とは表示されず、独自アイコンと「論文PDFファイル名整理」という名前になります。

```powershell
PowerShell -ExecutionPolicy Bypass -File .\packaging\windows\build_windows.ps1
PowerShell -ExecutionPolicy Bypass -File .\packaging\windows\install_windows.ps1
```

`install_windows.ps1`は管理者権限を使わず、`%LOCALAPPDATA%\Programs\PaperPdfRenamer`へ配置し、デスクトップとWindowsのスタートメニューにショートカットを作ります。設定・履歴は従来どおり`%APPDATA%\paper-pdf-renamer`を使うため、Python版からWindows版へ移行しても履歴は引き継がれます。

## CLI例

```powershell
# 1件を確認（ファイルは変更しない）
paper-pdf-renamer inspect "C:\Users\me\Downloads\download.pdf"

# 安全ゲートを通った場合だけ1件をリネーム
paper-pdf-renamer rename "C:\Users\me\Downloads\download.pdf"

# 既存フォルダをスキャンしてレビュー用プランを作る（変更しない）
paper-pdf-renamer scan "C:\Research\Brand" --plan-file .\rename-plan.json

# プランのうち、確認したパスだけ実行。--approveは複数指定可能
paper-pdf-renamer apply --plan-file .\rename-plan.json --approve "C:\Research\Brand\old.pdf"

# Downloadsをポーリング。完成後2回同じ状態になったPDFだけ処理
paper-pdf-renamer watch "$env:USERPROFILE\Downloads"

# 直近の成功リネームを復元
paper-pdf-renamer undo

# ローカル画面を起動（設定はユーザーのAppDataに保存）
paper-pdf-renamer gui

# デスクトップに起動ショートカットを作成
paper-pdf-renamer shortcut
```

`scan` は一覧と変更前・変更後をJSONで出力し、`--plan-file` を指定すると保存します。`apply` は `--approve` を指定しない限り何も変更しません。初回設定時や監視フォルダ変更時は、その時点のPDFを基準化して自動処理しません。前回の監視一覧がある場合、停止中に追加されたPDFだけを候補として表示し、確認・実行まで元の名前を維持します。

## ローカル画面

```powershell
.\.venv\Scripts\paper-pdf-renamer-gui.exe
# または
.\.venv\Scripts\python.exe -m paper_pdf_renamer.gui
```

依存なしのローカルWeb画面が `http://127.0.0.1:8766/` で開きます。ポートが使用中の場合は、8767以降の空きポートへ自動的に切り替えます。同じアプリを二重起動した場合は新しい処理プロセスを作らず、すでに開いている画面を表示します。監視フォルダ（複数可）は入力欄に書くほか、`フォルダを選択…` からWindows標準のフォルダー選択画面で追加できます。監視ON/OFF、タイトル最大長、信頼度基準、処理履歴、保留一覧、候補の確認実行、直近のUndoを操作できます。外部公開サーバーではなく、このPCのループバックアドレスだけで待ち受けます。設定は `%APPDATA%\paper-pdf-renamer\settings.json`、履歴は同フォルダの `history` に保存されます。Windows起動時の自動起動は画面のチェックボックスからHKCUだけを使って切り替えます。旧Python版を複数起動したまま更新した場合は、古い論文PDF整理画面をすべて閉じてから最新版を起動してください。

### 画面での操作順

- 新しいPDFを自動処理する場合：`フォルダを選択…` → `設定を保存（必須）` → `新しいPDFを自動監視`をON。監視開始前から存在するPDFは自動変更しません。
- アプリ停止中に追加されたPDFを確認する場合：アプリを起動して監視を再開すると、停止中に追加されたPDFだけが「候補」として表示されます。候補を確認してから`チェックした候補をリネーム`を押してください。初回設定前のPDFは差分を判断できないため、すべて既存扱いになります。
- 既存PDFを整理する場合：`フォルダを選択…` → `設定を保存（必須）` → `既存PDFをスキャン（変更なし）` → 状態が「候補」の行を確認・選択 → `チェックした候補をリネーム`。
- このソフトで過去に変更したPDFの形式を直す場合：新しい`ファイル名形式`を保存 → `履歴から再整理（変更なし）` → 変更前後を確認・選択 → `チェックした候補をリネーム`。履歴に保存済みのDOI・タイトル・著者・年を使うため、PDF本体を外部へ送らず、再解析もしません。
- 「要確認」「失敗」の行は自動では変更しません。元のファイル名を残したまま、理由を確認してください。
- PDFにDOIが直接書かれていない場合も、1ページ目の折り返しタイトルと著者を復元してCrossrefのタイトル検索を試みます。著者の所属番号や`et al.`は照合時に除外します。
- `ファイル名形式`には`{author}`、`{year}`、`{title}`、`{doi}`を使えます。例：`{author} ({year}). - {title}.pdf`。`.pdf`を省略した場合は自動で追加されます。
- 翻訳版と判定されたPDFには、形式にかかわらず拡張子の前に` [ja]`を追加します。日本語原著は通常の日本語著者表記のままで、英語原文と区別するための`[ja]`は翻訳版にだけ付きます。

Python版のデスクトップショートカットは、現在の`.venv`の`pythonw.exe`で画面を起動します。Windows版EXEを使う場合は、上記の`install_windows.ps1`で作成したショートカットを使ってください。

## Python API

`extract_pdf` → `resolve_metadata` → `RenameService.make_candidate` の順で候補を作り、候補の `ready` を確認してから `rename_candidate` を呼びます。一括処理は `BatchScanner.scan` と `execute_approved` を使います。Crossrefクライアントは依存性注入できるため、テストや社内プロキシにも対応できます。

## 現在の範囲と残課題

- ローカル画面、Windows起動時自動起動、複数監視フォルダの設定画面は初版に含まれます。監視はOSの常駐イベントAPIではなく、完成状態を確認する安全側のポーリングです。
- PDF本文抽出はPyMuPDFの品質に依存します。画像PDF（スキャンPDF）はOCRなしでは保留になります。
- Crossref以外の出版社APIや、同一論文の候補を複数提示するUIは後続候補です。
