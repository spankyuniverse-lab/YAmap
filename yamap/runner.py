"""High-level entry points: run_parser, run_category_parser."""

from __future__ import annotations

import logging
import random
from pathlib import Path

from ._playwright import sync_playwright
from .browser import create_browser_context, reset_warmup, setup_page
from .categories import resolve_categories
from .metrics import RunMetrics
from .models import Organization, dedup_key
from .output import (
    make_incremental_saver,
    print_stats,
    save_auto,
    save_csv,
    save_json,
    save_xlsx_by_categories,
)
from .resume import ResumeManager
from .scraper import enrich_orgs, search_with_retry
from .throttle import get_throttle


log = logging.getLogger("yandex_parser")


try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False


def run_parser(
    query: str,
    max_results: int = 500,
    output: str = "results.xlsx",
    headless: bool = True,
    detail: bool = False,
    scroll_pause: float = 1.0,
    api_intercept: bool = False,
    proxy_url: str | None = None,
    resume_path: Path | None = None,
) -> list[Organization]:
    """Парсер по одному поисковому запросу."""
    reset_warmup()
    out_path = Path(output)
    metrics = RunMetrics(queries_total=1)

    resume = ResumeManager(resume_path) if resume_path else None
    if resume and resume.existing_count > 0:
        log.info("Резюме: %d организаций уже собраны", resume.existing_count)

    on_org, incremental_orgs = make_incremental_saver(out_path, save_every=25)

    interrupted = False
    orgs: list[Organization] = []

    try:
        with sync_playwright() as pw:
            _, ctx = create_browser_context(pw, headless, proxy_url=proxy_url)
            page = setup_page(ctx)

            try:
                orgs = search_with_retry(
                    page, query, max_results, scroll_pause, api_intercept,
                    on_org=on_org, headless=headless,
                )
                metrics.queries_done = 1
                if not orgs:
                    metrics.queries_empty = 1

                for org in orgs:
                    org.search_query = query

                if resume:
                    before = len(orgs)
                    orgs = [o for o in orgs if not resume.is_known(o.name, o.address)]
                    if before != len(orgs):
                        log.info("Резюме: пропущено %d уже собранных", before - len(orgs))
                    orgs = resume.existing_orgs() + orgs

                if detail:
                    enrich_orgs(ctx, orgs)
            finally:
                try:
                    ctx.close()
                except Exception as exc:
                    log.debug("Ошибка закрытия контекста: %s", exc)
    except KeyboardInterrupt:
        interrupted = True
        metrics.interrupted = True
        log.warning("\nПрервано пользователем (Ctrl+C). Сохраняю собранное…")
        if not orgs:
            orgs = list(incremental_orgs)

    metrics.orgs_collected = len(orgs)
    metrics.captcha_hits = get_throttle().captcha_count

    save_auto(orgs, out_path)
    print_stats(orgs)
    print(metrics.report("Метрики запуска"))
    if interrupted:
        print(f"  Продолжить: python yandex_parser.py {query!r} --resume {out_path}")
    return orgs


