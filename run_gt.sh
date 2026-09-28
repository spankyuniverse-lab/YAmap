#!/usr/bin/env bash
# Меню сбора GT-сегмента (АЗС + продуктовая розница) по Казахстану.
set -u
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
    echo "[X] Окружение не установлено. Запусти сначала ./setup_mac.sh"
    exit 1
fi

cat <<'MENU'
============================================================
   GT-сегмент Казахстана (АЗС + продуктовая розница)
============================================================

  [1] 19 городов: весь GT (4 категории)      ~ 2,5-3 часа
  [2] 19 городов: только заправки            ~ 20-30 минут
  [3] 19 городов: только магазины            ~ 1,5-2 часа
  [4] Все города и посёлки (96): весь GT     ~ 6-9 часов
  [5] АУЛЫ И СЁЛА: сельский GT-набор         ~ 12-18 часов
  [6] ТРАССЫ: придорожные АЗС/кафе/магазины  ~ 8-14 часов
  [7] ВСЯ страна сплошняком: сельский набор  ~ несколько суток
  [8] ВСЯ страна сплошняком: весь GT         ~ неделя
  [9] Один город (спросит какой)
  [0] Выход

MENU

read -r -p "Выбор: " CHOICE
case "$CHOICE" in
    1) ARGS=(--all-cities --category gt         -o kz_gt.xlsx) ;;
    2) ARGS=(--all-cities --category gt-fuel    -o kz_azs.xlsx) ;;
    3) ARGS=(--all-cities --category gt-grocery -o kz_grocery.xlsx) ;;
    4) ARGS=(--all-cities --cities all --category gt -o kz_gt.xlsx) ;;
    5) ARGS=(--all-cities --cities аулы --category gt-село -o kz_aul.xlsx) ;;
    6) ARGS=(--routes --category gt-село -o kz_routes.xlsx) ;;
    7) ARGS=(--country --category gt-село -o kz_country.xlsx) ;;
    8) ARGS=(--country --category gt      -o kz_country.xlsx) ;;
    9) read -r -p "Город (напр. Алматы): " CITY
       [ -z "$CITY" ] && exit 0
       ARGS=(--city "$CITY" --category gt -o "${CITY}_gt.xlsx") ;;
    0) exit 0 ;;
    *) echo "Не понял выбор"; exit 1 ;;
esac

cat <<'WORKERS'

  Сколько браузеров запустить ОДНОВРЕМЕННО?
    1 — безопасно, капчи почти нет
    2 — вдвое быстрее, риск капчи небольшой (рекомендую)
    3 — втрое быстрее, но капча уже вероятна
    4+ — только если интернет тянет, капча очень вероятна
  Все браузеры идут с ОДНОГО твоего IP — в этом всё дело.

WORKERS
read -r -p "Число браузеров [Enter = 2]: " W
W="${W:-2}"

echo
echo "Запускаю: ${ARGS[*]} --workers $W"
echo "Прогресс сохраняется — Ctrl+C можно жать, повторный запуск продолжит."
echo
exec ./run.sh "${ARGS[@]}" --workers "$W" --api-intercept
