# Развёртывание YAmap на сервере (долгий сбор по всему Казахстану)

Гайд для запуска сплошного сбора АЗС (`--country`) на Linux-сервере (VPS),
чтобы не держать процесс на ноутбуке 2–3 суток.

## ⚠️ Главное №0: headless палится — нужен Xvfb + настоящий Chrome + WebGL

Яндекс блокирует не по одному флагу, а по **противоречиям сигналов**. На сервере
провальны сразу три вещи, и лечить надо все:

1. **headless** (особенно `chromium-headless-shell`) — палится мгновенно (UA
   `HeadlessChrome`, нет `window.chrome`/plugins). → Запускать **headful под
   Xvfb** настоящим `google-chrome` (не chromium). Парсер это делает
   автоматически: на headless-Linux он сам поднимает Xvfb (если стоит `xvfb` +
   `pyvirtualdisplay`) и идёт headful. Либо запускай через `xvfb-run`.
2. **WebGL software-renderer** — GPU-less сервер отдаёт `SwiftShader`, это
   мгновенный флаг. → Ставим **mesa (llvmpipe)** и парсер добавляет
   `--use-angle=gl` — рендерер становится правдоподобнее. (JS-спуф под patchright
   не работает — он изолирован; чиним на уровне браузера.)
3. **Датацентр-IP** — пенализируется. → У тебя **мобильный/резидентский KZ-IP**
   (напр. через Jusan Mobile) — это самый доверенный сигнал, он закрывает вопрос.

Ставим mesa (для WebGL) заранее:
```bash
sudo apt install -y xvfb mesa-utils libgl1-mesa-dri
pip install pyvirtualdisplay
```

Дальше — либо парсер сам поднимет Xvfb, либо запускай так:
```bash
xvfb-run -a --server-args='-screen 0 1920x1080x24' \
  python yandex_parser.py "АЗС" --country --step 0.15 --no-headless -o kz_azs.xlsx
```

## ⚠️ Главное: капча на сервере

Сервер обычно = **датацентр-IP** + **headless** (без экрана). Яндекс банит
датацентр-IP капчей, а на headless-сервере её **некому решить руками**.
Без решения этого вопроса сбор встанет почти сразу. Варианты (по убыванию
надёжности):

1. **Казахстанский резидентский / мобильный прокси** (лучший вариант). С таким
   IP капча почти не появляется, и `yandex.kz` отдаёт правильный KZ-регион.
   Запуск headless, без ручного вмешательства:
   ```bash
   python3 yandex_parser.py "АЗС" --country --step 0.15 \
       --proxy-file kz_proxies.txt -o kz_azs.xlsx
   ```
   (`kz_proxies.txt` — по одному прокси на строку, `http://user:pass@host:port`).

2. **Сервер, физически размещённый в Казахстане** (KZ-хостинг). IP казахстанский —
   капчи меньше, регион верный. Всё равно желательно 1–2 резидентских прокси
   про запас.

3. **VNC + headful** (ручное решение капчи удалённо). Ставишь `x11vnc`+Xvfb,
   подключаешься с ноутбука, решаешь капчу в окне. Костыль, но работает без
   прокси. Запуск с `--no-headless` под Xvfb.

4. **Антикапча-сервис** (2captcha / rucaptcha). Автоматическое решение
   SmartCaptcha по API-ключу (платно). Интеграции в парсере пока нет — скажи,
   если этот путь, добавлю.

> Без пунктов 1–4 headless на голом VPS-IP словит капчу и встанет. Не запускай
> «в лоб» — сначала прокси/KZ-хостинг.

## Рецепт для физического сервера в Казахстане (рекомендуется)

KZ-IP → капчи мало, регион верный, прокси не нужен. Чтобы браузер был
«человеческим» (headful лучше против анти-фрода) и чтобы капчу можно было
решить удалённо — поднимаем виртуальный дисплей Xvfb + VNC.

```bash
sudo apt install -y xvfb x11vnc tmux
tmux new -s yamap
Xvfb :99 -screen 0 1920x1080x24 >/dev/null 2>&1 &
export DISPLAY=:99
x11vnc -display :99 -bg -forever -nopw -localhost -rfbport 5900

# headful под виртуальным экраном, сплошной сбор по стране
python3 yandex_parser.py "АЗС" --country --step 0.15 --no-headless -o kz_azs.xlsx
# отсоединиться от tmux не прерывая процесс: Ctrl+B, затем D
```

Решить капчу с ноутбука (проброс VNC через SSH):
```bash
ssh -L 5900:localhost:5900 user@server     # с локальной машины
# затем VNC-клиент (на Mac: «Экранный доступ») -> localhost:5900 -> решить капчу
```
Профиль запомнит решение — дальше сбор идёт сам.

## Установка (Ubuntu 22.04 / Debian)

```bash
# 1. Система + Python
sudo apt update
sudo apt install -y python3 python3-pip python3-venv git tmux wget

# 2. Google Chrome (настоящий — лучший анти-детект)
wget -q https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt install -y ./google-chrome-stable_current_amd64.deb

# 3. Репозиторий + ветка
git clone -b claude/yandex-maps-kz-parser-s39rcl \
    https://github.com/spankyuniverse-lab/YAmap.git
cd YAmap

# 4. Зависимости Python
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 5. Браузер для Playwright/patchright (если нет системного Chrome —
#    парсер сам откатится на bundled Chromium)
python3 -m patchright install chrome || python3 -m patchright install chromium
```

Если Chrome не ставится (например, headless-образ) — парсер использует bundled
Chromium автоматически, либо задай путь к бинарю:
```bash
export YAMAP_BROWSER_PATH=/usr/bin/chromium-browser   # или свой путь
```

## Запуск в фоне (переживает отключение SSH)

Через `tmux` — процесс живёт после выхода из SSH:

```bash
tmux new -s yamap
source .venv/bin/activate

# headless + KZ-прокси (см. раздел про капчу выше)
python3 yandex_parser.py "АЗС" --country --step 0.15 \
    --proxy-file kz_proxies.txt --cooldown-every 30 -o kz_azs.xlsx

# отсоединиться от tmux, НЕ прерывая процесс: Ctrl+B, затем D
```

Вернуться к сессии позже: `tmux attach -t yamap`.

## Мониторинг

```bash
# живой лог
tail -f logs/yamap_$(date +%Y-%m-%d).log

# сколько собрано и покрытие полей (после первых сохранений)
ls -la kz_azs.xlsx reports/
cat reports/*Казахстан*.md | head -25
```

В логе смотри строки:
- `Свип: обработано N, в очереди M … +K | всего T` — прогресс/накопление;
- `Тайл … плотный … → делю на 4` — адаптив вынимает город целиком;
- `API-обогащение: … с телефоном/ID: X → Y` — наполнение полей.

## Прерывание и докачка

Процесс полностью **resume-able**: результат пишется в `kz_azs.xlsx` каждые
несколько тайлов, прогресс — в `kz_azs.xlsx.progress.json`. При любом обрыве,
перезагрузке, `Ctrl+C` — просто запусти **ту же команду**: продолжит с места,
уже собранное подхватит, готовые тайлы пропустит.

## Параллельно (если есть пул прокси) — быстрее в N раз

Если поддержан шардинг (`--shard i/N`), запусти N процессов, каждый со своим
прокси и своим файлом, затем слей. См. отдельную инструкцию / попроси добавить
`--shard`, если нужно.

## Забрать результат с сервера

```bash
# с локальной машины
scp user@server:~/YAmap/kz_azs.xlsx .
scp user@server:~/YAmap/reports/*Казахстан*.md .
```
