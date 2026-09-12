@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ============================================================
echo    YAmap - установка на Windows
echo ============================================================
echo.

rem ---------- 1. Python ----------
set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY (
    where python >nul 2>nul && set "PY=python"
)
if not defined PY (
    echo [X] Python не найден.
    echo     Скачай Python 3.11+ здесь: https://www.python.org/downloads/
    echo     ВАЖНО: при установке поставь галочку "Add python.exe to PATH".
    echo.
    pause
    exit /b 1
)
echo [1/5] Python:
%PY% --version
if errorlevel 1 (
    echo [X] Python сломан или не в PATH. Переустанови с галочкой "Add to PATH".
    pause
    exit /b 1
)

rem ---------- 2. venv ----------
if not exist ".venv\Scripts\python.exe" (
    echo [2/5] Создаю виртуальное окружение .venv ...
    %PY% -m venv .venv
    if errorlevel 1 (
        echo [X] Не удалось создать .venv
        pause
        exit /b 1
    )
) else (
    echo [2/5] Виртуальное окружение .venv уже есть
)

set "VPY=%CD%\.venv\Scripts\python.exe"

rem ---------- 3. зависимости ----------
echo [3/5] Обновляю pip ...
"%VPY%" -m pip install --upgrade pip --quiet
echo [3/5] Ставлю зависимости из requirements.txt ...
"%VPY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [X] Установка зависимостей упала. Проверь интернет/прокси.
    pause
    exit /b 1
)

rem ---------- 4. Chrome ----------
echo [4/5] Проверяю Google Chrome ...
set "CHROME="
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" set "CHROME=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" set "CHROME=%LocalAppData%\Google\Chrome\Application\chrome.exe"

if defined CHROME (
    echo       OK: !CHROME!
) else (
    echo       Системный Chrome не найден - ставлю Chrome для patchright ...
    "%VPY%" -m patchright install chrome
    if errorlevel 1 (
        echo       Не вышло. Поставь обычный Chrome: https://www.google.com/chrome/
        echo       Без настоящего Chrome анти-детект слабее ^(Яндекс чаще даёт капчу^).
    )
)

rem ---------- 5. проверка парсера ----------
echo [5/5] Проверяю парсер ...
"%VPY%" yandex_parser.py --list-cities
if errorlevel 1 (
    echo [X] Парсер не стартует - покажи этот вывод разработчику.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo    Готово. Что запускать дальше:
echo.
echo      run_gt.bat        - меню сбора GT ^(АЗС + продуктовые^)
echo      run.bat ...       - любая команда парсера напрямую
echo.
echo    Примеры для run.bat:
echo      run.bat --city Алматы --category gt
echo      run.bat --all-cities --category gt -o kz_gt.xlsx
echo      run.bat --country --category gt-fuel -o kz_azs.xlsx
echo ============================================================
echo.
pause
