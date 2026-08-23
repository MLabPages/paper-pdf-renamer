param(
    [switch]$SkipDesktop
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\")).Path
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$sourceDir = Join-Path $repoRoot "dist\論文PDFファイル名整理"
$sourceExe = Join-Path $sourceDir "論文PDFファイル名整理.exe"
$installDir = Join-Path $env:LOCALAPPDATA "Programs\PaperPdfRenamer"

if (-not (Test-Path -LiteralPath $sourceExe)) {
    throw "ビルド済みEXEが見つかりません。先に build_windows.ps1 を実行してください。"
}
if (-not (Test-Path -LiteralPath $python)) {
    throw ".venv が見つかりません。"
}

New-Item -ItemType Directory -Force -Path $installDir | Out-Null
Copy-Item -Path (Join-Path $sourceDir "*") -Destination $installDir -Recurse -Force
$installedExe = Join-Path $installDir "論文PDFファイル名整理.exe"

$shortcutArgs = @(
    "-m", "paper_pdf_renamer.cli", "shortcut",
    "--executable", $installedExe,
    "--start-menu"
)
if (-not $SkipDesktop) { $shortcutArgs += "--desktop" }
& $python @shortcutArgs
if ($LASTEXITCODE -ne 0) { throw "スタートメニュー登録に失敗しました。" }

Write-Host "インストール完了: $installedExe"
Write-Host "スタートメニューに「論文PDFファイル名整理」を登録しました。"
