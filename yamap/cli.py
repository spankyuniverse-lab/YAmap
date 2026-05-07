"""CLI entry point + интерактивное меню."""

from __future__ import annotations

import argparse
import logging
import re
import sys
import urllib.parse
from pathlib import Path

from ._playwright import sync_playwright
from .browser import create_browser_context, setup_page
from .categories import CATEGORIES, list_categories
from .config import load_config
from .logging_utils import setup_logging
from .proxy import ProxyRotator, set_proxy_rotator
from .runner import run_category_parser, run_parser
from .selectors import (
    ITEM_SEL,
    LINK_SEL,
    SELECTORS_CACHE_FILE,
    SELECTOR_KEYS,
    SelectorCache,
    _HARDCODED,
    _probe_selector,
    detect_selectors,
    get_selector_engine,
)


# Базовая консольная настройка применяется сразу для импортных сообщений;
# `setup_logging()` потом перенастраивает с учётом --log-file.
setup_logging()
log = logging.getLogger("yandex_parser")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Парсер организаций с Яндекс.Карт — по запросу или по категориям (аналог 2ГИС)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Примеры:
  # По запросу
  python yandex_parser.py "кофейни Москва"
  python yandex_parser.py "автосервис Казань" -n 200 -o авто.xlsx

  # По категориям
  python yandex_parser.py --city Москва --category еда
  python yandex_parser.py --city СПб --category авто красота -n 100
  python yandex_parser.py --city Казань --all-categories

  # С прокси
  python yandex_parser.py "аптеки Москва" --proxy http://user:pass@host:port
  python yandex_parser.py --city Москва --category еда --proxy-file proxies.txt

  # Продолжить прерванный сбор
  python yandex_parser.py --city Москва --all-categories --resume Москва_categories.xlsx

  # Экспорт в JSON
  python yandex_parser.py "рестораны Москва" -o results.json

  # С конфиг-файлом
  python yandex_parser.py --config config.yaml

  # Автодетект селекторов / список категорий
  python yandex_parser.py --detect-selectors --no-headless
  python yandex_parser.py --list-categories
