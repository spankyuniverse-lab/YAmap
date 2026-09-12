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
echo   [1] 19 городов КЗ: весь GT                 ~ 2,5-3 часа
echo   [2] 19 городов КЗ: только АЗС              ~ 20-30 минут
echo   [3] 19 городов КЗ: только продуктовые
echo   [4] Все города и посёлки ^(96^): весь GT     ~ 6-9 часов
echo   [5] АУЛЫ И СЁЛА: сельский GT-набор         ~ 12-18 часов
echo   [6] ТРАССЫ: придорожные АЗС/кафе           ~ 8-14 часов
echo   [7] ВСЯ страна сплошняком: сельский набор  ~ несколько суток
echo   [8] ВСЯ страна сплошняком: весь GT         ~ неделя
echo   [9] Один город ^(спросит какой^)
echo   [0] Выход
echo.
set "CHOICE="
set /p CHOICE=Выбор:

if "%CHOICE%"=="1" goto cities_gt
if "%CHOICE%"=="2" goto cities_azs
if "%CHOICE%"=="3" goto cities_shop
if "%CHOICE%"=="4" goto cities_all
if "%CHOICE%"=="5" goto auls
if "%CHOICE%"=="6" goto routes
if "%CHOICE%"=="7" goto country_rural
if "%CHOICE%"=="8" goto country_gt
if "%CHOICE%"=="9" goto one_city
if "%CHOICE%"=="0" exit /b 0
goto menu

:cities_gt
set "ARGS=--all-cities --category gt -o kz_gt.xlsx"
goto run

rem Пресеты и спеки в .bat зовём латиницей (gt-fuel/gt-rural/aul/max) —
rem кириллица в аргументах cmd.exe зависит от кодовой страницы и может побиться.
:cities_azs
set "ARGS=--all-cities --category gt-fuel -o kz_azs.xlsx"
goto run

:cities_shop
set "ARGS=--all-cities --category gt-grocery -o kz_grocery.xlsx"
goto run

:cities_all
set "ARGS=--all-cities --cities all --category gt -o kz_gt.xlsx"
goto run

:auls
set "ARGS=--all-cities --cities aul --category gt-rural -o kz_aul.xlsx"
goto run

:routes
set "ARGS=--routes --category gt-rural -o kz_routes.xlsx"
goto run

:country_rural
set "ARGS=--country --category gt-rural -o kz_country.xlsx"
goto run

:country_gt
set "ARGS=--country --category gt -o kz_country.xlsx"
goto run

:one_city
set "CITY="
set /p CITY=Город (напр. Алматы):
if "%CITY%"=="" goto menu
set "ARGS=--city "%CITY%" --category gt -o "%CITY%_gt.xlsx""
goto run

:run
echo.
echo   Сколько браузеров запустить ОДНОВРЕМЕННО?
echo     1 - безопасно, капчи почти нет
echo     2 - вдвое быстрее, риск капчи небольшой ^(рекомендую^)
echo     3 - втрое быстрее, но капча уже вероятна
echo     4+ - только если интернет и комп тянут, капча очень вероятна
echo   Все браузеры идут с ОДНОГО твоего IP - в этом всё дело.
echo.
set "W="
set /p W=Число браузеров [Enter = 2]:
if "%W%"=="" set "W=2"

echo.
echo Запускаю: yandex_parser.py %ARGS% --workers %W%
echo Прогресс сохраняется - Ctrl+C можно жать, повторный запуск продолжит.
echo.
"%VPY%" yandex_parser.py %ARGS% --workers %W% --api-intercept
echo.
echo Готово. Файл лежит рядом с этим .bat
pause
