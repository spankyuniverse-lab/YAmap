@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [X] Окружение не установлено. Запусти сначала setup_windows.bat
    pause
    exit /b 1
)

".venv\Scripts\python.exe" yandex_parser.py %*
set "RC=%errorlevel%"
echo.
if not "%RC%"=="0" echo [!] Парсер завершился с кодом %RC%
pause
exit /b %RC%
