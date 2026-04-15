# YAmap — Парсер Яндекс.Карт

Парсер организаций с Яндекс.Карт с поддержкой динамической подгрузки (infinite scroll) и парсинга по категориям (аналог 2ГИС).

## Установка

```bash
pip install -r requirements.txt
playwright install chromium
```

Для лучшего обхода антибот-защиты рекомендуется `patchright` (ставится через `requirements.txt`).

## Быстрый старт

```bash
# Интерактивное меню (без аргументов)
python -m yandex_parser

# По запросу
python -m yandex_parser "кофейни Москва"

# По категориям
python -m yandex_parser --city Москва --category еда
```

## Использование

### По поисковому запросу

```bash
python -m yandex_parser "кофейни Москва"
python -m yandex_parser "автосервис Казань" -n 200 -o авто.csv
python -m yandex_parser "аптеки Москва" --api-intercept
python -m yandex_parser "рестораны СПб" --detail
```

### По категориям (аналог 2ГИС)

```bash
# Каталог категорий
python -m yandex_parser --list-categories

# Группа «еда» (рестораны, кафе, бары, пиццерии, ...)
python -m yandex_parser --city Москва --category еда

# Конкретные категории
python -m yandex_parser --city Москва --category рестораны кафе бары

# Несколько групп
python -m yandex_parser --city СПб --category авто красота

# Все категории каталога
python -m yandex_parser --city Казань --all-categories -n 100
```

Результат по категориям в Excel: лист «Все результаты» + отдельный лист на каждую категорию.

### Прокси

```bash
# Один прокси
python -m yandex_parser "аптеки Москва" --proxy http://user:pass@host:port

# Файл с прокси (ротация round-robin, авто-переключение при капче)
python -m yandex_parser --city Москва --category еда --proxy-file proxies.txt
```

### Продолжение прерванного сбора

```bash
python -m yandex_parser --city Москва --all-categories --resume Москва_categories.xlsx
```

### Docker

```bash
docker compose build
docker compose run parser "кофейни Москва" -o results/кофейни.xlsx
docker compose run parser --city Москва --category еда -o results/еда.xlsx
```

## Параметры

| Параметр | Описание |
|---|---|
| `query` | Поисковый запрос |
| `--city` | Город для парсинга по категориям |
| `--category` | Категории или группы (еда, авто, ...) |
| `--all-categories` | Все категории каталога |
| `--list-categories` | Показать каталог категорий |
| `-n, --max-results` | Максимум организаций (по умолчанию 500) |
| `-o, --output` | Файл: `.xlsx`, `.csv` или `.json` |
| `--api-intercept` | Перехват JSON из API (надёжнее DOM) |
| `--detail` | Открывать карточки для телефона/сайта |
| `--no-headless` | Показывать браузер |
| `--proxy` | Прокси-сервер |
| `--proxy-file` | Файл со списком прокси |
| `--resume` | Продолжить прерванный сбор |
| `--config` | Конфиг-файл (JSON/YAML) |
| `--detect-selectors` | Автодетект CSS-селекторов |
| `--show-selectors` | Показать активные селекторы |
| `--reset-selectors` | Сбросить кеш селекторов |

## Собираемые данные

| Поле | Источник |
|---|---|
| Название | DOM / API |
| Адрес | DOM / API |
| Телефон | `--detail` / `--api-intercept` |
| Сайт | `--detail` / `--api-intercept` |
| Email | `--detail` / `--api-intercept` |
| Соцсети (VK, Telegram, ...) | `--detail` / `--api-intercept` |
| Рейтинг | DOM / API |
| Кол-во отзывов | DOM / API |
| Категория | DOM / API |
| Часы работы | DOM / API |
| Координаты (lat/lon) | `--detail` / `--api-intercept` |

## Архитектура

```
yandex_parser/
  __init__.py     — публичный API
  __main__.py     — python -m yandex_parser
  models.py       — Organization, дедупликация, валидация
  config.py       — категории, конфигурация, ResumeManager
  selectors.py    — CSS-селекторы, автодетект, SelectorEngine
  browser.py      — браузер, прокси, капча, троттлинг, warmup
  scraper.py      — скролл, парсинг, API-перехват, обогащение
  export.py       — CSV, JSON, XLSX
  core.py         — run_parser, run_category_parser
  cli.py          — CLI, интерактивное меню
tests/
  test_models.py  — тесты моделей и дедупликации
  test_config.py  — тесты категорий и ResumeManager
  test_export.py  — тесты экспорта
  test_scraper.py — тесты парсинга API-ответов
```

## Анти-детект

- **Patchright** — drop-in замена Playwright без `Runtime.enable` CDP-утечки
- **Настоящий Chrome** (`channel="chrome"`) — не Chromium Playwright
- **Persistent profile** — cookies/localStorage между запусками
- **Bezier-мышь** — движения по кривой, не прямые линии
- **Human-like скролл** — микро-шаги, случайные паузы, иногда скролл вверх
- **Адаптивный троттлинг** — замедление при капчах, ускорение при успехах
- **Ротация прокси** — автопереключение при серии капч

## Тесты

```bash
pip install pytest
pytest tests/ -v
```

## Каталог категорий

| Группа | Подкатегории |
|---|---|
| **еда** | рестораны, кафе, бары, пиццерии, суши-бары, кофейни, ... |
| **продукты** | супермаркеты, мясные, рыбные, овощи и фрукты, ... |
| **здоровье** | аптеки, больницы, стоматологии, медцентры, ... |
| **авто** | автосервисы, шиномонтаж, автомойки, АЗС, ... |
| **красота** | салоны красоты, парикмахерские, барбершопы, ... |
| **покупки** | ТЦ, одежда, обувь, электроника, мебель, ... |
| **услуги** | банки, нотариусы, юристы, химчистки, ... |
| **образование** | школы, университеты, курсы, автошколы, ... |
| **спорт** | фитнес, бассейны, йога, танцы, ... |
| **развлечения** | кинотеатры, театры, музеи, квесты, ... |
| **туризм** | гостиницы, хостелы, турагентства, ... |
| **транспорт** | такси, каршеринг, грузоперевозки, ... |
| **недвижимость** | агентства, новостройки, УК, ... |
| **дом** | сантехника, электрика, кухни, потолки, ... |
| **IT** | ремонт ПК, IT-компании, провайдеры, ... |
