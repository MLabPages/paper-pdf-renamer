param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\")).Path
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$buildRoot = Join-Path $repoRoot "packaging\windows"
$icon = Join-Path $buildRoot "paper-pdf-renamer.ico"
$entrypoint = Join-Path $buildRoot "entrypoint.py"
$versionInfo = Join-Path $buildRoot "version_info.txt"

if (-not (Test-Path -LiteralPath $python)) {
    throw ".venv が見つかりません。READMEのセットアップ手順を先に実行してください。"
}

if (-not $SkipInstall) {
    $projectSpec = $repoRoot + "[windows,pdf]"
    & $python -m pip install -e $projectSpec
    if ($LASTEXITCODE -ne 0) { throw "Windows用依存関係のインストールに失敗しました。" }
}

& $python (Join-Path $buildRoot "create_icon.py") --output $icon
if ($LASTEXITCODE -ne 0) { throw "アプリアイコンの生成に失敗しました。" }

$distPath = Join-Path $repoRoot "dist"
$workPath = Join-Path $repoRoot ".pyinstaller-build"
$specPath = Join-Path $buildRoot "spec"
New-Item -ItemType Directory -Force -Path $distPath, $workPath, $specPath | Out-Null

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onedir `
    --name "論文PDFファイル名整理" `
    --icon $icon `
    --version-file $versionInfo `
    --paths $repoRoot `
    --collect-all fitz `
    --collect-all pystray `
    --distpath $distPath `
    --workpath $workPath `
    --specpath $specPath `
    $entrypoint
if ($LASTEXITCODE -ne 0) { throw "Windowsアプリのビルドに失敗しました。" }

$exe = Join-Path $distPath "論文PDFファイル名整理\論文PDFファイル名整理.exe"
if (-not (Test-Path -LiteralPath $exe)) { throw "EXEが生成されませんでした: $exe" }
Write-Host "ビルド完了: $exe"
Write-Host "次に install_windows.ps1 を実行すると、スタートメニューへ登録できます。"
