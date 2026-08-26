@echo off
chcp 65001 >nul
title 論文PDFファイル名整理 インストール
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_windows.ps1"
if errorlevel 1 (
  echo.
  echo インストールに失敗しました。上のエラー内容を確認してください。
  pause
  exit /b 1
)
echo.
echo インストールが完了しました。
pause
