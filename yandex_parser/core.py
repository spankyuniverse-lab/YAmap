"""Основные функции парсинга: run_parser, run_category_parser."""

from __future__ import annotations

import logging
import random
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from patchright.sync_api import BrowserContext, Page

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

from . import browser as _browser
from .models import Organization, _dedup_key, _filter_valid, print_stats
from .config import ResumeManager, resolve_categories
from .geography import resolve as _geo_resolve
from .export import (
    save_auto,
    save_csv,
    save_json,
    save_xlsx,
    save_xlsx_by_categories,
)
from .scraper import (
    _enrich_orgs,
    _make_incremental_saver,
    _search_with_retry,
)

log = logging.getLogger("yandex_parser")


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
    place: str | None = None,
) -> list[Organization]:
    """Парсер по одному поисковому запросу.

    ``place`` — опциональный город/регион; если разрешается в geo_id
    из справочника, поиск ограничивается URL /maps/<geo_id>/<slug>/.
    """
    _browser._warmed_up = False
    _browser._install_sigint_handler()
    out_path = Path(output)

    resume = ResumeManager(resume_path) if resume_path else None
    if resume and resume.existing_count > 0:
        log.info("Резюме: %d организаций уже собраны", resume.existing_count)

    geo = _geo_resolve(place) if place else None
    if geo is not None:
        log.info("Регион: %s (geo_id=%d, %s)", geo.name, geo.geo_id, geo.kind)

    on_org, incremental_orgs = _make_incremental_saver(out_path, save_every=25)

    with _browser.sync_playwright() as pw:
        _, ctx = _browser.create_browser_context(pw, headless, proxy_url=proxy_url)
        page = _browser.setup_page(ctx)

        orgs = _search_with_retry(
            page, query, max_results, scroll_pause, api_intercept,
            on_org=on_org, headless=headless, geo=geo,
        )

        for org in orgs:
            org.search_query = query

        if resume:
            before = len(orgs)
            orgs = [o for o in orgs if not resume.is_known(o.name, o.address)]
            if before != len(orgs):
                log.info("Резюме: пропущено %d уже собранных", before - len(orgs))
            orgs = resume.existing_orgs() + orgs

        if detail and not _browser._shutdown_requested:
            _enrich_orgs(ctx, orgs)

        ctx.close()

    orgs = _filter_valid(orgs)
    save_auto(orgs, out_path)
    print_stats(orgs)
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
    """Парсер по категориям: для каждой категории запускает поиск."""
    _browser._warmed_up = False
    _browser._install_sigint_handler()
    out_path = Path(output)
    results: dict[str, list[Organization]] = {}
    seen_global: set[str] = set()

    resume = ResumeManager(resume_path) if resume_path else None
    if resume and resume.existing_count > 0:
        seen_global = resume.existing_keys()
        log.info("Резюме: %d организаций уже собраны", resume.existing_count)

    queries = resolve_categories(categories)
    total_queries = len(queries)

    # Если город разрешается в известный geo_id — поиск будет ограничен
    # границами региона (URL /maps/<geo_id>/<slug>/), а в текст запроса
    # город не добавляем (Яндекс уже знает регион из URL).
    geo = _geo_resolve(city)
    if geo is not None:
        log.info(
            "Город: %s → geo_id=%d (%s, %s) | Категорий: %d | Макс.: %d",
            city, geo.geo_id, geo.slug, geo.kind,
            total_queries, max_results_per_category,
        )
    else:
        log.info(
            "Город: %s (без geo_id) | Категорий: %d | Макс.: %d",
            city, total_queries, max_results_per_category,
        )

    cat_iter = enumerate(queries, 1)
    if HAS_TQDM:
        cat_iter_tqdm = tqdm(list(cat_iter), desc="Категории", unit="кат")
    else:
        cat_iter_tqdm = None

    with _browser.sync_playwright() as pw:
        _, ctx = _browser.create_browser_context(pw, headless, proxy_url=proxy_url)
        page = _browser.setup_page(ctx)

        throttle = _browser.get_throttle()
        captcha_baseline = throttle.captcha_count
        rotator = _browser.get_proxy_rotator()

        for q_idx, cat_query in (cat_iter_tqdm if cat_iter_tqdm else enumerate(queries, 1)):
            if _browser._shutdown_requested:
                log.warning("Graceful shutdown — прерываю обход категорий")
                break

            # Ротация прокси при серии капч
            if (rotator.has_proxies
                    and throttle.captcha_count - captcha_baseline >= 2):
                new_proxy = rotator.next()
                if new_proxy and new_proxy != proxy_url:
                    log.warning(
                        "Ротация прокси: %d капч подряд → пересоздаю контекст (%s)",
                        throttle.captcha_count - captcha_baseline,
                        new_proxy.split("@")[-1] if "@" in new_proxy else new_proxy,
                    )
                    try:
                        ctx.close()
                    except Exception:
                        pass
                    proxy_url = new_proxy
                    _, ctx = _browser.create_browser_context(
                        pw, headless, proxy_url=proxy_url,
                    )
                    page = _browser.setup_page(ctx)
                    captcha_baseline = throttle.captcha_count
                    _browser._warmed_up = False

            # Если регион определён, не дублируем город в тексте запроса —
            # область уже ограничена URL. Если нет — добавляем к тексту.
            if geo is not None:
                full_query = cat_query
            else:
                full_query = f"{cat_query} {city}"

            if resume and resume.is_query_done(full_query):
                log.info("Пропуск (резюме): %s", full_query)
                continue

            log.info("━━━ [%d/%d] %s ━━━", q_idx, total_queries, full_query)

            # Инкрементальный callback
            SAVE_EVERY = 25
            save_counter = {"n": 0}

            def _save_all_now() -> None:
                try:
                    sfx = out_path.suffix.lower()
                    if sfx == ".csv":
                        all_tmp = [o for v in results.values() for o in v]
                        save_csv(all_tmp, out_path)
                    elif sfx == ".json":
                        all_tmp = [o for v in results.values() for o in v]
                        save_json(all_tmp, out_path)
                    else:
                        save_xlsx_by_categories(results, out_path)
                except Exception as exc:
                    log.debug("Ошибка инкрементального сохранения: %s", exc)

            def on_org_incremental(org: Organization, _index: int) -> None:
                key = _dedup_key(org)
                if key in seen_global:
                    return
                seen_global.add(key)
                org.search_query = full_query
                results.setdefault(full_query, []).append(org)
                save_counter["n"] += 1
                if save_counter["n"] % SAVE_EVERY == 0:
                    _save_all_now()

            orgs = _search_with_retry(
                page, full_query, max_results_per_category,
                scroll_pause, api_intercept,
                on_org=on_org_incremental,
                headless=headless,
                geo=geo,
            )

            # Fallback: если callback пропустил что-то
            for org in _filter_valid(orgs):
                key = _dedup_key(org)
                if key not in seen_global:
                    seen_global.add(key)
                    org.search_query = full_query
                    results.setdefault(full_query, []).append(org)

            results[full_query] = _filter_valid(results.get(full_query, []))

            if detail and not _browser._shutdown_requested:
                _enrich_orgs(ctx, results[full_query])

            unique_orgs = results[full_query]
            log.info("Категория «%s»: %d организаций (уникальных)",
                     cat_query, len(unique_orgs))

            # Промежуточное сохранение после каждой категории
            _save_all_now()
            total_so_far = sum(len(v) for v in results.values())
            log.info("Промежуточное сохранение: %d записей → %s",
                     total_so_far, out_path)

            # Адаптивная пауза между категориями
            if q_idx < total_queries:
                pause = throttle.get_pause(base_ms=random.randint(3000, 6000))
                log.debug("Пауза между категориями: %d мс (x%.1f)",
                          pause, throttle.multiplier)
                page.wait_for_timeout(pause)

                if random.random() < 0.20:
                    for _ in range(random.randint(2, 4)):
                        page.mouse.move(
                            random.randint(500, 1000),
                            random.randint(200, 700),
                        )
                        page.wait_for_timeout(random.randint(200, 600))
                    page.mouse.wheel(0, random.randint(-100, 100))
                    page.wait_for_timeout(random.randint(500, 1500))

        ctx.close()

    # Финальное сохранение
    all_orgs: list[Organization] = []
    for v in results.values():
        all_orgs.extend(v)

    if resume:
        existing = resume.existing_orgs()
        all_orgs = existing + all_orgs

    suffix = out_path.suffix.lower()
    if suffix == ".json":
        save_json(all_orgs, out_path)
    elif suffix == ".csv":
        save_csv(all_orgs, out_path)
    else:
        save_xlsx_by_categories(results, out_path)

    total_orgs = len(all_orgs)
    log.info("Всего собрано: %d организаций по %d категориям",
             total_orgs, total_queries)
    print_stats(all_orgs, label=f"Статистика: {city}")
    return results
