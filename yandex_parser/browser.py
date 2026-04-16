"""Браузер, прокси, капча, троттлинг, warmup."""

from __future__ import annotations

import logging
import random
import signal
import sys
import time
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from patchright.sync_api import Browser, BrowserContext, Page, Response

# Ленивый импорт: sync_playwright нужен только при реальном запуске браузера.
# Это позволяет запускать тесты без установленного playwright/patchright.
_pw_module = None
log_lib = "playwright"


def _get_pw():
    global _pw_module, log_lib
    if _pw_module is not None:
        return _pw_module
    try:
        from patchright.sync_api import sync_playwright as sp
        _pw_module = sp
        log_lib = "patchright"
    except ImportError:
        from playwright.sync_api import sync_playwright as sp
        _pw_module = sp
        log_lib = "playwright"
    return _pw_module


def sync_playwright():
    """Обёртка: возвращает контекст-менеджер sync_playwright()."""
    return _get_pw()()

log = logging.getLogger("yandex_parser")

# Глобальный флаг graceful shutdown
_shutdown_requested = False

class ProxyRotator:
    """Ротация прокси-серверов для обхода блокировок.

    Форматы прокси:
      - http://host:port
      - http://user:pass@host:port
      - socks5://host:port
    """

    def __init__(self, proxies: list[str] | None = None):
        self._proxies = proxies or []
        self._index = 0
        self._fail_counts: dict[str, int] = {}

    @classmethod
    def from_file(cls, path: str) -> "ProxyRotator":
        """Загрузить прокси из файла (одна строка = один прокси)."""
        p = Path(path)
        if not p.exists():
            log.warning("Файл прокси не найден: %s", path)
            return cls([])
        lines = [
            line.strip() for line in p.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        log.info("Загружено %d прокси из %s", len(lines), path)
        return cls(lines)

    @property
    def has_proxies(self) -> bool:
        return len(self._proxies) > 0

    def next(self) -> str | None:
        """Следующий прокси (round-robin)."""
        if not self._proxies:
            return None
        proxy = self._proxies[self._index % len(self._proxies)]
        self._index += 1
        return proxy

    def current(self) -> str | None:
        if not self._proxies:
            return None
        return self._proxies[(self._index - 1) % len(self._proxies)]

    def mark_failed(self, proxy: str) -> None:
        self._fail_counts[proxy] = self._fail_counts.get(proxy, 0) + 1
        if self._fail_counts[proxy] >= 3:
            log.warning("Прокси %s — 3 ошибки, удаляю из ротации", proxy)
            self._proxies = [p for p in self._proxies if p != proxy]
            self._fail_counts.pop(proxy, None)

    def to_playwright_arg(self, proxy_url: str) -> dict:
        """Конвертировать URL прокси в формат Playwright."""
        result: dict[str, str] = {"server": proxy_url}
        parsed = urllib.parse.urlparse(proxy_url)
        if parsed.username:
            result["username"] = parsed.username
        if parsed.password:
            result["password"] = parsed.password
        if parsed.username or parsed.password:
            result["server"] = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        return result


# Глобальный ротатор (инициализируется в main)
_proxy_rotator: ProxyRotator | None = None


def get_proxy_rotator() -> ProxyRotator:
    global _proxy_rotator
    if _proxy_rotator is None:
        _proxy_rotator = ProxyRotator()
    return _proxy_rotator




_CAPTCHA_SELECTORS = [
    "[class*='captcha']",
    "[class*='Captcha']",
    "[class*='CheckboxCaptcha']",
    "#js-button",
    "[class*='smartcaptcha']",
    "iframe[src*='captcha']",
    "[class*='AdvancedCaptcha']",
]


def detect_captcha(page: Page) -> bool:
    """Проверить, показала ли страница CAPTCHA."""
    for sel in _CAPTCHA_SELECTORS:
        if page.locator(sel).count() > 0:
            return True
    # Дополнительно проверяем по URL
    if "showcaptcha" in page.url or "captcha" in page.url.lower():
        return True
    return False


def _bezier_mouse_move(page: Page, start_x: float, start_y: float,
                       end_x: float, end_y: float, steps: int = 15) -> None:
    """Двигаем мышь по кривой Безье (3 точки) — как человек.

    Прямые линии палятся как автоматизация. Человек двигает мышь по дуге
    с ускорением в начале и замедлением в конце.
    """
    # Контрольная точка — смещена вбок от прямой
    ctrl_x = (start_x + end_x) / 2 + random.randint(-80, 80)
    ctrl_y = (start_y + end_y) / 2 + random.randint(-60, 60)

    for i in range(steps + 1):
        t = i / steps
        # Квадратичная Безье: B(t) = (1-t)²·P0 + 2(1-t)t·P1 + t²·P2
        x = (1 - t) ** 2 * start_x + 2 * (1 - t) * t * ctrl_x + t ** 2 * end_x
        y = (1 - t) ** 2 * start_y + 2 * (1 - t) * t * ctrl_y + t ** 2 * end_y
        # Небольшой шум
        x += random.randint(-2, 2)
        y += random.randint(-2, 2)
        page.mouse.move(x, y)
        # Пауза: короткая в середине, длиннее в начале/конце (как человек)
        if i < 3 or i > steps - 3:
            page.wait_for_timeout(random.randint(20, 60))
        else:
            page.wait_for_timeout(random.randint(5, 20))


def _try_click_captcha(page: Page) -> bool:
    """Попробовать автоматически кликнуть 'Я не робот' (checkbox-капча).

    Использует Bezier-кривую для движения мыши — прямые линии палятся.
    """
    checkbox_sels = [
        "[class*='CheckboxCaptcha'] .CheckboxCaptcha-Button",
        "[class*='CheckboxCaptcha-Button']",
        "#js-button",
        "input[type='submit'][value*='робот']",
        "button:has-text('робот')",
        "[class*='captcha'] button",
        ".smartcaptcha input[type='checkbox']",
    ]
    for sel in checkbox_sels:
        try:
            btn = page.locator(sel).first
            if btn.count() > 0 and btn.is_visible():
                box = btn.bounding_box()
                if box:
                    # Стартуем из случайной точки на странице
                    start_x = random.randint(200, 600)
                    start_y = random.randint(100, 400)
                    page.mouse.move(start_x, start_y)
                    page.wait_for_timeout(random.randint(200, 500))

                    # Двигаем по Безье к кнопке
                    target_x = box["x"] + box["width"] / 2 + random.randint(-3, 3)
                    target_y = box["y"] + box["height"] / 2 + random.randint(-2, 2)
                    _bezier_mouse_move(page, start_x, start_y, target_x, target_y)

                    # Небольшая пауза перед кликом (человек "прицеливается")
                    page.wait_for_timeout(random.randint(100, 300))

                    # Клик по Безье-точке
                    page.mouse.click(target_x, target_y)
                else:
                    # Нет bounding box — обычный клик
                    btn.click()
                log.info("Автоклик по кнопке капчи: %s", sel)
                page.wait_for_timeout(3000)
                return not detect_captcha(page)
        except Exception:
            continue
    return False


def handle_captcha(page: Page, headless: bool) -> bool:
    """Обработать CAPTCHA: автоклик → сразу ручное решение.

    Стратегия без платных сервисов:
    1. Автоклик checkbox «Я не робот» (Bezier-мышь)
    2. Если не помогло — сразу просим решить вручную (не тратим время на reload)
    3. В headless — пауза + reload (надежда на протухание)

    Один раз решил вручную → persistent profile запоминает → следующий раз без капчи.

    Возвращает True если CAPTCHA решена, False если таймаут.
    """
    if not detect_captcha(page):
        return True

    log.warning("=" * 60)
    log.warning("ОБНАРУЖЕНА CAPTCHA!")

    # 1. Пробуем автоклик (checkbox «Я не робот») с Bezier-движением мыши
    if _try_click_captcha(page):
        log.info("CAPTCHA решена автокликом!")
        log.warning("=" * 60)
        return True

    # 2. Пауза + reload — иногда капча протухает после ожидания
    log.info("Пауза 15-25 сек + reload (капча может протухнуть)…")
    page.wait_for_timeout(random.randint(15000, 25000))
    try:
        page.reload(wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(random.randint(3000, 5000))
        if not detect_captcha(page):
            log.info("CAPTCHA исчезла после паузы и reload!")
            log.warning("=" * 60)
            return True
        # Ещё раз пробуем автоклик — может теперь простая форма
        if _try_click_captcha(page):
            log.info("CAPTCHA решена автокликом после reload!")
            log.warning("=" * 60)
            return True
    except Exception:
        pass

    # 3. Ручное решение / автоожидание
    if headless:
        # В headless ждём подольше — капча может протухнуть
        log.warning("Headless-режим: пауза 60 сек + reload…")
        page.wait_for_timeout(60000)
        try:
            page.reload(wait_until="domcontentloaded", timeout=20000)
            page.wait_for_timeout(5000)
            if not detect_captcha(page):
                log.info("CAPTCHA исчезла после паузы!")
                log.warning("=" * 60)
                return True
        except Exception:
            pass
    else:
        # В видимом браузере — СРАЗУ просим решить вручную
        log.warning("")
        log.warning("  >>> РЕШИТЕ КАПЧУ В БРАУЗЕРЕ! <<<")
        log.warning("  После решения парсер продолжит автоматически.")
        log.warning("  (persistent profile запомнит — следующий раз без капчи)")
        log.warning("")
        for i in range(60):  # 60 * 5 = 300 секунд (5 минут)
            page.wait_for_timeout(5000)
            if not detect_captcha(page):
                log.info("CAPTCHA решена! Продолжаем…")
                log.warning("=" * 60)
                return True
            if i > 0 and i % 12 == 0:
                log.info("Ожидание решения капчи… (%d сек)", i * 5)
        log.error("Таймаут ожидания решения CAPTCHA (5 мин)")

    log.warning("=" * 60)

    # Если капча не решена и используется прокси — помечаем его как failed,
    # следующий перезапуск контекста подхватит другой из ротации.
    still_captcha = detect_captcha(page)
    if still_captcha:
        rotator = get_proxy_rotator()
        current = rotator.current()
        if current:
            log.warning("Капча не решена — помечаю прокси как failed: %s",
                        current.split("@")[-1] if "@" in current else current)
            rotator.mark_failed(current)

    return not still_captcha


# С patchright + channel="chrome" этот скрипт НЕ инжектится.
_STEALTH_JS = """
() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
}
"""


BROWSER_DATA_DIR = Path(".browser_profile")


# ---------------------------------------------------------------------------
# Адаптивный троттлинг — замедляемся при появлении капчи
# ---------------------------------------------------------------------------

class AdaptiveThrottle:
    """Адаптивное управление скоростью парсинга.

    При появлении капчи увеличиваем паузы.
    При успешных запросах постепенно возвращаемся к нормальной скорости.
    """

    def __init__(self):
        self._captcha_count = 0
        self._success_streak = 0
        self._multiplier = 1.0

    def on_captcha(self) -> None:
        """Вызвать при обнаружении капчи."""
        self._captcha_count += 1
        self._success_streak = 0
        # Каждая капча удваивает замедление (макс x8)
        self._multiplier = min(8.0, self._multiplier * 2.0)
        log.info("Троттлинг: замедление x%.1f (капч: %d)", self._multiplier, self._captcha_count)

    def on_success(self) -> None:
        """Вызвать при успешном запросе без капчи."""
        self._success_streak += 1
        # После 3 успешных запросов подряд снижаем замедление
        if self._success_streak >= 3 and self._multiplier > 1.0:
            self._multiplier = max(1.0, self._multiplier * 0.7)
            self._success_streak = 0
            log.debug("Троттлинг: ускорение до x%.1f", self._multiplier)

    def get_pause(self, base_ms: int = 2000) -> int:
        """Получить паузу в мс с учётом троттлинга + рандом."""
        pause = int(base_ms * self._multiplier)
        # Добавляем 20% случайного шума
        noise = int(pause * 0.2)
        return pause + random.randint(-noise, noise)

    @property
    def multiplier(self) -> float:
        return self._multiplier

    @property
    def captcha_count(self) -> int:
        return self._captcha_count


_throttle = AdaptiveThrottle()


def get_throttle() -> AdaptiveThrottle:
    return _throttle



def create_browser_context(pw, headless: bool, proxy_url: str | None = None):
    """Создать persistent browser context с настоящим Chrome.

    Ключевые принципы (почему не ловим капчу):
    1. channel="chrome" — настоящий Chrome, не Playwright Chromium
       (другой TLS-fingerprint, нет автоматизационных маркеров)
    2. Persistent context — cookies/localStorage/кеш между запусками
       (Яндекс видит «знакомого» пользователя)
    3. НЕ подменяем User-Agent/headers — Chrome уже имеет правильные
    4. НЕ инжектим stealth JS — с patchright + real Chrome не нужно,
       а лишние патчи ПАЛЯТСЯ через getOwnPropertyDescriptor
    5. НЕ блокируем Яндекс.Метрику — её отсутствие = флаг «бот»
    """
    # Создаём директорию для профиля если нет
    BROWSER_DATA_DIR.mkdir(exist_ok=True)

    launch_args: dict[str, Any] = {
        "channel": "chrome",           # НАСТОЯЩИЙ Chrome, не Chromium
        "headless": headless,
        "no_viewport": True,            # Естественный размер окна Chrome
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-infobars",
        ],
        "locale": "ru-RU",
        "timezone_id": "Europe/Moscow",
        "color_scheme": "light",
        # НЕ ставим user_agent — Chrome уже имеет свой настоящий
        # НЕ ставим extra_http_headers — избегаем inconsistency
    }
    if proxy_url:
        rotator = get_proxy_rotator()
        launch_args["proxy"] = rotator.to_playwright_arg(proxy_url)
        log.info("Прокси: %s", proxy_url.split("@")[-1] if "@" in proxy_url else proxy_url)

    # Persistent context: всё состояние браузера сохраняется на диск
    ctx: BrowserContext = pw.chromium.launch_persistent_context(
        str(BROWSER_DATA_DIR),
        **launch_args,
    )
    log.info("Браузер: настоящий Chrome + persistent-профиль (%s)", BROWSER_DATA_DIR)

    # С patchright + channel="chrome" stealth-скрипт НЕ НУЖЕН:
    # - patchright убирает Runtime.enable CDP-утечку
    # - настоящий Chrome не имеет navigator.webdriver и прочих маркеров
    # - лишние патчи только палятся (SmartCaptcha проверяет property descriptors)
    #
    # Если fallback на обычный playwright — инжектим минимальный stealth
    if log_lib == "playwright":
        log.warning("patchright не установлен, используем playwright + stealth JS")
        ctx.add_init_script(_STEALTH_JS)

    return None, ctx


def setup_page(ctx: BrowserContext) -> Page:
    """Получить страницу из persistent-контекста."""
    # Persistent context может иметь открытые страницы — закрываем лишние
    for p in ctx.pages[1:]:
        try:
            p.close()
        except Exception:
            pass
    # Используем первую страницу persistent-контекста если есть, иначе новую
    if ctx.pages:
        page = ctx.pages[0]
    else:
        page: Page = ctx.new_page()
    # НЕ блокируем Яндекс.Метрику и аналитику!
    # Яндекс проверяет что его собственные трекеры загрузились.
    # Если mc.yandex.ru/metrika не отвечает — это флаг «бот».
    return page


_warmed_up = False


def _maps_url(geo: Any = None) -> str:
    """Построить URL Яндекс.Карт с учётом GeoEntry (если передан)."""
    if geo is not None and hasattr(geo, "geo_id") and hasattr(geo, "slug"):
        return f"https://yandex.ru/maps/{geo.geo_id}/{geo.slug}/"
    return "https://yandex.ru/maps/"


def _warmup(page: Page, headless: bool, geo: Any = None) -> None:
    """Прогрев: зайти на Яндекс как обычный пользователь перед парсингом.

    Стратегия: сначала заходим на yandex.ru (главная), потом переходим
    на карты — как обычный человек. Яндекс меньше подозревает юзеров
    с естественной цепочкой переходов и реферером.

    Если persistent-профиль уже открыт на yandex.ru/maps — пропускаем
    навигацию через главную (экономим 5-10 сек между категориями).

    Если передан ``geo`` (GeoEntry), конечный URL будет региональным
    (``/maps/<geo_id>/<slug>/``), и поиск будет ограничен этим регионом.
    """
    global _warmed_up
    if _warmed_up:
        return
    _warmed_up = True

    # Если уже на картах (например, между категориями) — короткий прогрев
    current_url = ""
    try:
        current_url = page.url or ""
    except Exception:
        pass
    if "yandex.ru/maps" in current_url or "yandex.com/maps" in current_url:
        log.info("Уже на Я.Картах — короткий прогрев (без перехода через главную)")
        try:
            # Пара случайных движений мыши — имитация активности
            for _ in range(random.randint(2, 4)):
                page.mouse.move(
                    random.randint(100, 900),
                    random.randint(100, 600),
                )
                page.wait_for_timeout(random.randint(100, 400))
            if detect_captcha(page):
                handle_captcha(page, headless)
        except Exception:
            pass
        return

    log.info("Прогрев: естественная навигация yandex.ru → карты…")
    try:
        # Шаг 1: заходим на главную Яндекса (как обычный пользователь)
        page.goto("https://yandex.ru/", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(random.randint(2000, 4000))

        # Проверяем капчу на главной
        if detect_captcha(page):
            handle_captcha(page, headless)

        # Двигаем мышку — «осматриваемся» на главной
        for _ in range(random.randint(2, 4)):
            page.mouse.move(
                random.randint(100, 900),
                random.randint(100, 600),
            )
            page.wait_for_timeout(random.randint(100, 400))

        # Шаг 2: переходим на карты через навигацию (реферер yandex.ru)
        page.wait_for_timeout(random.randint(1000, 2500))
        page.goto(_maps_url(geo), wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(random.randint(2000, 4000))

        # Проверяем капчу на картах
        if detect_captcha(page):
            handle_captcha(page, headless)

        # Двигаем мышку случайно по карте
        for _ in range(random.randint(3, 6)):
            page.mouse.move(
                random.randint(100, 900),
                random.randint(100, 700),
            )
            page.wait_for_timeout(random.randint(100, 400))

        # Кликаем куда-нибудь на карту
        page.mouse.click(random.randint(500, 900), random.randint(300, 600))
        page.wait_for_timeout(random.randint(1000, 2500))

        # Скроллим карту немного (зум)
        page.mouse.wheel(0, random.randint(-200, 200))
        page.wait_for_timeout(random.randint(500, 1500))

        # Иногда (30%) «ищем что-то» на карте — создаёт видимость активности
        if random.random() < 0.3:
            try:
                search_input = page.locator("input[class*='search'], input[class*='input']").first
                if search_input.count() > 0 and search_input.is_visible():
                    search_input.click()
                    page.wait_for_timeout(random.randint(300, 700))
                    # Просто кликнули и «передумали»
                    page.mouse.click(random.randint(500, 900), random.randint(300, 600))
                    page.wait_for_timeout(random.randint(500, 1000))
            except Exception:
                pass

        log.info("Прогрев завершён")
    except Exception as exc:
        log.debug("Прогрев не удался: %s", exc)


def _type_like_human(page: Page, selector: str, text: str) -> None:
    """Напечатать текст по буквам с человеческими задержками между символами."""
    el = page.locator(selector).first
    el.click()
    page.wait_for_timeout(random.randint(200, 500))
    # Очищаем поле (Ctrl+A → Delete)
    el.press("Control+a")
    page.wait_for_timeout(random.randint(50, 150))
    el.press("Delete")
    page.wait_for_timeout(random.randint(100, 300))
    # Печатаем по буквам
    for char in text:
        el.press_sequentially(char, delay=random.randint(40, 120))
        # Иногда (5%) микро-пауза — человек думает
        if random.random() < 0.05:
            page.wait_for_timeout(random.randint(200, 600))


def _do_search(page: Page, query: str, headless: bool, geo: Any = None) -> bool:
    """Выполнить поиск: ввести запрос в строку поиска как человек.

    Возвращает True если удалось ввести и отправить запрос.
    Fallback: если строка поиска не найдена — goto по URL.
    Если передан ``geo``, fallback-URL ограничен регионом.
    """
    # Пробуем найти строку поиска на странице
    search_sels = [
        "input[class*='input__control']",
        "input[class*='search-form']",
        "[class*='search-form-view__input'] input",
        "input[placeholder*='Поиск']",
        "input[aria-label*='Поиск']",
    ]
    for sel in search_sels:
        try:
            inp = page.locator(sel).first
            if inp.count() > 0 and inp.is_visible():
                log.info("Ввожу запрос в строку поиска: %s", query)
                _type_like_human(page, sel, query)
                page.wait_for_timeout(random.randint(300, 700))
                inp.press("Enter")
                return True
        except Exception:
            continue

    # Fallback: goto по URL (менее естественно, но работает)
    log.debug("Строка поиска не найдена — используем goto")
    encoded_query = urllib.parse.quote(query)
    base = _maps_url(geo).rstrip("/")
    page.goto(
        f"{base}/?text={encoded_query}",
        wait_until="domcontentloaded", timeout=30000,
    )
    return True


def _install_sigint_handler() -> None:
    """Установить обработчик SIGINT для graceful shutdown.

    При первом Ctrl+C ставим флаг — основной цикл сохранит данные и завершится.
    При втором Ctrl+C — немедленный выход.
    """
    def handler(signum, frame):
        global _shutdown_requested
        if _shutdown_requested:
            log.warning("Повторный SIGINT — немедленный выход")
            sys.exit(130)
        _shutdown_requested = True
        log.warning(
            "Получен SIGINT — завершаюсь после текущей операции "
            "(нажмите Ctrl+C ещё раз для немедленного выхода)"
        )
    try:
        signal.signal(signal.SIGINT, handler)
    except (ValueError, OSError):
        # signal работает только в главном потоке
        pass

