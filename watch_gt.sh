#!/usr/bin/env bash
# Сторож. Гоняет сбор в цикле: упал целиком (Chrome умер, интернет отвалился,
# мак ушёл в сон) — поднимает заново. Прогресс сохраняется, перезапуск
# продолжает с места обрыва. Останов: Ctrl+C.
#
#   ./watch_gt.sh                                    # АЗС по всем городам, 2 браузера
#   ./watch_gt.sh --all-cities --category gt-grocery -o kz_grocery.xlsx --workers 2
set -u
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
    echo "[X] Окружение не установлено. Запусти сначала ./setup_mac.sh"
    exit 1
fi

if [ "$#" -gt 0 ]; then
    ARGS=("$@")
else
    ARGS=(--all-cities --category gt-fuel -o kz_azs.xlsx --workers 2 --api-intercept)
fi

MAXTRY=100
TRY=0

# Ctrl+C должен гасить сторожа, а не только текущую попытку.
trap 'echo; echo "Остановлено. Прогресс сохранён — перезапусти ту же команду."; exit 0' INT TERM

while :; do
    TRY=$((TRY + 1))
    echo
    echo "============================================================"
    echo "  Попытка $TRY/$MAXTRY   $(date '+%Y-%m-%d %H:%M:%S')"
    echo "  ${ARGS[*]}"
    echo "============================================================"

    # Код возврата берём сразу после команды: `RC=$?` после `if … fi` вернул бы
    # статус самого if (то есть 0), а не упавшего прогона.
    ./run.sh "${ARGS[@]}"
    RC=$?

    # --new-run заводит НОВУЮ папку прогона. Для сторожа это яд: перезапуск —
    # это докачка, а не новый сбор. Оставь флаг — и каждое падение уводило бы
    # в пустую папку, то есть начинало бы всё с нуля. Снимаем после первой
    # попытки; папку уже завела она.
    for i in "${!ARGS[@]}"; do
        if [ "${ARGS[$i]}" = "--new-run" ]; then
            unset 'ARGS[i]'
            ARGS=("${ARGS[@]}")
            echo "[i] --new-run снят: дальше сторож продолжает тот же прогон."
            break
        fi
    done
    if [ "$RC" -eq 0 ]; then
        echo
        echo "[OK] Сбор завершён штатно."
        break
    fi

    echo
    echo "[!] Процесс упал с кодом $RC. Диагностика:"
    ./run.sh --doctor || true

    if [ "$TRY" -ge "$MAXTRY" ]; then
        echo "[X] Слишком много падений подряд — останавливаюсь."
        break
    fi
    echo
    echo "Перезапуск через 60 секунд... (Ctrl+C чтобы прекратить)"
    sleep 60
done

echo
./run.sh --doctor || true
