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

  [1] Все города КЗ: АЗС + продуктовые       ~ сутки
  [2] Все города КЗ: только АЗС              ~ 4-5 часов
  [3] Все города КЗ: только продуктовые      ~ ночь
  [4] ВСЯ страна сплошняком: АЗС             ~ сутки+
  [5] ВСЯ страна сплошняком: АЗС + продукты  ~ несколько суток
  [6] Один город (спросит какой)
  [0] Выход

MENU

read -r -p "Выбор: " CHOICE
case "$CHOICE" in
    1) ARGS=(--all-cities --category gt         -o kz_gt.xlsx) ;;
    2) ARGS=(--all-cities --category gt-fuel    -o kz_azs.xlsx) ;;
    3) ARGS=(--all-cities --category gt-grocery -o kz_grocery.xlsx) ;;
    4) ARGS=(--country --category gt-fuel --step 0.2 -o kz_azs_country.xlsx) ;;
    5) ARGS=(--country --category gt      --step 0.2 -o kz_gt_country.xlsx) ;;
    6) read -r -p "Город (напр. Алматы): " CITY
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
