@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
cd /d "%~dp0"

rem ===========================================================================
rem  Сторож. Гоняет сбор в цикле: если процесс упал целиком (Chrome умер,
rem  интернет отвалился, комп ушёл в сон) - поднимает заново. Прогресс
rem  сохраняется, так что перезапуск продолжает, а не начинает с нуля.
rem  Останов: Ctrl+C дважды, или закрыть окно.
rem ===========================================================================

if not exist ".venv\Scripts\python.exe" (
    echo [X] Окружение не установлено. Запусти сначала setup_windows.bat
    pause
    exit /b 1
)
set "VPY=%CD%\.venv\Scripts\python.exe"

set "ARGS=%*"
if "%ARGS%"=="" set "ARGS=--all-cities --category gt-fuel -o kz_azs.xlsx --workers 2 --api-intercept"

set /a TRY=0
set /a MAXTRY=100

:loop
set /a TRY+=1
echo.
echo ============================================================
echo   Попытка %TRY%/%MAXTRY%   %DATE% %TIME%
echo   %ARGS%
echo ============================================================
"%VPY%" yandex_parser.py %ARGS%
set "RC=%errorlevel%"

rem --new-run заводит НОВУЮ папку прогона. Для сторожа это яд: перезапуск -
rem это докачка, а не новый сбор. Оставь флаг - и каждое падение уводило бы в
rem пустую папку, то есть начинало бы всё с нуля. Снимаем после первой
rem попытки; папку уже завела она.
echo !ARGS! | find "--new-run" >nul && (
    set "ARGS=!ARGS:--new-run=!"
    echo [i] --new-run снят: дальше сторож продолжает тот же прогон.
)

if "%RC%"=="0" (
    echo.
    echo [OK] Сбор завершён штатно.
    goto done
)

echo.
echo [!] Процесс упал с кодом %RC%. Диагностика:
"%VPY%" yandex_parser.py --doctor
if %TRY% GEQ %MAXTRY% (
    echo [X] Слишком много падений подряд - останавливаюсь.
    goto done
)
echo.
echo Перезапуск через 60 секунд... ^(Ctrl+C чтобы прекратить^)
timeout /t 60 /nobreak >nul
goto loop

:done
echo.
"%VPY%" yandex_parser.py --doctor
echo.
pause
