"""CLI и интерактивное меню."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
import urllib.parse

from pathlib import Path

from . import browser as _browser
from .config import CATEGORIES, list_categories, load_config
from .core import run_category_parser, run_parser
from .selectors import (
    SELECTORS_CACHE_FILE,
    SELECTOR_KEYS,
    SelectorCache,
    _HARDCODED,
    _probe_selector,
    detect_selectors,
    get_selector_engine,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Парсер организаций с Яндекс.Карт",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Примеры:
  python -m yandex_parser "кофейни Москва"
  python -m yandex_parser "кофейни" --region Москва          # по geo_id=213
  python -m yandex_parser "аптеки" --region "Московская область"  # geo_id=1
  python -m yandex_parser "автосервис Казань" -n 200 -o авто.xlsx
  python -m yandex_parser --city Москва --category еда
  python -m yandex_parser --city СПб --category авто красота -n 100
  python -m yandex_parser --city Казань --all-categories
  python -m yandex_parser --city Москва --category еда --proxy-file proxies.txt
  python -m yandex_parser --city Москва --all-categories --resume Москва_categories.xlsx
  python -m yandex_parser --detect-selectors --no-headless
  python -m yandex_parser --list-categories
  python -m yandex_parser --list-regions
""",
    )
    parser.add_argument(
        "query", nargs="?", default=None,
        help='Поисковый запрос, напр. "кофейни Москва"',
    )
    parser.add_argument("--list-categories", action="store_true")
    parser.add_argument("--list-regions", action="store_true",
                        help="Показать список поддерживаемых регионов/городов")
    parser.add_argument("--city", type=str, default=None)
    parser.add_argument(
        "--region", type=str, default=None,
        help='Регион поиска для одиночного запроса, напр. "Москва", '
             '"Московская область", "СПб". Если известен geo_id — '
             'поиск ограничивается границами субъекта/города.',
    )
    parser.add_argument("--category", nargs="+", default=None)
    parser.add_argument("--all-categories", action="store_true")
    parser.add_argument("--max-results", "-n", type=int, default=500)
    parser.add_argument("--output", "-o", default=None)
    parser.add_argument("--no-headless", action="store_true")
    parser.add_argument("--detail", action="store_true")
    parser.add_argument("--scroll-pause", type=float, default=1.0)
    parser.add_argument("--api-intercept", action="store_true")
    parser.add_argument("--proxy", type=str, default=None)
    parser.add_argument("--proxy-file", type=str, default=None)
    parser.add_argument("--resume", type=str, default=None)
    parser.add_argument("--config", type=str, default=None)
    parser.add_argument("--detect-selectors", action="store_true")
    parser.add_argument("--show-selectors", action="store_true")
    parser.add_argument("--reset-selectors", action="store_true")

    args = parser.parse_args()

    # --- Утилитарные режимы ---
    if args.show_selectors:
        engine = get_selector_engine()
        cache = engine._cache.all()
        print("\nАктивные CSS-селекторы:\n")
        for key in SELECTOR_KEYS:
            cached = cache.get(key)
            hardcoded = _HARDCODED.get(key, "")
            src = "cache" if cached else "hardcoded"
            sel = cached or hardcoded
            print(f"  {key:25s} [{src:9s}]  {sel}")
        print(f"\nФайл кеша: {SELECTORS_CACHE_FILE}")
        if SELECTORS_CACHE_FILE.exists():
            print(f"  (существует, {SELECTORS_CACHE_FILE.stat().st_size} байт)")
        else:
            print("  (не существует)")
        return

    if args.reset_selectors:
        if SELECTORS_CACHE_FILE.exists():
            SELECTORS_CACHE_FILE.unlink()
            print("Кеш селекторов удалён.")
        else:
            print("Кеш селекторов не найден.")
        return

    if args.list_categories:
        list_categories()
        return

    if args.list_regions:
        from .geography import list_regions, list_cities
        print("\nПоддерживаемые регионы (субъекты РФ):\n")
        for e in list_regions():
            print(f"  {e.geo_id:>6}  {e.slug:<45} {e.name}")
        print("\nПоддерживаемые города:\n")
        for e in list_cities():
            print(f"  {e.geo_id:>6}  {e.slug:<45} {e.name}")
        print("\nОстальные города ищутся по тексту запроса "
              "(без ограничения по региону).")
        return

    if args.detect_selectors:
        _run_detect_selectors(args)
        return

    # --- Общие параметры ---
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
        rotator = _browser.ProxyRotator.from_file(args.proxy_file)
        _browser._proxy_rotator = rotator
        proxy_url = rotator.next()

    resume_path = Path(args.resume) if args.resume else None

    # --- Режим: по категориям ---
    if args.city and (args.category or args.all_categories):
        cats = list(CATEGORIES.keys()) if args.all_categories else args.category
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
        print(f"\nГотово! Собрано {total} организаций -> {output}")
        return

    # --- Режим: по запросу ---
    if not args.query:
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
        place=args.region,
    )
    print(f"\nГотово! Собрано {len(orgs)} организаций -> {output}")


