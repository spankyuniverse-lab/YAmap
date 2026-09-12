#!/usr/bin/env bash
# Запуск парсера с любыми аргументами: ./run.sh --all-cities --category gt -w 2
set -u
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
    echo "[X] Окружение не установлено. Запусти сначала ./setup_mac.sh"
    exit 1
fi

# На ноутбуке долгий прогон убивает засыпание по простою. caffeinate держит
# мак бодрым ровно на время прогона и сам отпускает после выхода.
# -i — не спать по простою, -s — не спать при работе от сети.
#
# Без массивов намеренно: в macOS системный bash — 3.2, и там раскрытие
# ПУСТОГО массива под `set -u` валится с «unbound variable».
if command -v caffeinate >/dev/null 2>&1; then
    exec caffeinate -is ./.venv/bin/python yandex_parser.py "$@"
else
    exec ./.venv/bin/python yandex_parser.py "$@"
fi