def run_category_parser(
    city: str,
    categories: list[str],
    max_results_per_category: int = 500,
    output: str = "categories.xlsx",
    headless: bool = True,
    detail: bool = False,
    scroll_pause: float = 1.0,
    api_intercept: bool = False,
    proxy_url: str | None = None,
    resume_path: Path | None = None,
) -> dict[str, list[Organization]]:
    """Парсер по категориям: для каждой категории запускает поиск «категория город»."""
    reset_warmup()
    out_path = Path(output)
    results: dict[str, list[Organization]] = {}
    seen_global: set[str] = set()

    resume = ResumeManager(resume_path) if resume_path else None
    if resume and resume.existing_count > 0:
        seen_global = resume.existing_keys()
        log.info("Резюме: %d организаций уже собраны", resume.existing_count)

    queries = resolve_categories(categories)
    total_queries = len(queries)
    log.info(
        "Город: %s | Категорий: %d | Макс. на категорию: %d",
        city, total_queries, max_results_per_category,
    )
    metrics = RunMetrics(queries_total=total_queries)

    cat_iter = enumerate(queries, 1)
    if HAS_TQDM:
        cat_iter_tqdm = tqdm(list(cat_iter), desc="Категории", unit="кат")
    else:
        cat_iter_tqdm = None

    interrupted = False

    try:
        with sync_playwright() as pw:
            _, ctx = create_browser_context(pw, headless, proxy_url=proxy_url)
            page = setup_page(ctx)

            try:
                for q_idx, cat_query in (cat_iter_tqdm if cat_iter_tqdm else enumerate(queries, 1)):
                    full_query = f"{cat_query} {city}"

                    if resume and resume.is_query_done(full_query):
                        log.info("Пропуск (резюме): %s", full_query)
                        continue

                    log.info("━━━ [%d/%d] %s ━━━", q_idx, total_queries, full_query)

                    orgs = search_with_retry(
                        page, full_query, max_results_per_category,
                        scroll_pause, api_intercept, headless=headless,
                    )
                    metrics.queries_done += 1
                    if not orgs:
                        metrics.queries_empty += 1

                    unique_orgs: list[Organization] = []
                    for org in orgs:
                        key = dedup_key(org)
                        if key not in seen_global:
                            seen_global.add(key)
                            org.search_query = full_query
                            unique_orgs.append(org)

                    if detail:
                        enrich_orgs(ctx, unique_orgs)

                    results[full_query] = unique_orgs
                    log.info("Категория «%s»: %d организаций (уникальных)", cat_query, len(unique_orgs))

                    try:
                        suffix = out_path.suffix.lower()
                        if suffix in (".csv", ".json"):
                            all_tmp = [o for v in results.values() for o in v]
                            (save_csv if suffix == ".csv" else save_json)(all_tmp, out_path)
                        else:
                            save_xlsx_by_categories(results, out_path)
                        total_so_far = sum(len(v) for v in results.values())
                        log.info("Промежуточное сохранение: %d записей → %s", total_so_far, out_path)
                    except Exception as exc:
                        log.debug("Ошибка промежуточного сохранения: %s", exc)

                    if q_idx < total_queries:
                        throttle = get_throttle()
                        pause = throttle.get_pause(base_ms=random.randint(3000, 6000))
                        log.debug("Пауза между категориями: %d мс (x%.1f)", pause, throttle.multiplier)
                        page.wait_for_timeout(pause)

                        # 20%: «гуляем» по карте между запросами — выглядит естественно
                        if random.random() < 0.20:
                            for _ in range(random.randint(2, 4)):
                                page.mouse.move(
                                    random.randint(500, 1000),
                                    random.randint(200, 700),
                                )
                                page.wait_for_timeout(random.randint(200, 600))
                            page.mouse.wheel(0, random.randint(-100, 100))
                            page.wait_for_timeout(random.randint(500, 1500))
            finally:
                try:
                    ctx.close()
                except Exception as exc:
                    log.debug("Ошибка закрытия контекста: %s", exc)
    except KeyboardInterrupt:
        interrupted = True
        metrics.interrupted = True
        log.warning("\nПрервано пользователем (Ctrl+C). Сохраняю собранное…")

    all_orgs: list[Organization] = []
    for v in results.values():
        all_orgs.extend(v)

    if resume:
        existing = resume.existing_orgs()
        all_orgs = existing + all_orgs

    metrics.orgs_collected = len(all_orgs)
    metrics.captcha_hits = get_throttle().captcha_count

    suffix = out_path.suffix.lower()
    if suffix == ".json":
        save_json(all_orgs, out_path)
    elif suffix == ".csv":
        save_csv(all_orgs, out_path)
    else:
        save_xlsx_by_categories(results, out_path)

    log.info("Всего собрано: %d организаций по %d категориям", len(all_orgs), total_queries)
    print_stats(all_orgs, label=f"Статистика: {city}")
    print(metrics.report(f"Метрики запуска: {city}"))
    if interrupted:
        cats_arg = " ".join(categories)
        print(f"  Продолжить: python yandex_parser.py --city {city} --category {cats_arg} --resume {out_path}")
    return results