def _run_detect_selectors(args) -> None:
    """Принудительная детекция селекторов."""
    test_query = args.query or "кофейни Москва"
    print(f"\nЗапуск автодетекта (запрос: «{test_query}»)…\n")

    if SELECTORS_CACHE_FILE.exists():
        SELECTORS_CACHE_FILE.unlink()

    with _browser.sync_playwright() as pw:
        _, ctx = _browser.create_browser_context(pw, headless=not args.no_headless)
        page = _browser.setup_page(ctx)

        encoded = urllib.parse.quote(test_query)
        page.goto(
            f"https://yandex.ru/maps/?text={encoded}",
            wait_until="domcontentloaded", timeout=30000,
        )
        page.wait_for_timeout(4000)

        print("--- Детекция селекторов списка ---")
        list_sels = detect_selectors(page, mode="list")
        for k, v in list_sels.items():
            works = _probe_selector(page, v)
            status = "OK" if works else "??"
            print(f"  [{status}] {k:25s} -> {v}")

        print("\n--- Проверка hardcoded ---")
        for k, v in _HARDCODED.items():
            if k.startswith("detail_"):
                continue
            works = _probe_selector(page, v)
            status = "OK" if works else "FAIL"
            print(f"  [{status}] {k:25s} -> {v}")

        from .selectors import ITEM_SEL, LINK_SEL
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
                print(f"\n--- Детекция карточки ({href[:60]}…) ---")
                page.goto(href, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(3000)
                detail_sels = detect_selectors(page, mode="detail")
                for k, v in detail_sels.items():
                    works = _probe_selector(page, v)
                    status = "OK" if works else "??"
                    print(f"  [{status}] {k:25s} -> {v}")
                list_sels.update(detail_sels)

        ctx.close()

    if list_sels:
        cache = SelectorCache()
        cache.update(list_sels)
        cache.save()
        print(f"\nСохранено {len(list_sels)} селекторов -> {SELECTORS_CACHE_FILE}")
    else:
        print("\nАвтодетект не нашёл селекторов.")


# ---------------------------------------------------------------------------
# Интерактивное меню
# ---------------------------------------------------------------------------

def _input_choice(prompt: str, options: list[str], allow_empty: bool = False) -> str:
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
        if raw in options or allow_empty:
            return raw
        print("  Неверный выбор, попробуйте ещё раз.")


def _input_yn(prompt: str, default: bool = False) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    raw = input(f"{prompt} {hint}: ").strip().lower()
    if raw == "":
        return default
    return raw in ("y", "yes", "д", "да", "1")


def interactive_menu() -> None:
    """Пошаговое интерактивное меню."""
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
            "Сбросить профиль браузера (при проблемах с капчей)",
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

    if mode == "Сбросить профиль браузера (при проблемах с капчей)":
        if _browser.BROWSER_DATA_DIR.exists():
            if _input_yn(
                f"Удалить {_browser.BROWSER_DATA_DIR}? "
                "Это сбросит все cookies/localStorage", False
            ):
                try:
                    shutil.rmtree(_browser.BROWSER_DATA_DIR)
                    print(f"Профиль удалён: {_browser.BROWSER_DATA_DIR}")
                except Exception as exc:
                    print(f"Ошибка: {exc}")
            else:
                print("Отменено.")
        else:
            print(f"Профиль не существует: {_browser.BROWSER_DATA_DIR}")
        return

    # Город / запрос
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

    # Параметры
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
    print("  1. Базовые (название, адрес, рейтинг) — быстро")
    print("  2. Полные (+ телефон, сайт, email) — через карточки")
    print("  3. Полные через API (перехват JSON) — надёжнее")
    data_mode = input("\nНомер [3]: ").strip() or "3"

    detail = data_mode == "2"
    api_intercept = data_mode not in ("1", "2")

    print("\n--- Дополнительные настройки ---")
    headless = not _input_yn("Показывать браузер?", True)

    proxy_url = None
    if _input_yn("Использовать прокси?", False):
        print("\n  1. Один прокси")
        print("  2. Файл с прокси-списком")
        proxy_mode = input("\nНомер [1]: ").strip()
        if proxy_mode == "2":
            proxy_file = input("Путь к файлу с прокси: ").strip()
            if proxy_file:
                rotator = _browser.ProxyRotator.from_file(proxy_file)
                _browser._proxy_rotator = rotator
                proxy_url = rotator.next()
        else:
            proxy_url = input("Прокси (http://host:port): ").strip() or None

    resume_path = None
    existing_file = Path(output)
    if existing_file.exists():
        if _input_yn(f"Файл {output} существует. Продолжить (resume)?", True):
            resume_path = existing_file

    # Подтверждение
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
            query=query, max_results=max_results, output=output,
            headless=headless, detail=detail, scroll_pause=1.0,
            api_intercept=api_intercept, proxy_url=proxy_url,
            resume_path=resume_path,
        )
        print(f"\nГотово! Собрано {len(orgs)} организаций -> {output}")
    else:
        results = run_category_parser(
            city=city, categories=categories_input,
            max_results_per_category=max_results, output=output,
            headless=headless, detail=detail, scroll_pause=1.0,
            api_intercept=api_intercept, proxy_url=proxy_url,
            resume_path=resume_path,
        )
        total = sum(len(v) for v in results.values())
        print(f"\nГотово! Собрано {total} организаций -> {output}")