""",
    )
    parser.add_argument("query", nargs="?", default=None,
                        help='Поисковый запрос, напр. "кофейни Москва"')
    parser.add_argument("--list-categories", action="store_true",
                        help="Показать каталог категорий и выйти")
    parser.add_argument("--city", type=str, default=None,
                        help="Город для парсинга по категориям")
    parser.add_argument("--category", nargs="+", default=None,
                        help="Категории или группы категорий (напр. еда рестораны кафе)")
    parser.add_argument("--all-categories", action="store_true",
                        help="Парсить все категории из каталога")
    parser.add_argument("--max-results", "-n", type=int, default=500,
                        help="Максимум организаций на запрос/категорию (по умолчанию 500)")
    parser.add_argument("--output", "-o", default=None,
                        help="Файл результатов: .xlsx, .csv или .json (по умолчанию results.xlsx / categories.xlsx)")
    parser.add_argument("--no-headless", action="store_true",
                        help="Показывать браузер (для отладки)")
    parser.add_argument("--detail", action="store_true",
                        help="Открывать карточку каждой организации для телефона/сайта")
    parser.add_argument("--scroll-pause", type=float, default=1.0,
                        help="Пауза между прокрутками в секундах (по умолчанию 1.0)")
    parser.add_argument("--api-intercept", action="store_true",
                        help="Перехватывать JSON из внутреннего API вместо парсинга DOM")
    parser.add_argument("--proxy", type=str, default=None,
                        help="Прокси-сервер (http://host:port или socks5://user:pass@host:port)")
    parser.add_argument("--proxy-file", type=str, default=None,
                        help="Файл со списком прокси (одна строка = один прокси, ротация round-robin)")
    parser.add_argument("--resume", type=str, default=None,
                        help="Путь к файлу с предыдущими результатами для продолжения сбора")
    parser.add_argument("--config", type=str, default=None,
                        help="Конфиг-файл (JSON или YAML) с параметрами парсера")
    parser.add_argument("--detect-selectors", action="store_true",
                        help="Открыть Я.Карты, автоматически обнаружить актуальные CSS-селекторы и сохранить в selectors_cache.json")
    parser.add_argument("--show-selectors", action="store_true",
                        help="Показать текущие активные селекторы (из кеша или hardcoded)")
    parser.add_argument("--reset-selectors", action="store_true",
                        help="Удалить кеш селекторов и вернуться к hardcoded")
    parser.add_argument("--log-file", type=str, default=None,
                        help="Путь к лог-файлу. Полный DEBUG-лог с ротацией 5MB × 3 копии.")

    args = parser.parse_args()

    if args.log_file:
        setup_logging(log_file=args.log_file)

    if args.show_selectors:
        engine = get_selector_engine()
        cache = engine.cache.all()
        print("\nАктивные CSS-селекторы:\n")
        for key in SELECTOR_KEYS:
            cached = cache.get(key)
            hardcoded = _HARDCODED.get(key, "")
            if cached:
                src = "cache"
                sel = cached
            else:
                src = "hardcoded"
                sel = hardcoded
            print(f"  {key:25s} [{src:9s}]  {sel}")
        print(f"\nФайл кеша: {SELECTORS_CACHE_FILE}")
        if SELECTORS_CACHE_FILE.exists():
            print(f"  (существует, {SELECTORS_CACHE_FILE.stat().st_size} байт)")
        else:
            print("  (не существует — будет создан при первом запуске)")
        return

    if args.reset_selectors:
        if SELECTORS_CACHE_FILE.exists():
            SELECTORS_CACHE_FILE.unlink()
            print("Кеш селекторов удалён. Будут использоваться hardcoded-селекторы.")
        else:
            print("Кеш селекторов не найден.")
        return

    if args.list_categories:
        list_categories()
        return

    if args.detect_selectors:
        _run_detect_selectors(args)
        return

    headless = not args.no_headless

    if args.config:
        cfg = load_config(args.config)
        for k, v in cfg.items():
            arg_key = k.replace("-", "_")
            if hasattr(args, arg_key) and getattr(args, arg_key) is None:
                setattr(args, arg_key, v)

    proxy_url = None
    if args.proxy:
        proxy_url = args.proxy
    elif args.proxy_file:
        rotator = ProxyRotator.from_file(args.proxy_file)
        set_proxy_rotator(rotator)
        proxy_url = rotator.next()

    resume_path = Path(args.resume) if args.resume else None

    # Режим: парсинг по категориям
    if args.city and (args.category or args.all_categories):
        if args.all_categories:
            cats = list(CATEGORIES.keys())
        else:
            cats = args.category

        output = args.output or f"{args.city}_categories.xlsx"

        results = run_category_parser(
            city=args.city,
            categories=cats,
            max_results_per_category=args.max_results,
            output=output,
            headless=headless,
            detail=args.detail,
            scroll_pause=args.scroll_pause,
            api_intercept=args.api_intercept,
            proxy_url=proxy_url,
            resume_path=resume_path,
        )

        total = sum(len(v) for v in results.values())
        print(f"\nГотово! Собрано {total} организаций по {len(results)} категориям -> {output}")
        return

    # Режим: обычный поиск по запросу
    if not args.query:
        # Нет аргументов → запускаем интерактивное меню
        interactive_menu()
        return

    output = args.output or "results.xlsx"
    orgs = run_parser(
        query=args.query,
        max_results=args.max_results,
        output=output,
        headless=headless,
        detail=args.detail,
        scroll_pause=args.scroll_pause,
        api_intercept=args.api_intercept,
        proxy_url=proxy_url,
        resume_path=resume_path,
    )

    print(f"\nГотово! Собрано {len(orgs)} организаций -> {output}")


def _run_detect_selectors(args) -> None:
    """Принудительная детекция селекторов на живой странице."""
    test_query = args.query or "кофейни Москва"
    print(f"\nЗапуск автодетекта селекторов (запрос: «{test_query}»)…\n")

    if SELECTORS_CACHE_FILE.exists():
        SELECTORS_CACHE_FILE.unlink()

    with sync_playwright() as pw:
        _, ctx = create_browser_context(pw, headless=not args.no_headless)
        page = setup_page(ctx)

        encoded = urllib.parse.quote(test_query)
        page.goto(
            f"https://yandex.ru/maps/?text={encoded}",
            wait_until="domcontentloaded", timeout=30000,
        )
        page.wait_for_timeout(4000)

        print("--- Детекция селекторов списка результатов ---")
        list_sels = detect_selectors(page, mode="list")
        for k, v in list_sels.items():
            works = _probe_selector(page, v)
            status = "OK" if works else "??"
            print(f"  [{status}] {k:25s} → {v}")

        print("\n--- Проверка hardcoded-селекторов ---")
        for k, v in _HARDCODED.items():
            if k.startswith("detail_"):
                continue
            works = _probe_selector(page, v)
            status = "OK" if works else "FAIL"
            print(f"  [{status}] {k:25s} → {v}")

        item_sel = list_sels.get("item") or ITEM_SEL
        link_sel = list_sels.get("link") or LINK_SEL
        card = page.locator(item_sel).first
        if card.count() > 0:
            link = card.locator(link_sel).first
            href = ""
            if link.count() > 0:
                href = link.get_attribute("href") or ""
            if href:
                if href.startswith("/"):
                    href = f"https://yandex.ru{href}"
                print(f"\n--- Детекция селекторов карточки ({href[:60]}…) ---")
                page.goto(href, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(3000)

                detail_sels = detect_selectors(page, mode="detail")
                for k, v in detail_sels.items():
                    works = _probe_selector(page, v)
                    status = "OK" if works else "??"
                    print(f"  [{status}] {k:25s} → {v}")

                print("\n--- Проверка hardcoded detail-селекторов ---")
                for k, v in _HARDCODED.items():
                    if not k.startswith("detail_"):
                        continue
                    works = _probe_selector(page, v)
                    status = "OK" if works else "FAIL"
                    print(f"  [{status}] {k:25s} → {v}")

                list_sels.update(detail_sels)

        ctx.close()

    if list_sels:
        cache = SelectorCache()
        cache.update(list_sels)
        cache.save()
        print(f"\nСохранено {len(list_sels)} селекторов → {SELECTORS_CACHE_FILE}")
    else:
        print("\nАвтодетект не нашёл селекторов.")


# ---------------------------------------------------------------------------
# Интерактивное меню (для PyCharm / запуска без аргументов)
# ---------------------------------------------------------------------------

def _input_choice(prompt: str, options: list[str], allow_empty: bool = False) -> str:
    """Показать пронумерованный список и запросить выбор."""
    for i, opt in enumerate(options, 1):
        print(f"  {i}. {opt}")
    while True:
        raw = input(f"\n{prompt} ").strip()
        if allow_empty and raw == "":
            return ""
        if raw.isdigit():
            idx = int(raw) - 1
            if 0 <= idx < len(options):
                return options[idx]
        print("  Неверный номер, попробуйте снова.")


def _input_yn(prompt: str, default: bool = False) -> bool:
    """Запросить да/нет."""
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        raw = input(f"{prompt} {suffix}: ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes", "д", "да"):
            return True
        if raw in ("n", "no", "н", "нет"):
            return False


def interactive_menu() -> None:
    """Пошаговое интерактивное меню — запускается при старте без аргументов."""
    print()
    print("=" * 55)
    print("  YAmap — Парсер Яндекс.Карт")
    print("=" * 55)
    print()

    print("Выберите режим:")
    mode = _input_choice(
        "Номер:",
        [
            "Поиск по запросу",
            "Парсинг по категориям (как 2ГИС)",
            "Все категории города",
            "Показать список категорий",
            "Автодетект селекторов",
        ],
    )

    if mode == "Показать список категорий":
        list_categories()
        return

    if mode == "Автодетект селекторов":
        print("\nЗапускаю автодетект селекторов…")
        sys.argv = [sys.argv[0], "--detect-selectors", "--no-headless"]
        main()
        return

    query = None
    city = None
    categories_input: list[str] = []

    if mode == "Поиск по запросу":
        query = input("\nПоисковый запрос (напр. кофейни Москва): ").strip()
        if not query:
            print("Запрос не может быть пустым!")
            return

    elif mode in ("Парсинг по категориям (как 2ГИС)", "Все категории города"):
        city = input("\nГород: ").strip()
        if not city:
            print("Город не может быть пустым!")
            return

        if mode == "Парсинг по категориям (как 2ГИС)":
            print("\nДоступные группы категорий:")
            groups = list(CATEGORIES.keys())
            for i, g in enumerate(groups, 1):
                count = len(CATEGORIES[g])
                examples = ", ".join(CATEGORIES[g][:3])
                print(f"  {i:2d}. {g:15s} ({count} шт: {examples}…)")

            print(f"\nВведите номера через пробел (напр. 1 3 5)")
            print(f"Или названия: еда авто красота")
            raw = input("\nКатегории: ").strip()
            if not raw:
                print("Категории не выбраны!")
                return

            parts = raw.split()
            for p in parts:
                if p.isdigit():
                    idx = int(p) - 1
                    if 0 <= idx < len(groups):
                        categories_input.append(groups[idx])
                else:
                    categories_input.append(p)
        else:
            categories_input = list(CATEGORIES.keys())
            print(f"\nБудут спарсены ВСЕ {len(categories_input)} групп категорий")

    raw_max = input("\nМаксимум организаций на запрос [500]: ").strip()
    max_results = int(raw_max) if raw_max.isdigit() else 500

    print("\nФормат сохранения:")
    fmt = _input_choice("Номер:", ["Excel (.xlsx)", "CSV (.csv)", "JSON (.json)"])
    ext_map = {"Excel (.xlsx)": ".xlsx", "CSV (.csv)": ".csv", "JSON (.json)": ".json"}
    ext = ext_map[fmt]

    if city:
        default_name = f"{city}_categories{ext}"
    elif query:
        safe_name = re.sub(r'[^\w\s-]', '', query)[:30].strip().replace(' ', '_')
        default_name = f"{safe_name}{ext}"
    else:
        default_name = f"results{ext}"

    raw_output = input(f"\nИмя файла [{default_name}]: ").strip()
    output = raw_output if raw_output else default_name

    print("\n--- Какие данные собирать? ---")
    print("  1. Базовые (название, адрес, рейтинг, категория) — быстро")
    print("  2. Полные (+ телефон, сайт, email, соцсети, координаты) — через карточки, медленнее")
    print("  3. Полные через API (+ телефон, сайт, координаты) — перехват JSON, надёжнее")
    data_mode = input("\nНомер [3]: ").strip() or "3"

    if data_mode == "1":
        detail = False
        api_intercept = False
    elif data_mode == "2":
        detail = True
        api_intercept = False
    else:
        detail = False
        api_intercept = True

    print("\n--- Дополнительные настройки ---")
    headless = not _input_yn("Показывать браузер? (рекомендуется для решения капчи)", True)

    proxy_url = None
    if _input_yn("Использовать прокси?", False):
        print("\n  1. Один прокси (ввести вручную)")
        print("  2. Файл с прокси-списком")
        proxy_mode = input("\nНомер [1]: ").strip()
        if proxy_mode == "2":
            proxy_file = input("Путь к файлу с прокси: ").strip()
            if proxy_file:
                rotator = ProxyRotator.from_file(proxy_file)
                set_proxy_rotator(rotator)
                proxy_url = rotator.next()
        else:
            proxy_url = input("Прокси (http://host:port): ").strip() or None

    resume_path = None
    existing_file = Path(output)
    if existing_file.exists():
        if _input_yn(f"Файл {output} уже существует. Продолжить сбор (resume)?", True):
            resume_path = existing_file

    print("\n" + "=" * 55)
    print("  Параметры запуска:")
    print("=" * 55)
    if query:
        print(f"  Режим:       поиск по запросу")
        print(f"  Запрос:      {query}")
    else:
        print(f"  Режим:       по категориям")
        print(f"  Город:       {city}")
        print(f"  Категории:   {', '.join(categories_input)}")
    print(f"  Макс. орг:   {max_results}")
    print(f"  Файл:        {output}")
    print(f"  Detail:      {'да' if detail else 'нет'}")
    print(f"  API-перехват: {'да' if api_intercept else 'нет'}")
    print(f"  Браузер:     {'видимый' if not headless else 'скрытый'}")
    print(f"  Прокси:      {proxy_url or 'нет'}")
    print(f"  Резюме:      {resume_path or 'нет'}")
    print("=" * 55)

    if not _input_yn("\nЗапустить?", True):
        print("Отменено.")
        return

    print()

    if query:
        orgs = run_parser(
            query=query,
            max_results=max_results,
            output=output,
            headless=headless,
            detail=detail,
            scroll_pause=1.0,
            api_intercept=api_intercept,
            proxy_url=proxy_url,
            resume_path=resume_path,
        )
        print(f"\nГотово! Собрано {len(orgs)} организаций -> {output}")
    else:
        results = run_category_parser(
            city=city,
            categories=categories_input,
            max_results_per_category=max_results,
            output=output,
            headless=headless,
            detail=detail,
            scroll_pause=1.0,
            api_intercept=api_intercept,
            proxy_url=proxy_url,
            resume_path=resume_path,
        )
        total = sum(len(v) for v in results.values())
        print(f"\nГотово! Собрано {total} организаций по {len(results)} категориям -> {output}")
