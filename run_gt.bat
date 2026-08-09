@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [X] Окружение не установлено. Запусти сначала setup_windows.bat
    pause
    exit /b 1
)
set "VPY=%CD%\.venv\Scripts\python.exe"

:menu
cls
echo ============================================================
echo    GT-сегмент Казахстана ^(АЗС + продуктовая розница^)
echo ============================================================
echo.
echo   [1] Все города КЗ: АЗС + продуктовые       ~ часы
echo   [2] Все города КЗ: только АЗС              ~ быстрее
echo   [3] Все города КЗ: только продуктовые
echo   [4] ВСЯ страна сплошняком: АЗС             ~ сутки+
echo   [5] ВСЯ страна сплошняком: АЗС + продукты  ~ несколько суток
echo   [6] Один город ^(спросит какой^)
echo   [0] Выход
echo.
set "CHOICE="
set /p CHOICE=Выбор:

if "%CHOICE%"=="1" goto cities_gt
if "%CHOICE%"=="2" goto cities_azs
if "%CHOICE%"=="3" goto cities_shop
if "%CHOICE%"=="4" goto country_azs
if "%CHOICE%"=="5" goto country_gt
if "%CHOICE%"=="6" goto one_city
if "%CHOICE%"=="0" exit /b 0
goto menu

:cities_gt
set "ARGS=--all-cities --category gt -o kz_gt.xlsx"
goto run

rem Пресеты в .bat зовём латиницей (gt-fuel/gt-grocery) — кириллица в
rem аргументах cmd.exe зависит от кодовой страницы и может побиться.
:cities_azs
set "ARGS=--all-cities --category gt-fuel -o kz_azs.xlsx"
goto run

:cities_shop
set "ARGS=--all-cities --category gt-grocery -o kz_grocery.xlsx"
goto run

:country_azs
set "ARGS=--country --category gt-fuel --step 0.2 -o kz_azs_country.xlsx"
goto run

:country_gt
set "ARGS=--country --category gt --step 0.2 -o kz_gt_country.xlsx"
goto run

:one_city
set "CITY="
set /p CITY=Город (напр. Алматы):
if "%CITY%"=="" goto menu
set "ARGS=--city "%CITY%" --category gt -o "%CITY%_gt.xlsx""
goto run

:run
echo.
echo Запускаю: yandex_parser.py %ARGS%
echo Прогресс сохраняется - Ctrl+C можно жать, повторный запуск продолжит.
echo.
"%VPY%" yandex_parser.py %ARGS% -n 500 --api-intercept
echo.
echo Готово. Файл лежит рядом с этим .bat
pause
