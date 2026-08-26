param(
    [switch]$SkipDesktop
)

$ErrorActionPreference = "Stop"
$repoRoot = $PSScriptRoot
if (-not (Test-Path -LiteralPath (Join-Path $repoRoot "dist\論文PDFファイル名整理"))) {
    $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\")).Path
}
$sourceDir = Join-Path $repoRoot "dist\論文PDFファイル名整理"
$sourceExe = Join-Path $sourceDir "論文PDFファイル名整理.exe"
$installDir = Join-Path $env:LOCALAPPDATA "Programs\PaperPdfRenamer"

if (-not (Test-Path -LiteralPath $sourceExe)) {
    throw "ビルド済みEXEが見つかりません。先に build_windows.ps1 を実行してください。"
}
New-Item -ItemType Directory -Force -Path $installDir | Out-Null
Copy-Item -Path (Join-Path $sourceDir "*") -Destination $installDir -Recurse -Force
$installedExe = Join-Path $installDir "論文PDFファイル名整理.exe"

$shell = New-Object -ComObject WScript.Shell
$shortcutPaths = @(
    (Join-Path ([Environment]::GetFolderPath("Programs")) "論文PDFファイル名整理.lnk")
)
if (-not $SkipDesktop) {
    $shortcutPaths += Join-Path ([Environment]::GetFolderPath("Desktop")) "論文PDFファイル名整理.lnk"
}
foreach ($shortcutPath in $shortcutPaths) {
    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $installedExe
    $shortcut.WorkingDirectory = $installDir
    $shortcut.IconLocation = "$installedExe,0"
    $shortcut.Save()
}

# 既存設定が自動起動ONなら、Windows起動時もPython版ではなく今回のEXEを使う。
$settingsPath = Join-Path $env:APPDATA "paper-pdf-renamer\settings.json"
if (Test-Path -LiteralPath $settingsPath) {
    $settings = Get-Content -LiteralPath $settingsPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    New-Item -Path $runKey -Force | Out-Null
    if ($settings.auto_start -eq $true) {
        Set-ItemProperty -Path $runKey -Name "PaperPdfRenamer" -Value ('"{0}"' -f $installedExe)
    } else {
        Remove-ItemProperty -Path $runKey -Name "PaperPdfRenamer" -ErrorAction SilentlyContinue
    }
}

Write-Host "インストール完了: $installedExe"
Write-Host "スタートメニューに「論文PDFファイル名整理」を登録しました。"
