#!/usr/bin/env bash
# Установка на macOS (работает и на Linux). Запуск: ./setup_mac.sh
set -u
cd "$(dirname "$0")"

echo "============================================================"
echo "   YAmap — установка (macOS)"
echo "============================================================"
echo

# ---------- 1. Python 3.10+ ----------
# Системный python3 в macOS — 3.9, а парсеру нужен 3.10+. Поэтому ищем
# самый свежий доступный интерпретатор, а не первый попавшийся.
PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
        if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
            PY="$cand"
            break
        fi
    fi
done

if [ -z "$PY" ]; then
    echo "[X] Нужен Python 3.10 или новее."
    if [ "$(uname)" = "Darwin" ]; then
        echo "    Системный python3 в macOS слишком старый. Поставь свежий:"
        echo "      brew install python@3.12"
        echo "    Нет Homebrew — возьми установщик с https://www.python.org/downloads/macos/"
    else
        echo "      sudo apt install -y python3.12 python3.12-venv"
    fi
    exit 1
fi
echo "[1/5] Python: $("$PY" --version) ($(command -v "$PY"))"

# ---------- 2. venv ----------
if [ ! -x ".venv/bin/python" ]; then
    echo "[2/5] Создаю виртуальное окружение .venv ..."
    "$PY" -m venv .venv || { echo "[X] venv не создался"; exit 1; }
else
    echo "[2/5] Виртуальное окружение .venv уже есть"
fi
VPY="$PWD/.venv/bin/python"

# ---------- 3. зависимости ----------
echo "[3/5] Ставлю зависимости ..."
"$VPY" -m pip install --upgrade pip --quiet
"$VPY" -m pip install -r requirements.txt || { echo "[X] Установка упала"; exit 1; }

# ---------- 4. Chrome ----------
echo "[4/5] Проверяю Google Chrome ..."
CHROME=""
for cand in \
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    "$HOME/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
    "/usr/bin/google-chrome" "/usr/bin/google-chrome-stable"; do
    [ -x "$cand" ] && { CHROME="$cand"; break; }
done

if [ -n "$CHROME" ]; then
    echo "      OK: $CHROME"
else
    echo "      Системный Chrome не найден — ставлю Chrome для patchright ..."
    "$VPY" -m patchright install chrome || {
        echo "      Не вышло. Поставь обычный Chrome: https://www.google.com/chrome/"
        echo "      Без настоящего Chrome анти-детект слабее (Яндекс чаще даёт капчу)."
    }
fi

# ---------- 5. проверка ----------
echo "[5/5] Проверяю парсер ..."
"$VPY" yandex_parser.py --list-cities >/dev/null || {
    echo "[X] Парсер не стартует"; exit 1; }
chmod +x run.sh run_gt.sh watch_gt.sh 2>/dev/null || true

echo
echo "============================================================"
echo "   Готово. Что запускать дальше:"
echo
echo "     ./run_gt.sh       — меню сбора GT (АЗС + продуктовые)"
echo "     ./watch_gt.sh     — то же, но с авто-перезапуском (для долгих прогонов)"
echo "     ./run.sh ...      — любая команда парсера напрямую"
echo
echo "   Примеры:"
echo "     ./run.sh --all-cities --category gt-fuel -o kz_azs.xlsx --workers 2"
echo "     ./run.sh --doctor"
echo "============================================================"
