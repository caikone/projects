# TAgent build script (PyInstaller)
# Usage: powershell -ExecutionPolicy Bypass -File build_exe.ps1

$ErrorActionPreference = "Stop"
Set-Location "E:\projects\Agent\TAgent"

Write-Host "===== [1/4] Install deps and PyInstaller =====" -ForegroundColor Cyan
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install pyinstaller

Write-Host "===== [2/4] Clean old artifacts =====" -ForegroundColor Cyan
foreach ($d in @("build", "dist", "TAgent.spec")) {
    if (Test-Path $d) { Remove-Item $d -Recurse -Force }
}

Write-Host "===== [3/4] PyInstaller build =====" -ForegroundColor Cyan
python -m PyInstaller --noconfirm --clean --name TAgent --console `
    --collect-all textual --collect-all trafilatura `
    --collect-all tui `
    --exclude-module PyQt5 --exclude-module PyQt6 --exclude-module PySide --exclude-module PySide2 --exclude-module PySide6 `
    --exclude-module tkinter --exclude-module _tkinter `
    run.py

Write-Host "===== [4/4] Copy config.ini into dist =====" -ForegroundColor Cyan
Copy-Item "config.ini" "dist\TAgent\config.ini" -Force

Write-Host ""
Write-Host "DONE. Output dir: E:\projects\Agent\TAgent\dist\TAgent\" -ForegroundColor Green
Write-Host "Next: go into dist\TAgent\ and double-click TAgent.exe to test." -ForegroundColor Yellow
