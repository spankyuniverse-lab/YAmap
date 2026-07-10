"""
Парсер Яндекс.Карт (Казахстан, yandex.kz) — сбор ВСЕХ доступных данных
об организациях по поисковому запросу или по категориям.

По умолчанию работает с доменом yandex.kz и пинит гео-вьюпорт Казахстана
(ll=долгота,широта + z + lang=ru_RU), чтобы выдача была казахстанской, а не
московской. Домен переключается флагом --tld (kz/ru/com/by/uz/tr).

Яндекс.Карты используют динамическую подгрузку результатов (infinite scroll),
поэтому применяется Playwright/patchright с эмуляцией прокрутки и обходом
анти-фрод системы (SmartCaptcha): настоящий Chrome, persistent-профиль,
человеческая мышь (Безье), прогрев, адаптивный троттлинг, ротация прокси.

Режимы работы:
  1. По запросу:    python yandex_parser.py "кофейни Алматы"
  2. По категориям: python yandex_parser.py --city Алматы --category еда рестораны кафе
  3. Все категории: python yandex_parser.py --city Астана --all-categories
  4. Список категорий: python yandex_parser.py --list-categories
  5. Список городов KZ: python yandex_parser.py --list-cities

Дополнительно:
  --api-intercept  — перехват JSON из внутреннего API (надёжнее DOM, все поля)
  --detail         — открытие карточек для телефона/сайта
  --ll / --z       — центр карты и зум для гео-привязки
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import random
import re
import sys
import time
import urllib.parse
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

try:
    from tqdm import tqdm
    HAS_TQDM = True
except ImportError:
    HAS_TQDM = False

# Patchright — drop-in замена Playwright без Runtime.enable CDP-утечки.
# SmartCaptcha/Cloudflare/DataDome детектят Playwright через Runtime.enable —
# patchright обходит это, выполняя JS в изолированных execution contexts.
# API 100% совместим с Playwright, меняется только импорт.
try:
    from patchright.sync_api import (
        Browser,
        BrowserContext,
        Page,
        Response,
        sync_playwright,
    )
    log_lib = "patchright"
except ImportError:
    # Fallback на обычный Playwright если patchright не установлен
    from playwright.sync_api import (
        Browser,
        BrowserContext,
        Page,
        Response,
        sync_playwright,
    )
    log_lib = "playwright"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("yandex_parser")

# ---------------------------------------------------------------------------
# Домен и гео-конфигурация (по умолчанию — Казахстан, yandex.kz)
#
# Все три хоста (yandex.kz / yandex.com / yandex.ru) обслуживают один и тот же
# SPA Яндекс.Карт и один и тот же бэкенд организаций. Разница — в регионе по
# умолчанию: yandex.ru без гео-контекста уводит выдачу в Москву (регион 213).
# Поэтому для KZ мы (1) используем yandex.kz как базу и (2) ВСЕГДА пиним
# вьюпорт ll=lon,lat + z + lang=ru_RU, чтобы выдача была казахстанской.
#
# Карточки организаций канонизируются на yandex.com/maps/org/<slug>/<id>/,
# поэтому регэкспы/переходы должны принимать и .com, и .kz.
# ---------------------------------------------------------------------------

# код TLD -> (хост, язык, центр страны "lon,lat", зум по стране)
DOMAIN_REGISTRY: dict[str, tuple[str, str, str | None, int]] = {
    "kz": ("yandex.kz", "ru_RU", "66.9237,48.0196", 5),      # Казахстан (регион 159)
    "ru": ("yandex.ru", "ru_RU", "37.6173,55.7558", 5),      # Россия
    "com": ("yandex.com", "en_US", None, 5),
    "by": ("yandex.by", "ru_RU", None, 5),
    "uz": ("yandex.uz", "ru_RU", None, 5),
    "tr": ("yandex.com.tr", "tr_TR", None, 5),
}

# Активная конфигурация домена (переопределяется в main() через --tld/--config).
DOMAIN = "yandex.kz"   # хост по умолчанию — Казахстан
LANG = "ru_RU"         # язык выдачи (kk_KZ у Карт нет — используем русский)

# Пресеты городов Казахстана: ll = "lon,lat" (долгота ПЕРВАЯ — как в GeoJSON).
# Зум по умолчанию z=12 (город); 10 — пригороды, 14 — плотный центр.
KZ_CITIES: dict[str, str] = {
    "Алматы": "76.8897,43.2389",
    "Астана": "71.4491,51.1694",            # не «Нур-Султан»; регион 163
    "Шымкент": "69.5967,42.3167",           # не «Чимкент»
    "Караганда": "73.1094,49.8047",
    "Актобе": "57.1670,50.2839",
    "Тараз": "71.3667,42.9000",
    "Павлодар": "76.9674,52.2870",
    "Усть-Каменогорск": "82.6279,49.9481",  # каз. Өскемен
    "Семей": "80.2275,50.4111",             # бывш. Семипалатинск
    "Атырау": "51.9238,47.0945",            # регион 10291
    "Костанай": "63.6246,53.2144",
    "Кызылорда": "65.4823,44.8479",
    "Уральск": "51.3667,51.2333",           # каз. Орал
    "Петропавловск": "69.1500,54.8667",     # каз. Петропавл
    "Актау": "51.1408,43.6350",
    "Темиртау": "72.9644,50.0547",
    "Туркестан": "68.2517,43.3017",
    "Кокшетау": "69.3833,53.2833",
}
KZ_COUNTRY_LL = "66.9237,48.0196"   # весь Казахстан (регион 159), использовать с z=5
DEFAULT_CITY_Z = 12                 # зум для сбора по городу

# Таймзона/локаль браузера должны соответствовать домену (анти-фрод консистентность).
DOMAIN_TZ: dict[str, str] = {
    "yandex.kz": "Asia/Almaty",
    "yandex.ru": "Europe/Moscow",
    "yandex.com": "Europe/Moscow",
    "yandex.by": "Europe/Minsk",
    "yandex.uz": "Asia/Tashkent",
    "yandex.com.tr": "Europe/Istanbul",
}
DOMAIN_LOCALE: dict[str, str] = {
    "yandex.kz": "ru-RU",
    "yandex.ru": "ru-RU",
    "yandex.com": "en-US",
    "yandex.by": "ru-RU",
    "yandex.uz": "ru-RU",
    "yandex.com.tr": "tr-TR",
}

# Активный вьюпорт (устанавливается из --city / --ll в main()).
MAP_LL: str | None = None
MAP_Z: int = DEFAULT_CITY_Z


def set_domain(code: str | None) -> None:
    """Установить активный хост/язык по короткому коду TLD (kz/ru/com/…)."""
    global DOMAIN, LANG
    host, lang, _, _ = DOMAIN_REGISTRY.get((code or "kz").lower(), DOMAIN_REGISTRY["kz"])
    DOMAIN, LANG = host, lang


def set_viewport(city: str | None = None, ll: str | None = None, z: int | None = None) -> None:
    """Установить центр карты (ll=lon,lat) и зум для гео-привязки выдачи к KZ."""
    global MAP_LL, MAP_Z
    if ll:
        MAP_LL, MAP_Z = ll, (z or DEFAULT_CITY_Z)
    elif city and city in KZ_CITIES:
        MAP_LL, MAP_Z = KZ_CITIES[city], (z or DEFAULT_CITY_Z)
    else:
        # Нет города/координат — центрируемся на всей стране домена, если известно.
        _, _, country_ll, country_z = DOMAIN_REGISTRY.get(
            _tld_of(DOMAIN), DOMAIN_REGISTRY["kz"]
        )
        MAP_LL, MAP_Z = country_ll, (z or country_z)


def _tld_of(host: str) -> str:
    """yandex.kz -> kz, yandex.com.tr -> tr, yandex.com -> com."""
    for code, (h, *_rest) in DOMAIN_REGISTRY.items():
        if h == host:
            return code
    return "kz"


def base_url() -> str:
    return f"https://{DOMAIN}"


def maps_url() -> str:
    return f"https://{DOMAIN}/maps/"


def org_url(seoname: str, org_id: str) -> str:
    """Ссылка на карточку организации (канонизируется на yandex.com, но любой хост резолвит)."""
    return f"https://{DOMAIN}/maps/org/{seoname}/{org_id}/"


def search_url(query: str, with_viewport: bool = True) -> str:
    """URL поиска с языком и (опционально) закреплённым вьюпортом для гео-привязки."""
    u = f"https://{DOMAIN}/maps/?text={urllib.parse.quote(query)}&lang={LANG}"
    if with_viewport and MAP_LL:
        u += f"&ll={MAP_LL}&z={MAP_Z}"
    return u


# ---------------------------------------------------------------------------
# Постоянные отчёты и логи (сохраняются АВТОМАТИЧЕСКИ при каждом запуске)
#
#   logs/yamap_<дата>.log     — полный лог всего происходящего (DEBUG)
#   reports/<время>_<метка>.json + .md — отчёт по каждому прогону
#   reports/history.jsonl     — история всех запусков (по строке на прогон)
# ---------------------------------------------------------------------------

LOGS_DIR = Path("logs")
REPORTS_DIR = Path("reports")
_RUN_ERRORS: list[str] = []
_file_logging_ready = False


def setup_file_logging() -> Path:
    """Включить запись ВСЕХ логов в файл (в дополнение к консоли). Идемпотентно."""
    global _file_logging_ready
    LOGS_DIR.mkdir(exist_ok=True)
    log_path = LOGS_DIR / f"yamap_{time.strftime('%Y-%m-%d')}.log"
    if _file_logging_ready:
        return log_path
    root = logging.getLogger()
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    root.addHandler(handler)
    # В файл пишем DEBUG, но консоль оставляем на INFO (не засоряем вывод).
    root.setLevel(logging.DEBUG)
    for h in root.handlers:
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            h.setLevel(logging.INFO)
    _file_logging_ready = True
    log.info("Логи пишутся в файл: %s", log_path)
    return log_path


def record_error(msg: str) -> None:
    """Зафиксировать ошибку/инцидент для попадания в отчёт."""
    _RUN_ERRORS.append(f"{time.strftime('%H:%M:%S')}  {msg}")


def _report_to_md(r: dict) -> str:
    """Человекочитаемый Markdown-отчёт."""
    st = r.get("stats", {})
    lines = [
        f"# Отчёт YAmap — {r.get('timestamp', '')}",
        "",
        f"- **Режим:** {r.get('mode', '')}",
        f"- **Метка:** {r.get('label', '')}",
        f"- **Домен:** {r.get('domain', '')}  | язык: {r.get('lang', '')}",
        f"- **Вьюпорт:** ll={r.get('viewport_ll', '—')} z={r.get('viewport_z', '')}",
        f"- **Параметры:** max={r.get('max_results', '')}, api_intercept={r.get('api_intercept', '')}, "
        f"detail={r.get('detail', '')}, headless={r.get('headless', '')}, proxy={r.get('proxy', '')}",
        f"- **Длительность:** {r.get('duration_sec', '')} c",
        f"- **Капч поймано:** {r.get('captchas', 0)}",
        f"- **Файл результатов:** {r.get('output', '')}",
        "",
        "## Итоги сбора",
        "",
        f"- Всего организаций: **{st.get('total', 0)}**",
    ]
    cov = st.get("coverage", {})
    if cov:
        lines.append("- Покрытие полей:")
        for k, v in cov.items():
            lines.append(f"    - {k}: {v['count']} ({v['pct']}%)")
    if st.get("avg_rating"):
        lines.append(f"- Средний рейтинг: {st['avg_rating']}")
    if st.get("top_categories"):
        lines.append("- Топ категории:")
        for name, cnt in st["top_categories"]:
            lines.append(f"    - {name}: {cnt}")
    if r.get("per_query"):
        lines += ["", "## По запросам/категориям", ""]
        for q, cnt in r["per_query"].items():
            lines.append(f"- {q}: {cnt}")
    if r.get("errors"):
        lines += ["", "## Инциденты / ошибки", ""]
        for e in r["errors"]:
            lines.append(f"- {e}")
    return "\n".join(lines) + "\n"


def save_run_report(meta: dict, stats: dict) -> Path | None:
    """Сохранить отчёт о прогоне (JSON + Markdown) и дописать history.jsonl.

    Вызывается автоматически в конце каждого прогона (даже при частичном сборе).
    """
    try:
        REPORTS_DIR.mkdir(exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        label = re.sub(r"[^\w-]+", "_", str(meta.get("label", "run")))[:40].strip("_") or "run"
        stem = f"{ts}_{label}"
        report = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            **meta,
            "stats": stats,
            "errors": list(_RUN_ERRORS),
        }
        (REPORTS_DIR / f"{stem}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        (REPORTS_DIR / f"{stem}.md").write_text(_report_to_md(report), encoding="utf-8")
        with open(REPORTS_DIR / "history.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "timestamp": report["timestamp"],
                "label": meta.get("label"),
                "mode": meta.get("mode"),
                "domain": meta.get("domain"),
                "total": stats.get("total"),
                "captchas": meta.get("captchas"),
                "duration_sec": meta.get("duration_sec"),
                "output": meta.get("output"),
                "errors": len(_RUN_ERRORS),
            }, ensure_ascii=False) + "\n")
        log.info("Отчёт сохранён: reports/%s.json (+ .md), история → reports/history.jsonl", stem)
        return REPORTS_DIR / f"{stem}.json"
    except Exception as exc:
        log.debug("Не удалось сохранить отчёт: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Organization:
    # --- базовые поля (порядок колонок сохраняется, новые поля добавляются в конец) ---
    name: str = ""
    address: str = ""
    phone: str = ""
    website: str = ""
    rating: str = ""
    reviews_count: str = ""
    category: str = ""
    working_hours: str = ""
    latitude: str = ""
    longitude: str = ""
    email: str = ""
    social_links: str = ""
    yandex_url: str = ""
    search_query: str = ""
    # --- расширенные поля: «снять все данные» с Яндекс.Карт ---
    # идентификаторы и ссылки
    org_id: str = ""             # items[].id (стабильный permalink/oid)
    seoname: str = ""            # items[].seoname (slug рубрики/организации)
    org_uri: str = ""            # items[].uri (ymapsbm1://org?oid=...)
    # названия
    short_name: str = ""         # items[].shortTitle
    legal_name: str = ""         # items[].name (юр. лицо, если отличается от title)
    # структурированный адрес
    full_address: str = ""       # items[].fullAddress (с городом)
    additional_address: str = "" # items[].additionalAddress (этаж/вход/в ТЦ)
    postal_code: str = ""        # compositeAddress.postalCode
    country: str = ""            # items[].country
    region_name: str = ""        # compositeAddress.region
    locality: str = ""           # compositeAddress.locality (город)
    district: str = ""           # compositeAddress.district (район)
    street: str = ""             # compositeAddress.street
    house: str = ""              # compositeAddress.house
    geo_id: str = ""             # items[].geoId (числовой ID региона)
    display_latitude: str = ""   # displayCoordinates[1] (координата метки)
    display_longitude: str = ""  # displayCoordinates[0]
    # контакты
    websites_all: str = ""       # items[].urls через "; " (website = urls[0])
    phone_types: str = ""        # phones[].type параллельно phone
    booking_url: str = ""        # links/businessLinks type=booking
    # классификация
    category_classes: str = ""   # categories[].class (стабильные слаги)
    category_seonames: str = ""  # categories[].seoname
    rubric_ids: str = ""         # rubricIds
    chain_id: str = ""           # chain.id
    chain_name: str = ""         # chain.name (сеть/бренд)
    # рейтинги (разделены с reviews_count)
    rating_count: str = ""       # ratingData.ratingCount (кол-во оценок-звёзд)
    # часы работы / статус
    is_open_now: str = ""        # currentWorkingStatus.isOpenNow -> да/нет
    current_status_text: str = "" # currentWorkingStatus.text
    round_the_clock: str = ""    # круглосуточно (да/"")
    tz_offset: str = ""          # items[].tzOffset (сек)
    status: str = ""             # items[].status (open/closed/temporarily_closed)
    temporarily_closed: str = "" # да, если status == temporarily_closed
    # атрибуты
    description: str = ""        # items[].description
    features: str = ""           # features[] в виде "имя: значение; ..."
    price_average: str = ""      # средний чек из subtitleItems
    # медиа
    photos_count: str = ""       # photos.count
    photo_url_template: str = "" # photos.urlTemplate (avatars.mds.yandex.net, %s = размер)
    logo_url: str = ""           # businessImages.logo.urlTemplate
    panorama_id: str = ""        # panorama.id
    # транспорт
    metro: str = ""              # metro[] "имя (расстояние)"
    stops: str = ""              # stops[] "имя (расстояние)"
    # реклама / верификация / провенанс
    is_advert: str = ""          # да, если advert != null (платное размещение)
    inn_tax_id: str = ""         # advert.ordInfo.client.tin (только у рекламы)
    awards: str = ""             # awards, напр. "goodPlaceYear:2024"
    is_verified: str = ""        # да, если владелец подтверждён (sources/businessProperties)
    # конверт запроса
    total_result_count: str = "" # data.totalResultCount по запросу


FIELDNAMES = [f.name for f in fields(Organization)]


def _normalize_for_dedup(text: str) -> str:
    """Нормализовать строку для дедупликации.

    'ул. Ленина, 5' и 'улица Ленина, 5' → одинаковый ключ.
    """
    s = text.lower().strip()
    # Стандартные сокращения
    s = re.sub(r'\bулица\b', 'ул', s)
    s = re.sub(r'\bпроспект\b', 'пр-т', s)
    s = re.sub(r'\bпереулок\b', 'пер', s)
    s = re.sub(r'\bбульвар\b', 'б-р', s)
    s = re.sub(r'\bпроезд\b', 'пр-д', s)
    s = re.sub(r'\bнабережная\b', 'наб', s)
    s = re.sub(r'\bплощадь\b', 'пл', s)
    s = re.sub(r'\bмикрорайон\b', 'мкр', s)
    # Убираем точки, лишние пробелы, запятые
    s = s.replace('.', '').replace(',', ' ')
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def _dedup_key(org: Organization) -> str:
    """Ключ для дедупликации организации."""
    return f"{_normalize_for_dedup(org.name)}|{_normalize_for_dedup(org.address)}"


# ---------------------------------------------------------------------------
# Proxy rotation
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# CAPTCHA detection
# ---------------------------------------------------------------------------

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
    return not detect_captcha(page)


# ---------------------------------------------------------------------------
# Resume manager — продолжение с места остановки
# ---------------------------------------------------------------------------

class ResumeManager:
    """Менеджер докачки: загружает уже собранные данные из файла."""

    def __init__(self, path: Path | None = None):
        self._existing: dict[str, Organization] = {}
        self._completed_queries: set[str] = set()
        if path and path.exists():
            self._load(path)

    def _load(self, path: Path) -> None:
        """Загрузить существующий файл результатов."""
        suffix = path.suffix.lower()
        rows: list[dict] = []

        if suffix == ".csv":
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f, delimiter=";")
                    rows = list(reader)
            except Exception as exc:
                log.warning("Не удалось прочитать CSV для резюме: %s", exc)
                return

        elif suffix == ".xlsx":
            try:
                from openpyxl import load_workbook
                wb = load_workbook(path, read_only=True)
                ws = wb.active
                if ws is None:
                    return
                headers = [cell.value for cell in next(ws.iter_rows(min_row=1, max_row=1))]
                for row in ws.iter_rows(min_row=2, values_only=True):
                    row_dict = {h: (v or "") for h, v in zip(headers, row) if h}
                    rows.append(row_dict)
                wb.close()
            except Exception as exc:
                log.warning("Не удалось прочитать XLSX для резюме: %s", exc)
                return

        elif suffix == ".json":
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    rows = data
                elif isinstance(data, dict) and "organizations" in data:
                    rows = data["organizations"]
            except Exception as exc:
                log.warning("Не удалось прочитать JSON для резюме: %s", exc)
                return

        # Маппинг русских заголовков на английские поля
        ru_to_en = {v: k for k, v in HEADERS_RU.items()}

        for row in rows:
            # Нормализуем ключи (могут быть русские заголовки из xlsx)
            norm = {}
            for k, v in row.items():
                en_key = ru_to_en.get(k, k)
                norm[en_key] = str(v) if v else ""

            key = f"{_normalize_for_dedup(norm.get('name', ''))}|{_normalize_for_dedup(norm.get('address', ''))}"
            if key != "|":
                org = Organization(**{f: norm.get(f, "") for f in FIELDNAMES if f in norm})
                self._existing[key] = org
                q = norm.get("search_query", "")
                if q:
                    self._completed_queries.add(q)

        log.info(
            "Резюме: загружено %d существующих организаций, %d выполненных запросов",
            len(self._existing), len(self._completed_queries),
        )

    @property
    def existing_count(self) -> int:
        return len(self._existing)

    def is_known(self, name: str, address: str) -> bool:
        return f"{_normalize_for_dedup(name)}|{_normalize_for_dedup(address)}" in self._existing

    def is_query_done(self, query: str) -> bool:
        return query in self._completed_queries

    def existing_orgs(self) -> list[Organization]:
        return list(self._existing.values())

    def existing_keys(self) -> set[str]:
        return set(self._existing.keys())


# ---------------------------------------------------------------------------
# Config file support (YAML/JSON)
# ---------------------------------------------------------------------------

DEFAULT_CONFIG: dict[str, Any] = {
    "max_results": 500,
    "scroll_pause": 1.0,
    "headless": True,
    "detail": False,
    "api_intercept": False,
    "output": "results.xlsx",
    "proxy": None,
    "proxy_file": None,
    "city": None,
    "categories": None,
}


def load_config(path: str) -> dict[str, Any]:
    """Загрузить конфиг из YAML или JSON файла."""
    p = Path(path)
    if not p.exists():
        log.warning("Конфиг-файл не найден: %s", path)
        return {}

    text = p.read_text(encoding="utf-8")
    if p.suffix in (".yaml", ".yml"):
        try:
            import yaml
            return yaml.safe_load(text) or {}
        except ImportError:
            log.warning("PyYAML не установлен — пробую как JSON")
    try:
        return json.loads(text)
    except Exception as exc:
        log.warning("Ошибка чтения конфига %s: %s", path, exc)
        return {}


# ---------------------------------------------------------------------------
# Каталог категорий (аналог 2ГИС)
#
# Структура: группа → список поисковых запросов.
# При парсинге каждый запрос дополняется названием города.
# ---------------------------------------------------------------------------

CATEGORIES: dict[str, list[str]] = {
    "еда": [
        "рестораны", "кафе", "бары", "пиццерии", "суши-бары",
        "столовые", "фастфуд", "кофейни", "кондитерские", "пекарни",
        "шаурма", "бургерные", "доставка еды",
    ],
    "продукты": [
        "продуктовые магазины", "супермаркеты", "мясные магазины",
        "рыбные магазины", "овощи и фрукты", "молочные продукты",
        "алкогольные магазины", "кулинарии",
    ],
    "здоровье": [
        "аптеки", "больницы", "поликлиники", "стоматологии",
        "медицинские центры", "ветеринарные клиники", "лаборатории",
        "оптика", "косметология",
    ],
    "авто": [
        "автосервисы", "шиномонтаж", "автомойки", "автозапчасти",
        "АЗС", "автосалоны", "эвакуаторы", "парковки",
        "техосмотр", "автострахование",
    ],
    "красота": [
        "салоны красоты", "парикмахерские", "барбершопы",
        "маникюр", "массаж", "спа-салоны", "солярии", "тату-салоны",
    ],
    "покупки": [
        "торговые центры", "магазины одежды", "магазины обуви",
        "магазины электроники", "магазины мебели", "строительные магазины",
        "магазины цветов", "зоомагазины", "книжные магазины",
        "магазины подарков", "ювелирные магазины",
    ],
    "услуги": [
        "банки", "банкоматы", "нотариусы", "юридические услуги",
        "страховые компании", "фотостудии", "ателье", "химчистки",
        "ремонт телефонов", "ремонт бытовой техники", "клининг",
        "курьерские службы", "типографии",
    ],
    "образование": [
        "школы", "детские сады", "университеты", "колледжи",
        "языковые курсы", "автошколы", "репетиторы",
        "курсы программирования", "музыкальные школы",
    ],
    "спорт": [
        "фитнес-клубы", "тренажёрные залы", "бассейны",
        "спортивные магазины", "йога-студии", "танцевальные студии",
        "боксёрские клубы", "теннисные корты", "спортивные площадки",
    ],
    "развлечения": [
        "кинотеатры", "театры", "музеи", "парки развлечений",
        "боулинг", "бильярд", "караоке", "квесты",
        "ночные клубы", "концертные залы",
    ],
    "туризм": [
        "гостиницы", "хостелы", "турагентства", "достопримечательности",
        "экскурсии", "аренда автомобилей", "визовые центры",
    ],
    "транспорт": [
        "такси", "каршеринг", "автобусные станции",
        "железнодорожные вокзалы", "аэропорты", "грузоперевозки",
    ],
    "недвижимость": [
        "агентства недвижимости", "новостройки",
        "управляющие компании", "жилые комплексы",
    ],
    "дом": [
        "мебельные магазины", "сантехника", "электрика",
        "окна и двери", "кухни на заказ", "натяжные потолки",
        "кондиционеры", "отопление",
    ],
    "IT": [
        "компьютерные магазины", "ремонт компьютеров",
        "IT-компании", "интернет-провайдеры", "веб-студии",
    ],
}


def list_categories() -> None:
    """Вывести каталог категорий в консоль."""
    print("\n📂 Каталог категорий (аналог 2ГИС):\n")
    for group, items in CATEGORIES.items():
        print(f"  [{group}]")
        for item in items:
            print(f"    • {item}")
        print()
    print("Использование:")
    print('  python yandex_parser.py --city Алматы --category еда')
    print('  python yandex_parser.py --city Астана --category еда рестораны кафе')
    print('  python yandex_parser.py --city Шымкент --all-categories')


def resolve_categories(names: list[str]) -> list[str]:
    """Преобразовать названия групп/категорий в список поисковых запросов.

    Принимает как названия групп (еда, авто), так и конкретные запросы
    (рестораны, кафе). Если имя совпадает с группой — разворачивает все
    подкатегории. Иначе трактует как прямой поисковый запрос.
    """
    queries: list[str] = []
    for name in names:
        key = name.lower().strip()
        if key in CATEGORIES:
            queries.extend(CATEGORIES[key])
        else:
            queries.append(name.strip())
    return queries


# ---------------------------------------------------------------------------
# Selectors
#
# Яндекс.Карты периодически добавляет числовой префикс к классам
# (напр. "_1a2b3c_search-snippet-view"), поэтому используем
# [class*="..."] — поиск по подстроке класса, что устойчивее.
#
# Селекторы верифицированы по открытым парсерам:
#   - github.com/artemsteshenko/parser_maps
#   - github.com/chernyshov-dp/YMapsGrabber
#   - github.com/erdzhemadinov/PARSING_DISTRIBUTION_CENTRES
# ---------------------------------------------------------------------------

# -- Список результатов (левая панель) --
# Поле ввода поиска
SEARCH_INPUT_SEL = "[class*='search-form-view__input'] input, input.input__control"
# Кнопка поиска
SEARCH_BUTTON_SEL = "[class*='small-search-form-view__button'], [class*='search-form-view'] button[type='submit']"

# Контейнер прокрутки результатов
SCROLL_CONTAINER_SEL = "[class*='scroll__container']"
# Ползунок скроллбара (для определения наличия прокрутки)
SCROLLBAR_THUMB_SEL = "[class*='scroll__scrollbar-thumb']"

# Один сниппет организации в списке результатов
ITEM_SEL = "[class*='search-snippet-view']"
# Ссылка-оверлей на карточку организации
LINK_SEL = "[class*='search-snippet-view__link-overlay']"
# Кнопка «Показать ещё»
SHOW_MORE_SEL = "[class*='show-more'] button, [class*='search-list-view__more'] button"

# -- Поля внутри сниппета (список результатов) --
SNIPPET_TITLE_SEL = "[class*='search-business-snippet-view__title']"
SNIPPET_ADDRESS_SEL = "[class*='search-business-snippet-view__address']"
SNIPPET_CATEGORY_SEL = "[class*='search-business-snippet-view__categories']"
SNIPPET_HOURS_SEL = "[class*='search-business-snippet-view__open-hours']"
SNIPPET_RATING_SEL = "[class*='business-rating-badge-view__rating-text'], [class*='business-summary-rating-badge-view__rating-text']"
SNIPPET_REVIEWS_SEL = "[class*='business-rating-badge-view__rating-count']"

# -- Карточка организации (detail page) --
DETAIL_NAME_SEL = "h1[class*='orgpage-header-view__header'], h1[class*='card-title-view__title']"
DETAIL_ADDRESS_SEL = "[class*='business-contacts-view__address-link'], [class*='orgpage-header-view__address'], [class*='card-title-view__subtitle']"
DETAIL_PHONE_SEL = "[class*='card-phones-view__phone-number'], [class*='orgpage-phones-view__phone-number'], a[href^='tel:']"
DETAIL_WEBSITE_SEL = "[class*='business-urls-view__text'], [class*='card-feature-view__content'] a[href], [class*='orgpage-feature-view__content'] a[href]"
DETAIL_RATING_SEL = "[class*='business-summary-rating-badge-view__rating-text'], [class*='business-rating-badge-view__rating-text']"
DETAIL_REVIEWS_COUNT_SEL = "[class*='tabs-select-view__counter']"
DETAIL_HOURS_SEL = "meta[itemprop='openingHours'], [class*='business-working-status-view__text']"
DETAIL_CATEGORY_SEL = "[class*='business-categories-view__category'], [class*='breadcrumbs-view__text']"


# ---------------------------------------------------------------------------
# Selector auto-detection engine
#
# Яндекс периодически меняет DOM-структуру и обфусцирует классы.
# SelectorDetector анализирует живую страницу через JS и находит
# нужные элементы по эвристикам: schema.org microdata, ARIA,
# семантика HTML, текстовые паттерны, повторяющиеся структуры.
#
# Цепочка: кеш → hardcoded → auto-detect → fallback
# ---------------------------------------------------------------------------

SELECTORS_CACHE_FILE = Path("selectors_cache.json")

# Ключи, которые мы детектим
SELECTOR_KEYS = [
    "scroll_container", "item", "link",
    "snippet_title", "snippet_address", "snippet_category",
    "snippet_hours", "snippet_rating", "snippet_reviews",
    "detail_name", "detail_address", "detail_phone",
    "detail_website", "detail_rating", "detail_reviews_count",
    "detail_hours", "detail_category",
    "show_more",
]

# Маппинг ключ → hardcoded-селектор (для fallback)
_HARDCODED: dict[str, str] = {
    "scroll_container": SCROLL_CONTAINER_SEL,
    "item": ITEM_SEL,
    "link": LINK_SEL,
    "show_more": SHOW_MORE_SEL,
    "snippet_title": SNIPPET_TITLE_SEL,
    "snippet_address": SNIPPET_ADDRESS_SEL,
    "snippet_category": SNIPPET_CATEGORY_SEL,
    "snippet_hours": SNIPPET_HOURS_SEL,
    "snippet_rating": SNIPPET_RATING_SEL,
    "snippet_reviews": SNIPPET_REVIEWS_SEL,
    "detail_name": DETAIL_NAME_SEL,
    "detail_address": DETAIL_ADDRESS_SEL,
    "detail_phone": DETAIL_PHONE_SEL,
    "detail_website": DETAIL_WEBSITE_SEL,
    "detail_rating": DETAIL_RATING_SEL,
    "detail_reviews_count": DETAIL_REVIEWS_COUNT_SEL,
    "detail_hours": DETAIL_HOURS_SEL,
    "detail_category": DETAIL_CATEGORY_SEL,
}


class SelectorCache:
    """Кеш обнаруженных селекторов в JSON-файле."""

    def __init__(self, path: Path = SELECTORS_CACHE_FILE):
        self._path = path
        self._data: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
                log.info("Загружен кеш селекторов: %s (%d записей)", self._path, len(self._data))
            except Exception:
                self._data = {}

    def save(self) -> None:
        self._path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info("Кеш селекторов сохранён: %s", self._path)

    def get(self, key: str) -> str | None:
        return self._data.get(key)

    def set(self, key: str, value: str) -> None:
        self._data[key] = value

    def update(self, mapping: dict[str, str]) -> None:
        self._data.update(mapping)

    def all(self) -> dict[str, str]:
        return dict(self._data)


# --- JS-код для автодетекта, выполняется через page.evaluate() ---

_JS_DETECT_SNIPPETS = """() => {
    // Ищем повторяющиеся карточки в левой панели (sidebar)
    // Стратегия: найти контейнер со списком, в нём — повторяющиеся элементы

    // 1) Пробуем по известным паттернам подстроки класса
    const knownPatterns = [
        'search-snippet-view',
        'search-list-view__item',
        'serp-item',
        'search-result',
    ];
    for (const pat of knownPatterns) {
        const els = document.querySelectorAll(`[class*="${pat}"]`);
        if (els.length >= 3) {
            return { item: `[class*="${pat}"]`, count: els.length };
        }
    }

    // 2) Эвристика: ищем группы >3 однотипных элементов с ссылками внутри
    //    в левой трети экрана (sidebar Я.Карт)
    const sidebar = document.querySelector('[class*="sidebar"], [class*="panel"], aside')
        || document.body;
    const candidates = {};
    const allEls = sidebar.querySelectorAll('div, li, article, section');

    for (const el of allEls) {
        if (el.children.length < 2) continue;
        if (el.offsetWidth > window.innerWidth * 0.6) continue;
        const cl = Array.from(el.classList).find(c => c.length > 4) || '';
        if (!cl) continue;
        // Группируем по «базовому» классу (без цифровых префиксов)
        const base = cl.replace(/^_[a-f0-9]+_/i, '').replace(/^_+/, '');
        if (!base) continue;
        if (!candidates[base]) candidates[base] = { sel: '', count: 0 };
        candidates[base].count++;
        candidates[base].sel = `[class*="${base}"]`;
    }

    // Берём класс с наибольшим количеством повторений (>= 3)
    let best = null;
    for (const [base, info] of Object.entries(candidates)) {
        if (info.count >= 3 && (!best || info.count > best.count)) {
            best = info;
        }
    }
    if (best) return { item: best.sel, count: best.count };
    return null;
}"""


_JS_DETECT_SNIPPET_FIELDS = """(itemSel) => {
    const cards = document.querySelectorAll(itemSel);
    if (cards.length === 0) return {};

    const result = {};
    // Анализируем первые 3 карточки для надёжности
    const sample = Array.from(cards).slice(0, 3);

    // Собираем все уникальные классы потомков
    function findBySubstring(els, patterns) {
        for (const pat of patterns) {
            for (const el of els) {
                const matches = el.querySelectorAll(`[class*="${pat}"]`);
                if (matches.length > 0) return `[class*="${pat}"]`;
            }
        }
        return null;
    }

    // Название — самый крупный текст / заголовок
    result.snippet_title = findBySubstring(sample, [
        'snippet-view__title', 'business-snippet-view__title',
        'title-view__title', 'card-title',
    ]);
    if (!result.snippet_title) {
        // Fallback: ищем элемент с наибольшим font-size
        for (const card of sample) {
            let maxSize = 0, bestEl = null;
            for (const child of card.querySelectorAll('*')) {
                const fs = parseFloat(getComputedStyle(child).fontSize);
                const text = child.textContent.trim();
                if (fs > maxSize && text.length > 2 && text.length < 100) {
                    maxSize = fs;
                    bestEl = child;
                }
            }
            if (bestEl) {
                const cls = Array.from(bestEl.classList).find(c => c.length > 4);
                if (cls) { result.snippet_title = `[class*="${cls.replace(/^_[a-f0-9]+_/i, '')}"]`; break; }
            }
        }
    }

    // Адрес
    result.snippet_address = findBySubstring(sample, [
        'snippet-view__address', 'business-snippet-view__address',
        'address-view', 'subtitle',
    ]);

    // Категория
    result.snippet_category = findBySubstring(sample, [
        'snippet-view__categories', 'business-snippet-view__categories',
        'categories-view', 'category',
    ]);

    // Рейтинг (число)
    result.snippet_rating = findBySubstring(sample, [
        'rating-badge-view__rating-text', 'rating-text',
        'rating-value', 'rating__value',
    ]);

    // Отзывы (счётчик)
    result.snippet_reviews = findBySubstring(sample, [
        'rating-badge-view__rating-count', 'rating-count',
        'reviews-count',
    ]);

    // Часы работы
    result.snippet_hours = findBySubstring(sample, [
        'snippet-view__open-hours', 'open-hours',
        'working-status', 'hours',
    ]);

    // Ссылка-оверлей
    result.link = findBySubstring(sample, [
        'snippet-view__link-overlay', 'link-overlay',
        'snippet-view__link',
    ]);
    // Fallback: первый <a> с href, содержащим /org/
    if (!result.link) {
        for (const card of sample) {
            const a = card.querySelector('a[href*="/org/"]');
            if (a) {
                const cls = Array.from(a.classList).find(c => c.length > 4);
                if (cls) { result.link = `[class*="${cls.replace(/^_[a-f0-9]+_/i, '')}"]`; break; }
                else { result.link = 'a[href*="/org/"]'; break; }
            }
        }
    }

    // Убираем null-значения
    for (const k in result) {
        if (!result[k]) delete result[k];
    }
    return result;
}"""


_JS_DETECT_DETAIL_FIELDS = """() => {
    const result = {};

    function findSel(patterns) {
        for (const pat of patterns) {
            if (document.querySelector(`[class*="${pat}"]`)) return `[class*="${pat}"]`;
        }
        return null;
    }

    // Название — h1
    const h1 = document.querySelector('h1');
    if (h1) {
        const cls = Array.from(h1.classList).find(c => c.length > 4);
        result.detail_name = cls
            ? `h1[class*="${cls.replace(/^_[a-f0-9]+_/i, '')}"]`
            : 'h1';
    }

    // Адрес
    result.detail_address = findSel([
        'business-contacts-view__address', 'orgpage-header-view__address',
        'card-title-view__subtitle', 'address-link',
    ]);

    // Телефон — ищем a[href^="tel:"] или элементы с паттерном телефона
    const telLink = document.querySelector('a[href^="tel:"]');
    if (telLink) {
        const cls = Array.from(telLink.classList).find(c => c.length > 4);
        result.detail_phone = cls
            ? `[class*="${cls.replace(/^_[a-f0-9]+_/i, '')}"]`
            : 'a[href^="tel:"]';
    }
    if (!result.detail_phone) {
        result.detail_phone = findSel([
            'card-phones-view__phone-number', 'orgpage-phones-view__phone-number',
            'phone-number', 'phones-view',
        ]);
    }

    // Сайт — ссылка наружу (не yandex, не tel:)
    result.detail_website = findSel([
        'business-urls-view__text', 'card-feature-view__content',
        'orgpage-feature-view__content', 'urls-view',
    ]);
    if (!result.detail_website) {
        const links = document.querySelectorAll('a[href^="http"]');
        for (const a of links) {
            const href = a.getAttribute('href') || '';
            if (!href.includes('yandex') && !href.includes('google') && a.textContent.trim()) {
                const cls = Array.from(a.classList).find(c => c.length > 4);
                if (cls) {
                    result.detail_website = `[class*="${cls.replace(/^_[a-f0-9]+_/i, '')}"]`;
                    break;
                }
            }
        }
    }

    // Рейтинг
    result.detail_rating = findSel([
        'business-summary-rating-badge-view__rating-text',
        'business-rating-badge-view__rating-text',
        'rating-badge-view__rating-text', 'rating-text',
    ]);

    // Кол-во отзывов
    result.detail_reviews_count = findSel([
        'tabs-select-view__counter', 'rating-count', 'reviews-count',
    ]);

    // Часы работы — schema.org microdata или класс
    if (document.querySelector('meta[itemprop="openingHours"]')) {
        result.detail_hours = 'meta[itemprop="openingHours"]';
    }
    if (!result.detail_hours) {
        result.detail_hours = findSel([
            'business-working-status-view__text', 'working-status-view',
            'open-hours', 'working-hours',
        ]);
    }

    // Категория
    result.detail_category = findSel([
        'business-categories-view__category', 'breadcrumbs-view__text',
        'categories-view', 'category-view',
    ]);

    // Убираем null
    for (const k in result) {
        if (!result[k]) delete result[k];
    }
    return result;
}"""


_JS_DETECT_SCROLL = """() => {
    // 1) Известные паттерны
    const knownPatterns = ['scroll__container', 'scroll-container', 'scrollable'];
    for (const pat of knownPatterns) {
        const el = document.querySelector(`[class*="${pat}"]`);
        if (el && el.scrollHeight > el.clientHeight + 50) {
            return `[class*="${pat}"]`;
        }
    }

    // 2) Ищем любой div с overflow-y: auto/scroll и достаточной высотой в левой части
    const all = document.querySelectorAll('div, section, aside, ul');
    for (const el of all) {
        const style = getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        if ((style.overflowY === 'auto' || style.overflowY === 'scroll') &&
            el.scrollHeight > el.clientHeight + 100 &&
            el.clientHeight > 200 &&
            rect.left < window.innerWidth * 0.5) {
            if (el.id) return '#' + el.id;
            const cls = Array.from(el.classList).find(c => c.length > 4);
            if (cls) return `[class*="${cls.replace(/^_[a-f0-9]+_/i, '')}"]`;
            return el.tagName.toLowerCase() + '.' + Array.from(el.classList).join('.');
        }
    }
    return null;
}"""


_JS_DETECT_SHOW_MORE = """() => {
    // Кнопка «Показать ещё» / «Ещё» / «Show more»
    const patterns = ['show-more', 'search-list-view__more', 'load-more'];
    for (const pat of patterns) {
        const el = document.querySelector(`[class*="${pat}"] button, button[class*="${pat}"]`);
        if (el) {
            const cls = Array.from(el.parentElement.classList).find(c => c.length > 4) ||
                        Array.from(el.classList).find(c => c.length > 4);
            if (cls) return `[class*="${cls.replace(/^_[a-f0-9]+_/i, '')}"] button`;
        }
    }
    // Fallback: кнопка с текстом «ещё» или «показать»
    const buttons = document.querySelectorAll('button');
    for (const b of buttons) {
        const t = b.textContent.toLowerCase().trim();
        if (t.includes('ещё') || t.includes('показать') || t.includes('more')) {
            const cls = Array.from(b.classList).find(c => c.length > 4);
            if (cls) return `button[class*="${cls.replace(/^_[a-f0-9]+_/i, '')}"]`;
        }
    }
    return null;
}"""


def _probe_selector(page: Page, selector: str) -> bool:
    """Проверить, находит ли селектор хотя бы один элемент на странице."""
    try:
        return page.locator(selector).count() > 0
    except Exception:
        return False


def detect_selectors(page: Page, mode: str = "list") -> dict[str, str]:
    """Обнаружить актуальные CSS-селекторы на живой странице.

    Args:
        page: Playwright-страница с загруженными результатами.
        mode: 'list' — детектим селекторы списка/сниппетов,
              'detail' — детектим селекторы карточки организации.

    Returns:
        dict с ключами из SELECTOR_KEYS и значениями — CSS-селекторами.
    """
    found: dict[str, str] = {}

    if mode == "list":
        # Скролл-контейнер
        scroll_sel = page.evaluate(_JS_DETECT_SCROLL)
        if scroll_sel:
            found["scroll_container"] = scroll_sel
            log.debug("Auto-detect scroll_container: %s", scroll_sel)

        # Сниппеты (карточки результатов)
        snippet_info = page.evaluate(_JS_DETECT_SNIPPETS)
        if snippet_info and snippet_info.get("item"):
            item_sel = snippet_info["item"]
            found["item"] = item_sel
            log.debug("Auto-detect item: %s (%d шт)", item_sel, snippet_info.get("count", 0))

            # Поля внутри сниппетов
            fields = page.evaluate(_JS_DETECT_SNIPPET_FIELDS, item_sel)
            if fields:
                found.update(fields)
                log.debug("Auto-detect snippet fields: %s", list(fields.keys()))

        # Кнопка «Показать ещё»
        show_more = page.evaluate(_JS_DETECT_SHOW_MORE)
        if show_more:
            found["show_more"] = show_more
            log.debug("Auto-detect show_more: %s", show_more)

    elif mode == "detail":
        fields = page.evaluate(_JS_DETECT_DETAIL_FIELDS)
        if fields:
            found.update(fields)
            log.debug("Auto-detect detail fields: %s", list(fields.keys()))

    return found


class SelectorEngine:
    """Умный движок селекторов с цепочкой: кеш → hardcoded → auto-detect.

    Использование:
        engine = SelectorEngine()
        engine.probe_and_detect(page, mode='list')  # запуск на живой странице
        sel = engine.get('item')  # получить селектор
    """

    def __init__(self, cache_path: Path = SELECTORS_CACHE_FILE):
        self._cache = SelectorCache(cache_path)
        self._active: dict[str, str] = {}
        self._detected = False

    def get(self, key: str) -> str:
        """Получить селектор по ключу. Fallback: hardcoded."""
        return self._active.get(key) or _HARDCODED.get(key, "")

    def probe_and_detect(self, page: Page, mode: str = "list") -> None:
        """Проверить селекторы на живой странице и при необходимости передетектить.

        1. Проверяем кешированные селекторы
        2. Проверяем hardcoded-селекторы
        3. Если ключевые не работают — запускаем auto-detect
        4. Обновляем кеш
        """
        # Ключевые селекторы, без которых парсинг невозможен
        critical_keys = (
            ["item", "snippet_title"] if mode == "list"
            else ["detail_name"]
        )

        # Шаг 1: пробуем кеш
        cache_ok = True
        for key in critical_keys:
            cached = self._cache.get(key)
            if cached and _probe_selector(page, cached):
                self._active[key] = cached
            else:
                cache_ok = False

        if cache_ok:
            # Загружаем остальные из кеша, fallback на hardcoded
            for key in SELECTOR_KEYS:
                if key not in self._active:
                    cached = self._cache.get(key)
                    if cached:
                        self._active[key] = cached
                    elif key in _HARDCODED:
                        self._active[key] = _HARDCODED[key]
            log.info("Селекторы загружены из кеша (все критичные работают)")
            return

        # Шаг 2: пробуем hardcoded
        hardcoded_ok = True
        for key in critical_keys:
            hc = _HARDCODED.get(key, "")
            if hc and _probe_selector(page, hc):
                self._active[key] = hc
            else:
                hardcoded_ok = False

        if hardcoded_ok:
            for key in SELECTOR_KEYS:
                if key not in self._active and key in _HARDCODED:
                    self._active[key] = _HARDCODED[key]
            log.info("Используются hardcoded-селекторы (все критичные работают)")
            # Сохраняем работающие в кеш
            self._cache.update(self._active)
            self._cache.save()
            return

        # Шаг 3: auto-detect
        log.warning("Hardcoded-селекторы не работают — запускаю автодетект…")
        detected = detect_selectors(page, mode=mode)

        if detected:
            log.info("Автодетект нашёл %d селекторов: %s", len(detected), list(detected.keys()))
            self._active.update(detected)
            self._detected = True

            # Дополняем hardcoded-ами то, что не нашли
            for key in SELECTOR_KEYS:
                if key not in self._active and key in _HARDCODED:
                    self._active[key] = _HARDCODED[key]

            # Сохраняем в кеш
            self._cache.update(self._active)
            self._cache.save()
        else:
            log.error("Автодетект не смог найти селекторы — используем hardcoded как есть")
            self._active = dict(_HARDCODED)

    def detect_detail(self, page: Page) -> None:
        """Детектить селекторы карточки организации (detail page)."""
        # Проверяем критичные detail-селекторы
        critical = ["detail_phone", "detail_website"]
        all_ok = all(
            _probe_selector(page, self.get(k)) for k in critical if self.get(k)
        )

        if not all_ok:
            log.info("Detail-селекторы не работают — запускаю автодетект…")
            detected = detect_selectors(page, mode="detail")
            if detected:
                self._active.update(detected)
                self._cache.update(detected)
                self._cache.save()
                log.info("Detail автодетект: %s", list(detected.keys()))

    def report(self) -> str:
        """Отчёт о состоянии селекторов."""
        lines = ["Активные селекторы:"]
        for key in SELECTOR_KEYS:
            sel = self._active.get(key, "—")
            source = "cache" if self._cache.get(key) == sel else (
                "detected" if self._detected else "hardcoded"
            )
            lines.append(f"  {key:25s} [{source:9s}] {sel}")
        return "\n".join(lines)


# Глобальный экземпляр (инициализируется при первом запуске)
_selector_engine: SelectorEngine | None = None


def get_selector_engine() -> SelectorEngine:
    """Получить или создать глобальный SelectorEngine."""
    global _selector_engine
    if _selector_engine is None:
        _selector_engine = SelectorEngine()
    return _selector_engine


# ---------------------------------------------------------------------------
# Scroll helpers (используют SelectorEngine)
# ---------------------------------------------------------------------------

def _find_scroll_container(page: Page) -> str | None:
    """Найти скроллящийся контейнер с результатами поиска."""
    engine = get_selector_engine()
    sel = engine.get("scroll_container")
    if sel and _probe_selector(page, sel):
        return sel

    # Fallback: JS-поиск по overflow-y
    container_sel = page.evaluate(_JS_DETECT_SCROLL)
    return container_sel


def _human_scroll(page: Page, container_sel: str | None) -> None:
    """Плавный скролл с имитацией поведения человека.

    - Случайная дельта прокрутки (200–700 px)
    - Микро-шаги внутри одного скролла (3–6 шагов с паузами 50–150 мс)
    - Случайные движения мыши в зоне панели результатов
    - Иногда «задумывается» — длинная пауза
    - Иногда скроллит чуть вверх (как человек, вернувшийся посмотреть)
    """
    # Случайное движение мыши в области левой панели
    mouse_x = random.randint(150, 420)
    mouse_y = random.randint(200, 700)
    page.mouse.move(mouse_x, mouse_y)
    page.wait_for_timeout(random.randint(50, 200))

    # Общая дельта этого скролла
    total_delta = random.randint(200, 700)

    # Иногда (15%) скроллим немного вверх — как будто пересматриваем
    if random.random() < 0.15:
        up_delta = random.randint(50, 150)
        _do_scroll_step(page, container_sel, -up_delta)
        page.wait_for_timeout(random.randint(300, 800))

    # Разбиваем на микро-шаги
    steps = random.randint(3, 6)
    for i in range(steps):
        step_delta = total_delta // steps
        # Добавляем немного шума к каждому шагу
        step_delta += random.randint(-20, 20)
        step_delta = max(30, step_delta)

        _do_scroll_step(page, container_sel, step_delta)

        # Микро-пауза между шагами (50–150 мс)
        page.wait_for_timeout(random.randint(50, 150))

    # Иногда (10%) «задумываемся» — длинная пауза
    if random.random() < 0.10:
        think_ms = random.randint(1500, 3500)
        log.debug("Имитация паузы: %d мс", think_ms)
        page.wait_for_timeout(think_ms)


def _do_scroll_step(page: Page, container_sel: str | None, delta: int) -> None:
    """Один шаг прокрутки (через контейнер или mouse.wheel)."""
    if container_sel:
        loc = page.locator(container_sel).first
        if loc.count() > 0:
            loc.evaluate(f"el => el.scrollTop += {delta}")
            return

    # Fallback: колёсико мыши
    page.mouse.wheel(0, delta)


def _get_loaded_count(page: Page) -> int:
    """Вернуть количество подгруженных сниппетов."""
    engine = get_selector_engine()
    return page.locator(engine.get("item")).count()


def _click_show_more(page: Page) -> bool:
    """Нажать кнопку «Показать ещё», если она видна."""
    engine = get_selector_engine()
    sel = engine.get("show_more")
    if not sel:
        return False
    btn = page.locator(sel).first
    if btn.count() > 0 and btn.is_visible():
        btn.click()
        page.wait_for_timeout(1500)
        return True
    return False


# ---------------------------------------------------------------------------
# Streaming scroll + parse: скроллим и парсим на лету
# ---------------------------------------------------------------------------

def scroll_and_parse(
    page: Page,
    max_results: int,
    scroll_pause: float = 1.0,
    on_org: Any = None,
) -> list[Organization]:
    """Скроллить и парсить сниппеты на лету по мере появления.

    Вместо двухэтапного подхода (сначала весь скролл, потом парсинг)
    каждый новый сниппет парсится сразу при появлении.

    Args:
        page: Playwright-страница с результатами поиска.
        max_results: Максимум организаций.
        scroll_pause: Базовая пауза между скроллами (рандомизируется).
        on_org: Callback(org, index) — вызывается при каждой новой организации.

    Returns:
        Список собранных Organization.
    """
    engine = get_selector_engine()
    item_sel = engine.get("item")
    container_sel = _find_scroll_container(page)

    if container_sel:
        log.info("Скролл-контейнер: %s", container_sel)
    else:
        log.warning("Скролл-контейнер не найден, используем mouse.wheel")

    orgs: list[Organization] = []
    parsed_indices: set[int] = set()
    seen_keys: set[str] = set()   # дедуп по содержимому (имя+адрес)
    stale_rounds = 0
    max_stale = 10

    # Прогресс-бар (если tqdm установлен)
    pbar = None
    if HAS_TQDM:
        pbar = tqdm(total=max_results, desc="Сбор организаций", unit="орг")

    while True:
        cur_count = page.locator(item_sel).count()

        # Парсим новые сниппеты, которые появились после скролла
        new_parsed = 0
        for i in range(cur_count):
            if i in parsed_indices:
                continue
            if len(orgs) >= max_results:
                break
            try:
                org = parse_snippet(page, i)
                # Дедуп по содержимому: селектор карточки может матчить
                # вложенные обёртки (search-snippet-view__body/__content и т.п.),
                # из-за чего одна организация парсится 2–3 раза — отсекаем дубли.
                if org.name:
                    key = _dedup_key(org)
                    if key not in seen_keys:
                        seen_keys.add(key)
                        orgs.append(org)
                        if on_org:
                            on_org(org, len(orgs))
                        new_parsed += 1
                        if pbar:
                            pbar.update(1)
            except Exception as exc:
                log.debug("Ошибка парсинга сниппета #%d: %s", i, exc)
            parsed_indices.add(i)

        if new_parsed > 0:
            if not pbar:
                log.info("Собрано: %d организаций (новых: +%d)", len(orgs), new_parsed)
            stale_rounds = 0
        else:
            # Новых сниппетов нет — пробуем «Показать ещё»
            if not _click_show_more(page):
                stale_rounds += 1
            else:
                stale_rounds = 0

        # Проверяем лимиты
        if len(orgs) >= max_results:
            log.info("Достигнут лимит: %d / %d", len(orgs), max_results)
            break

        if stale_rounds >= max_stale:
            log.info("Новые результаты не появляются, завершаем (всего %d)", len(orgs))
            break

        # Плавный человеческий скролл
        _human_scroll(page, container_sel)

        # Рандомизированная пауза (±30% от базовой)
        jitter = scroll_pause * random.uniform(0.7, 1.3)
        page.wait_for_timeout(int(jitter * 1000))

    if pbar:
        pbar.close()

    return orgs


# ---------------------------------------------------------------------------
# Extracting from snippet list (DOM)
# ---------------------------------------------------------------------------

def _safe_text(loc) -> str:
    """Извлечь текст из локатора, вернуть '' если элемент отсутствует."""
    if loc.count() > 0:
        return loc.first.inner_text().strip()
    return ""


def parse_snippet(page: Page, index: int) -> Organization:
    """Извлечь данные из одного сниппета по индексу."""
    engine = get_selector_engine()
    card = page.locator(engine.get("item")).nth(index)
    org = Organization()

    # Название
    org.name = _safe_text(card.locator(engine.get("snippet_title")))

    # Адрес
    sel = engine.get("snippet_address")
    if sel:
        org.address = _safe_text(card.locator(sel))

    # Категория
    sel = engine.get("snippet_category")
    if sel:
        org.category = _safe_text(card.locator(sel))

    # Рейтинг
    sel = engine.get("snippet_rating")
    if sel:
        org.rating = _safe_text(card.locator(sel))

    # Количество отзывов
    sel = engine.get("snippet_reviews")
    if sel:
        raw_reviews = _safe_text(card.locator(sel))
        if raw_reviews:
            org.reviews_count = re.sub(r"[^\d]", "", raw_reviews)

    # Часы работы
    sel = engine.get("snippet_hours")
    if sel:
        org.working_hours = _safe_text(card.locator(sel))

    # Ссылка на карточку
    sel = engine.get("link")
    if sel:
        link = card.locator(sel).first
        if link.count() > 0:
            org.yandex_url = link.get_attribute("href") or ""

    # ID/slug организации из URL (/maps/org/<slug>/<id>/)
    seoname, org_id = _org_ids_from_url(org.yandex_url)
    org.seoname, org.org_id = seoname, org_id

    return org


# ---------------------------------------------------------------------------
# Detail page parsing (phone, website, etc.)
# ---------------------------------------------------------------------------

def enrich_from_detail(page: Page, org: Organization, _detail_detected: list[bool] | None = None) -> Organization:
    """Открыть карточку организации и дополнить данные."""
    if not org.yandex_url:
        return org

    engine = get_selector_engine()
    url = org.yandex_url
    if url.startswith("/"):
        url = f"{base_url()}{url}"

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(2500)

        # При первом открытии detail-страницы — детектим селекторы
        if _detail_detected is not None and not _detail_detected[0]:
            engine.detect_detail(page)
            _detail_detected[0] = True

        # Телефон
        sel = engine.get("detail_phone")
        if sel:
            phone_loc = page.locator(sel).first
            if phone_loc.count() > 0:
                org.phone = phone_loc.inner_text().strip()
                if not org.phone:
                    href = phone_loc.get_attribute("href") or ""
                    if href.startswith("tel:"):
                        org.phone = href[4:]

        # Сайт
        sel = engine.get("detail_website")
        if sel:
            site_loc = page.locator(sel).first
            if site_loc.count() > 0:
                org.website = site_loc.inner_text().strip()
                if not org.website:
                    org.website = site_loc.get_attribute("href") or ""

        # Адрес (если не извлекли из сниппета)
        if not org.address:
            sel = engine.get("detail_address")
            if sel:
                org.address = _safe_text(page.locator(sel))

        # Рейтинг (если не извлекли из сниппета)
        if not org.rating:
            sel = engine.get("detail_rating")
            if sel:
                org.rating = _safe_text(page.locator(sel))

        # Количество отзывов (если не извлекли)
        if not org.reviews_count:
            sel = engine.get("detail_reviews_count")
            if sel:
                raw = _safe_text(page.locator(sel))
                if raw:
                    org.reviews_count = re.sub(r"[^\d]", "", raw)

        # Часы работы
        if not org.working_hours:
            sel = engine.get("detail_hours")
            if sel:
                if "meta[itemprop" in sel:
                    hours_meta = page.locator(sel)
                    if hours_meta.count() > 0:
                        parts = []
                        for i in range(hours_meta.count()):
                            val = hours_meta.nth(i).get_attribute("content") or ""
                            if val:
                                parts.append(val)
                        org.working_hours = "; ".join(parts)
                else:
                    org.working_hours = _safe_text(page.locator(sel))

            # Fallback на meta-тег
            if not org.working_hours:
                hours_meta = page.locator("meta[itemprop='openingHours']")
                if hours_meta.count() > 0:
                    parts = []
                    for i in range(hours_meta.count()):
                        val = hours_meta.nth(i).get_attribute("content") or ""
                        if val:
                            parts.append(val)
                    org.working_hours = "; ".join(parts)

        # Категория
        if not org.category:
            sel = engine.get("detail_category")
            if sel:
                org.category = _safe_text(page.locator(sel))

        # Координаты — из URL (ll=lon,lat) или meta-тегов
        if not org.latitude:
            coords = _extract_coords_from_url(page.url)
            if coords:
                org.latitude, org.longitude = coords[0], coords[1]
            else:
                coords = page.evaluate("""() => {
                    // Из meta-тегов
                    const lat = document.querySelector('meta[itemprop="latitude"]');
                    const lng = document.querySelector('meta[itemprop="longitude"]');
                    if (lat && lng) return [lat.content, lng.content];
                    // Из JSON-LD
                    const scripts = document.querySelectorAll('script[type="application/ld+json"]');
                    for (const s of scripts) {
                        try {
                            const d = JSON.parse(s.textContent);
                            const geo = d.geo || (d.address && d.address.geo);
                            if (geo) return [String(geo.latitude), String(geo.longitude)];
                        } catch(e) {}
                    }
                    return null;
                }""")
                if coords:
                    org.latitude, org.longitude = coords[0], coords[1]

        # Email
        if not org.email:
            emails = page.evaluate("""() => {
                const links = document.querySelectorAll('a[href^="mailto:"]');
                return Array.from(links).map(a => a.href.replace('mailto:', '')).filter(Boolean);
            }""")
            if emails:
                org.email = "; ".join(emails)

        # Соцсети (VK, Telegram, Instagram, Facebook, OK, YouTube, Twitter/X)
        if not org.social_links:
            socials = page.evaluate("""() => {
                const patterns = [
                    'vk.com', 't.me', 'telegram', 'instagram.com',
                    'facebook.com', 'fb.com', 'ok.ru', 'youtube.com',
                    'twitter.com', 'x.com', 'tiktok.com',
                ];
                const links = document.querySelectorAll('a[href]');
                const found = [];
                for (const a of links) {
                    const href = a.getAttribute('href') || '';
                    for (const pat of patterns) {
                        if (href.includes(pat) && !found.includes(href)) {
                            found.push(href);
                            break;
                        }
                    }
                }
                return found;
            }""")
            if socials:
                org.social_links = "; ".join(socials)

        # ID/slug организации из финального URL карточки
        if not org.org_id:
            seoname, org_id = _org_ids_from_url(page.url)
            if org_id:
                org.org_id = org_id
                org.seoname = org.seoname or seoname

        # Описание — из meta-тегов (og:description / description), если пусто
        if not org.description:
            desc = page.evaluate("""() => {
                const el = document.querySelector('meta[property="og:description"], meta[name="description"]');
                return el ? (el.getAttribute('content') || '') : '';
            }""")
            if desc:
                org.description = desc.strip()

    except Exception as exc:
        log.warning("Не удалось открыть карточку %s: %s", org.name, exc)

    return org


def _extract_coords_from_url(url: str) -> tuple[str, str] | None:
    """Извлечь координаты из URL Яндекс.Карт (ll=lon,lat или pt=lon,lat)."""
    for param in ("ll", "pt"):
        match = re.search(rf'{param}=([\d.]+)%2C([\d.]+)|{param}=([\d.]+),([\d.]+)', url)
        if match:
            lon = match.group(1) or match.group(3)
            lat = match.group(2) or match.group(4)
            return (lat, lon)
    return None


def _org_ids_from_url(url: str) -> tuple[str, str]:
    """Извлечь (seoname, org_id) из URL карточки: /maps/org/<slug>/<id>/.

    Работает для yandex.kz / yandex.com / yandex.ru и относительных ссылок.
    """
    if not url:
        return "", ""
    m = re.search(r'/org/([^/]+)/(\d+)', url)
    if m:
        return m.group(1), m.group(2)
    # Формат без slug: /org/<id>
    m = re.search(r'/org/(\d+)', url)
    if m:
        return "", m.group(1)
    return "", ""


# ---------------------------------------------------------------------------
# API Intercept mode
#
# Яндекс.Карты делают XHR-запросы к внутренним API при скролле.
# Мы перехватываем ответы и извлекаем JSON напрямую — это надёжнее,
# чем парсить DOM.
# ---------------------------------------------------------------------------

def _s(val: Any) -> str:
    """Безопасно привести значение к строке ('' для None)."""
    if val is None:
        return ""
    if isinstance(val, bool):
        return "да" if val else "нет"
    return str(val)


def _join_unique(values) -> str:
    """Склеить непустые уникальные значения через '; ' (порядок сохраняется)."""
    seen: dict[str, None] = {}
    for v in values:
        v = (v or "").strip() if isinstance(v, str) else _s(v)
        if v and v not in seen:
            seen[v] = None
    return "; ".join(seen)


SOCIAL_PATTERNS = (
    "vk.com", "t.me", "telegram", "wa.me", "whatsapp", "instagram",
    "facebook", "fb.com", "ok.ru", "youtube", "twitter", "x.com",
    "tiktok", "viber",
)


def _find_api_items(data: dict) -> list[dict]:
    """Найти массив организаций в JSON-конверте ответа (все известные формы)."""
    if not isinstance(data, dict):
        return []
    inner = data.get("data") if isinstance(data.get("data"), dict) else {}
    # 1) прямые массивы на верхнем уровне и в data
    for holder in (data, inner):
        for key in ("items", "results", "features"):
            arr = holder.get(key)
            if isinstance(arr, list) and arr:
                return arr
    # 2) SSR-конверт: stack[0].results.items
    stack = data.get("stack") or inner.get("stack")
    if isinstance(stack, list):
        for frame in stack:
            if isinstance(frame, dict):
                res = frame.get("results")
                if isinstance(res, dict) and isinstance(res.get("items"), list):
                    return res["items"]
    return []


def _extract_total_count(data: dict) -> str:
    """Общее число результатов по запросу (для стоп-условия и как поле)."""
    inner = data.get("data") if isinstance(data.get("data"), dict) else {}
    for holder in (data, inner):
        for key in ("totalResultCount", "total", "found", "count"):
            v = holder.get(key)
            if isinstance(v, (int, str)) and _s(v):
                return _s(v)
    return ""


def _org_from_item(feat: dict, total_count: str = "") -> Organization | None:
    """Собрать Organization из одного элемента API.

    Поддерживает два формата:
      • внутренний camelCase (yandex.kz/maps/api/search → data.items[])
      • публичный GeoJSON (search-maps.yandex.ru → features[].properties.CompanyMetaData)
    Читаем сначала внутренние поля (props), затем — публичные (company) как fallback.
    """
    if not isinstance(feat, dict):
        return None
    props = feat.get("properties", feat)
    if not isinstance(props, dict):
        return None
    company = props.get("CompanyMetaData") or props.get("companyMetaData") or {}

    # Отсекаем не-организации (топонимы, рубрики, рекламные строки) — только для внутреннего формата
    itype = props.get("type")
    if not company and itype and itype != "business":
        return None

    org = Organization()

    # --- название / короткое / юр.лицо ---
    title = props.get("title")
    if title:
        org.name = title
        legal = props.get("name", "")
        if legal and legal != title:
            org.legal_name = legal
    else:
        org.name = company.get("name") or props.get("name", "")
    org.short_name = props.get("shortTitle", "")

    # --- идентификаторы ---
    org.org_id = _s(props.get("id") or props.get("oid")
                    or props.get("businessId") or company.get("id"))
    org.seoname = props.get("seoname", "")
    org.org_uri = props.get("uri", "")
    org.geo_id = _s(props.get("geoId"))

    # --- адрес ---
    org.address = props.get("address", "") or company.get("address", "")
    org.full_address = props.get("fullAddress", "") or company.get("address", "")
    org.additional_address = props.get("additionalAddress", "")
    org.country = props.get("country", "")
    ca = props.get("compositeAddress") or {}
    if isinstance(ca, dict):
        org.region_name = _s(ca.get("region"))
        org.locality = _s(ca.get("locality"))
        org.district = _s(ca.get("district"))
        org.street = _s(ca.get("street"))
        org.house = _s(ca.get("house"))
        org.postal_code = _s(ca.get("postalCode"))
    # Компоненты адреса из публичного GeoJSON (Address.Components[])
    addr_meta = company.get("address") if isinstance(company.get("address"), dict) else {}
    comps = (addr_meta.get("Components") if isinstance(addr_meta, dict) else None) or []
    for c in comps:
        if not isinstance(c, dict):
            continue
        kinds = c.get("kind") or c.get("kinds") or []
        kinds = kinds if isinstance(kinds, list) else [kinds]
        name = c.get("name", "")
        if "locality" in kinds and not org.locality:
            org.locality = name
        elif ("province" in kinds or "area" in kinds) and not org.region_name:
            org.region_name = name
        elif "street" in kinds and not org.street:
            org.street = name
        elif "house" in kinds and not org.house:
            org.house = name
        elif "district" in kinds and not org.district:
            org.district = name

    # --- координаты: [lon, lat] (порядок GeoJSON) ---
    coords = props.get("coordinates")
    if not (isinstance(coords, list) and len(coords) >= 2):
        coords = (feat.get("geometry") or {}).get("coordinates")
    if isinstance(coords, list) and len(coords) >= 2:
        org.longitude, org.latitude = _s(coords[0]), _s(coords[1])
    dc = props.get("displayCoordinates")
    if isinstance(dc, list) and len(dc) >= 2:
        org.display_longitude, org.display_latitude = _s(dc[0]), _s(dc[1])

    # --- телефоны ---
    phones = props.get("phones") or company.get("Phones") or company.get("phones") or []
    nums, types = [], []
    for ph in phones:
        if not isinstance(ph, dict):
            continue
        num = ph.get("number") or ph.get("formatted") or ph.get("value", "")
        if num:
            nums.append(num)
            types.append(ph.get("type", ""))
    org.phone = _join_unique(nums)
    org.phone_types = _join_unique(types)

    # --- сайты ---
    urls = props.get("urls")
    if isinstance(urls, list) and urls:
        org.website = urls[0] if isinstance(urls[0], str) else _s(urls[0])
        org.websites_all = _join_unique(urls)
    else:
        url_obj = company.get("url") or company.get("Url") or ""
        org.website = url_obj if isinstance(url_obj, str) else _s(url_obj.get("value") if isinstance(url_obj, dict) else "")

    # --- типизированные ссылки: email / booking / соцсети ---
    links = props.get("links") or company.get("Links") or company.get("links") or []
    emails, booking, socials = [], [], []
    for lk in links:
        if isinstance(lk, dict):
            t = (lk.get("type") or "").lower()
            href = lk.get("href") or lk.get("aref") or lk.get("url") or ""
        else:
            t, href = "", str(lk)
        if not href:
            continue
        if t == "email" or href.startswith("mailto:"):
            emails.append(href.replace("mailto:", ""))
        elif t == "booking":
            booking.append(href)
        elif any(p in href for p in SOCIAL_PATTERNS):
            socials.append(href)
    # Явный блок соцсетей внутреннего формата
    for s in (props.get("socialLinks") or []):
        if isinstance(s, dict) and s.get("href"):
            label = s.get("type", "")
            socials.append(f"{label}:{s['href']}".strip(":"))
    # Публичные Emails[]
    for e in (company.get("Emails") or company.get("emails") or []):
        emails.append(e.get("value", "") if isinstance(e, dict) else str(e))
    org.email = _join_unique(emails)
    org.booking_url = _join_unique(booking)
    org.social_links = _join_unique(socials)

    # --- категории / рубрики / сеть ---
    cats = props.get("categories") or company.get("Categories") or company.get("categories") or []
    org.category = ", ".join(c.get("name", "") for c in cats if isinstance(c, dict) and c.get("name"))
    org.category_classes = _join_unique(c.get("class", "") for c in cats if isinstance(c, dict))
    org.category_seonames = _join_unique(c.get("seoname", "") for c in cats if isinstance(c, dict))
    org.rubric_ids = _join_unique(_s(r) for r in (props.get("rubricIds") or []))
    chain = props.get("chain") or {}
    if isinstance(chain, dict):
        org.chain_id = _s(chain.get("id"))
        org.chain_name = _s(chain.get("name"))

    # --- рейтинг / отзывы ---
    rd = props.get("ratingData") or {}
    if isinstance(rd, dict) and rd:
        org.rating = _s(rd.get("ratingValue"))
        org.rating_count = _s(rd.get("ratingCount"))
        org.reviews_count = _s(rd.get("reviewCount"))
    if not org.rating:
        rating_val = company.get("rating") if isinstance(company.get("rating"), dict) else {}
        if isinstance(rating_val, dict) and rating_val:
            org.rating = _s(rating_val.get("value") or rating_val.get("score"))
            org.reviews_count = org.reviews_count or _s(rating_val.get("ratings") or rating_val.get("count"))
    if not org.rating:
        for key in ("rating", "Rating", "score"):
            v = props.get(key) or company.get(key)
            if v and not isinstance(v, dict):
                org.rating = _s(v)
                break
    if not org.reviews_count:
        for key in ("reviewCount", "ratingCount", "totalRatings"):
            v = props.get(key) or company.get(key)
            if v:
                org.reviews_count = _s(v)
                break

    # --- часы работы / статус ---
    org.working_hours = props.get("workingTimeText", "")
    if not org.working_hours:
        hours = company.get("Hours") or company.get("hours") or {}
        if isinstance(hours, dict):
            org.working_hours = hours.get("text", "")
    cws = props.get("currentWorkingStatus") or {}
    if isinstance(cws, dict):
        if "isOpenNow" in cws:
            org.is_open_now = "да" if cws.get("isOpenNow") else "нет"
        org.current_status_text = _s(cws.get("text"))
    org.round_the_clock = "да" if "круглосуточно" in org.working_hours.lower() else ""
    org.tz_offset = _s(props.get("tzOffset"))
    org.status = _s(props.get("status"))
    org.temporarily_closed = "да" if props.get("status") == "temporarily_closed" else ""

    # --- описание / особенности / цены ---
    org.description = props.get("description", "") or company.get("description", "")
    feats = props.get("features") or company.get("Features") or []
    parts = []
    for f in feats:
        if not isinstance(f, dict):
            continue
        n = f.get("name", "")
        v = f.get("value")
        if isinstance(v, bool):
            parts.append(n if v else f"{n}: нет")
        elif isinstance(v, list):
            vv = ", ".join(_s(x.get("name") if isinstance(x, dict) else x) for x in v)
            parts.append(f"{n}: {vv}" if vv else n)
        elif v not in (None, ""):
            parts.append(f"{n}: {_s(v)}")
        elif n:
            parts.append(n)
    org.features = _join_unique(parts)
    for si in (props.get("subtitleItems") or []):
        if isinstance(si, dict) and si.get("type") in ("goods", "price"):
            for pr in (si.get("property") or []):
                if isinstance(pr, dict) and pr.get("key") == "price":
                    org.price_average = _s(pr.get("value"))
                    break
            if org.price_average:
                break

    # --- медиа ---
    photos = props.get("photos") or {}
    if isinstance(photos, dict):
        org.photos_count = _s(photos.get("count"))
        org.photo_url_template = _s(photos.get("urlTemplate"))
    bimg = props.get("businessImages") or {}
    if isinstance(bimg, dict):
        logo = bimg.get("logo") or {}
        org.logo_url = _s(logo.get("urlTemplate")) if isinstance(logo, dict) else ""
    pano = props.get("panorama") or {}
    if isinstance(pano, dict):
        org.panorama_id = _s(pano.get("id"))

    # --- транспорт ---
    org.metro = _join_unique(
        f"{m.get('name', '')} ({m.get('distance', '')})".strip()
        for m in (props.get("metro") or []) if isinstance(m, dict) and m.get("name")
    )
    org.stops = _join_unique(
        f"{s.get('name', '')} ({s.get('distance', '')})".strip()
        for s in (props.get("stops") or []) if isinstance(s, dict) and s.get("name")
    )

    # --- реклама / верификация ---
    advert = props.get("advert")
    org.is_advert = "да" if advert else ""
    if isinstance(advert, dict):
        client = ((advert.get("ordInfo") or {}).get("client")) or {}
        org.inn_tax_id = _s(client.get("tin"))
    aw = props.get("awards")
    if isinstance(aw, dict):
        org.awards = _join_unique(f"{k}:{_s(v)}" for k, v in aw.items())
    srcs = props.get("sources") or []
    owner_src = any(isinstance(s, dict) and s.get("type") == "owner" for s in srcs)
    org.is_verified = "да" if (owner_src or props.get("businessProperties") or props.get("verified")) else ""

    # --- ссылка на карточку + всего найдено ---
    if org.seoname and org.org_id:
        org.yandex_url = org_url(org.seoname, org.org_id)
    else:
        org.yandex_url = props.get("uri") or props.get("url", "")
    org.total_result_count = total_count

    return org if org.name else None


def _extract_orgs_from_api_response(data: dict) -> list[Organization]:
    """Извлечь организации из JSON-ответа внутреннего/публичного API Яндекс.Карт."""
    total = _extract_total_count(data)
    orgs: list[Organization] = []
    for feat in _find_api_items(data):
        org = _org_from_item(feat, total)
        if org is not None:
            orgs.append(org)
    return orgs


def run_api_intercept(
    page: Page,
    max_results: int,
    scroll_pause: float,
) -> list[Organization]:
    """Скроллить и перехватывать JSON из XHR-ответов."""
    all_orgs: list[Organization] = []
    seen_names: set[str] = set()

    def on_response(response: Response) -> None:
        url = response.url
        # Ловим запросы к API поиска/бизнесов
        api_patterns = [
            "/maps/api/search",
            "/maps/api/business",
            "searchBusinesses",
            "/search/",
            "csrfToken",  # skip
        ]
        is_api = any(p in url for p in api_patterns[:4])
        if not is_api:
            return
        if response.status != 200:
            return

        try:
            body = response.json()
        except Exception:
            return

        orgs = _extract_orgs_from_api_response(body)
        for org in orgs:
            key = _dedup_key(org)
            if key not in seen_names:
                seen_names.add(key)
                all_orgs.append(org)

        if orgs:
            log.info("API перехвачено: +%d (всего %d)", len(orgs), len(all_orgs))

    page.on("response", on_response)

    container_sel = _find_scroll_container(page)
    stale_rounds = 0

    pbar = None
    if HAS_TQDM:
        pbar = tqdm(total=max_results, desc="API-перехват", unit="орг")

    while len(all_orgs) < max_results:
        prev = len(all_orgs)
        _human_scroll(page, container_sel)
        _click_show_more(page)

        # Рандомизированная пауза
        jitter = scroll_pause * random.uniform(0.7, 1.3)
        page.wait_for_timeout(int(jitter * 1000))

        new_count = len(all_orgs) - prev
        if new_count > 0:
            stale_rounds = 0
            if pbar:
                pbar.update(new_count)
        else:
            stale_rounds += 1

        if stale_rounds >= 12:
            log.info("API: новые данные не поступают, завершаем (всего %d)", len(all_orgs))
            break

    if pbar:
        pbar.close()

    page.remove_listener("response", on_response)
    return all_orgs[:max_results]


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def save_csv(orgs: list[Organization], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, delimiter=";")
        writer.writeheader()
        for org in orgs:
            writer.writerow(asdict(org))
    log.info("CSV сохранён: %s (%d записей)", path, len(orgs))


def save_json(orgs: list[Organization], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "total": len(orgs),
        "organizations": [asdict(org) for org in orgs],
    }
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("JSON сохранён: %s (%d записей)", path, len(orgs))


HEADERS_RU = {
    "name": "Название",
    "address": "Адрес",
    "phone": "Телефон",
    "website": "Сайт",
    "rating": "Рейтинг",
    "reviews_count": "Отзывы",
    "category": "Категория",
    "working_hours": "Часы работы",
    "latitude": "Широта",
    "longitude": "Долгота",
    "email": "Email",
    "social_links": "Соцсети",
    "search_query": "Поисковый запрос",
    "yandex_url": "Ссылка",
    # расширенные поля
    "org_id": "ID организации",
    "seoname": "Slug",
    "org_uri": "URI",
    "short_name": "Короткое название",
    "legal_name": "Юр. лицо",
    "full_address": "Полный адрес",
    "additional_address": "Уточнение адреса",
    "postal_code": "Индекс",
    "country": "Страна",
    "region_name": "Регион",
    "locality": "Город",
    "district": "Район",
    "street": "Улица",
    "house": "Дом",
    "geo_id": "Гео ID",
    "display_latitude": "Широта (метка)",
    "display_longitude": "Долгота (метка)",
    "websites_all": "Все сайты",
    "phone_types": "Типы телефонов",
    "booking_url": "Бронирование",
    "category_classes": "Классы рубрик",
    "category_seonames": "Slug рубрик",
    "rubric_ids": "ID рубрик",
    "chain_id": "ID сети",
    "chain_name": "Сеть",
    "rating_count": "Оценки (кол-во)",
    "is_open_now": "Открыто сейчас",
    "current_status_text": "Статус работы",
    "round_the_clock": "Круглосуточно",
    "tz_offset": "Часовой пояс (сек)",
    "status": "Статус",
    "temporarily_closed": "Временно закрыто",
    "description": "Описание",
    "features": "Особенности",
    "price_average": "Средний чек",
    "photos_count": "Фото (кол-во)",
    "photo_url_template": "Шаблон URL фото",
    "logo_url": "Логотип",
    "panorama_id": "Панорама",
    "metro": "Метро",
    "stops": "Остановки",
    "is_advert": "Реклама",
    "inn_tax_id": "ИНН (реклама)",
    "awards": "Награды",
    "is_verified": "Подтверждён владельцем",
    "total_result_count": "Всего найдено",
}


def _write_sheet(ws, orgs: list[Organization]) -> None:
    """Записать организации на один лист Excel."""
    from openpyxl.styles import Font

    cols = FIELDNAMES

    for col_idx, field in enumerate(cols, 1):
        cell = ws.cell(row=1, column=col_idx, value=HEADERS_RU.get(field, field))
        cell.font = Font(bold=True)

    for row_idx, org in enumerate(orgs, 2):
        d = asdict(org)
        for col_idx, field in enumerate(cols, 1):
            ws.cell(row=row_idx, column=col_idx, value=d.get(field, ""))

    for col in ws.columns:
        max_len = max((len(str(c.value or "")) for c in col), default=10)
        ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 60)


def save_xlsx(orgs: list[Organization], path: Path) -> None:
    try:
        from openpyxl import Workbook
    except ImportError:
        log.warning("openpyxl не установлен — сохраняю в CSV")
        save_csv(orgs, path.with_suffix(".csv"))
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "Яндекс.Карты"
    _write_sheet(ws, orgs)
    wb.save(path)
    log.info("XLSX сохранён: %s (%d записей)", path, len(orgs))


def save_xlsx_by_categories(
    results: dict[str, list[Organization]],
    path: Path,
) -> None:
    """Сохранить результаты по категориям: отдельный лист на каждую + сводный."""
    try:
        from openpyxl import Workbook
    except ImportError:
        log.warning("openpyxl не установлен — сохраняю сводный CSV")
        all_orgs = []
        for orgs in results.values():
            all_orgs.extend(orgs)
        save_csv(all_orgs, path.with_suffix(".csv"))
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()

    # Сводный лист со всеми результатами
    ws_all = wb.active
    ws_all.title = "Все результаты"
    all_orgs: list[Organization] = []
    for orgs in results.values():
        all_orgs.extend(orgs)
    _write_sheet(ws_all, all_orgs)

    # Отдельный лист на каждую категорию
    for query, orgs in results.items():
        if not orgs:
            continue
        # Имя листа Excel ≤ 31 символ, без спецсимволов
        sheet_name = re.sub(r'[\\/*?\[\]:]', '', query)[:31]
        ws = wb.create_sheet(title=sheet_name)
        _write_sheet(ws, orgs)

    wb.save(path)
    total = sum(len(v) for v in results.values())
    log.info(
        "XLSX сохранён: %s (%d записей, %d листов)",
        path, total, len(wb.sheetnames),
    )


# ---------------------------------------------------------------------------
# Main parser flow
# ---------------------------------------------------------------------------

# Минимальный stealth JS — используется ТОЛЬКО как fallback для обычного Playwright.
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



def _create_browser_context(pw, headless: bool, proxy_url: str | None = None):
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
        # Локаль и таймзона ДОЛЖНЫ соответствовать домену/региону, иначе
        # рассинхрон (yandex.kz + Europe/Moscow) палит бота. Для KZ — Asia/Almaty.
        "locale": DOMAIN_LOCALE.get(DOMAIN, "ru-RU"),
        "timezone_id": DOMAIN_TZ.get(DOMAIN, "Asia/Almaty"),
        "color_scheme": "light",
        # НЕ ставим user_agent — Chrome уже имеет свой настоящий
        # НЕ ставим extra_http_headers — избегаем inconsistency
    }
    if proxy_url:
        rotator = get_proxy_rotator()
        launch_args["proxy"] = rotator.to_playwright_arg(proxy_url)
        log.info("Прокси: %s", proxy_url.split("@")[-1] if "@" in proxy_url else proxy_url)

    # Явный путь к бинарю Chromium/Chrome (для CI/докера/песочниц, где нет
    # системного Chrome и не скачаны браузеры Playwright). Напр.:
    #   YAMAP_BROWSER_PATH=/opt/pw-browsers/chromium
    browser_path = os.environ.get("YAMAP_BROWSER_PATH")

    # Persistent context: всё состояние браузера сохраняется на диск.
    # Приоритет — настоящий Chrome (channel="chrome"): лучший анти-детект.
    # Fallback-цепочка, если Chrome не установлен: явный executable_path →
    # bundled Chromium. На «сыром» Chromium инжектим stealth-JS.
    need_stealth = (log_lib == "playwright")
    ctx: BrowserContext
    if browser_path:
        launch_args.pop("channel", None)
        launch_args["executable_path"] = browser_path
        ctx = pw.chromium.launch_persistent_context(str(BROWSER_DATA_DIR), **launch_args)
        need_stealth = True
        log.info("Браузер: Chromium по YAMAP_BROWSER_PATH=%s (persistent %s)",
                 browser_path, BROWSER_DATA_DIR)
    else:
        try:
            ctx = pw.chromium.launch_persistent_context(str(BROWSER_DATA_DIR), **launch_args)
            log.info("Браузер: настоящий Chrome + persistent-профиль (%s)", BROWSER_DATA_DIR)
        except Exception as exc:
            log.warning("Настоящий Chrome недоступен (%s) — откат на bundled Chromium. "
                        "Для лучшего обхода анти-фрода установите Google Chrome "
                        "или задайте YAMAP_BROWSER_PATH.",
                        str(exc).splitlines()[0] if str(exc) else exc)
            launch_args.pop("channel", None)   # bundled Chromium вместо channel="chrome"
            ctx = pw.chromium.launch_persistent_context(str(BROWSER_DATA_DIR), **launch_args)
            need_stealth = True
            log.info("Браузер: bundled Chromium + persistent-профиль (%s)", BROWSER_DATA_DIR)

    # С patchright + настоящим Chrome stealth НЕ нужен (нет Runtime.enable-утечки
    # и navigator.webdriver, лишние патчи только палятся). Но на «сыром» Chromium
    # или обычном playwright — инжектим минимальный stealth.
    if need_stealth:
        try:
            ctx.add_init_script(_STEALTH_JS)
        except Exception:
            pass

    return None, ctx


def _setup_page(ctx: BrowserContext) -> Page:
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


def _warmup(page: Page, headless: bool) -> None:
    """Прогрев: зайти на Яндекс как обычный пользователь перед парсингом.

    Стратегия: сначала заходим на главную активного домена (напр. yandex.kz),
    потом переходим на карты — как обычный человек. Яндекс меньше подозревает
    юзеров с естественной цепочкой переходов и консистентным реферером.
    ВАЖНО: оба перехода идут через ОДИН домен — смешанная цепочка .ru→.kz
    выглядит как бот и повышает риск капчи.
    """
    global _warmed_up
    if _warmed_up:
        return
    _warmed_up = True

    log.info("Прогрев: естественная навигация %s → карты…", DOMAIN)
    try:
        # Шаг 1: заходим на главную Яндекса (как обычный пользователь)
        page.goto(f"{base_url()}/", wait_until="domcontentloaded", timeout=20000)
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

        # Шаг 2: переходим на карты через навигацию (реферер = тот же домен).
        # Открываем карты сразу с гео-вьюпортом KZ, чтобы регион не «уполз» в Москву.
        page.wait_for_timeout(random.randint(1000, 2500))
        warm_maps = f"{maps_url()}?lang={LANG}" + (f"&ll={MAP_LL}&z={MAP_Z}" if MAP_LL else "")
        page.goto(warm_maps, wait_until="domcontentloaded", timeout=20000)
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


def _do_search(page: Page, query: str, headless: bool) -> bool:
    """Выполнить поиск: ввести запрос в строку поиска как человек.

    Возвращает True если удалось ввести и отправить запрос.
    Fallback: если строка поиска не найдена — goto по URL.
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

    # Fallback: goto по URL (менее естественно, но работает).
    # URL несёт lang=ru_RU и вьюпорт KZ (ll+z), чтобы выдача была казахстанской.
    log.debug("Строка поиска не найдена — используем goto")
    page.goto(
        search_url(query),
        wait_until="domcontentloaded", timeout=30000,
    )
    return True


def _search_and_collect(
    page: Page,
    query: str,
    max_results: int,
    scroll_pause: float,
    api_intercept: bool,
    on_org: Any = None,
    headless: bool = True,
) -> list[Organization]:
    """Выполнить поиск и собрать результаты (общая логика для всех режимов)."""
    engine = get_selector_engine()

    # Прогрев при первом запросе
    _warmup(page, headless)

    log.info("Поиск: %s", query)

    # Адаптивная пауза перед запросом (замедляемся если были капчи)
    throttle = get_throttle()
    pre_pause = throttle.get_pause(base_ms=1000)
    page.wait_for_timeout(pre_pause)

    # Вводим запрос через строку поиска (как человек)
    _do_search(page, query, headless)
    page.wait_for_timeout(random.randint(2500, 4500))

    # Проверка CAPTCHA
    if detect_captcha(page):
        throttle.on_captcha()
        if not handle_captcha(page, headless):
            log.error("CAPTCHA не решена, пропускаю запрос: %s", query)
            return []
    else:
        throttle.on_success()

    # Ждём появления результатов, пробуем несколько селекторов
    item_sel = engine.get("item")
    found = False
    for sel_candidate in [item_sel, ITEM_SEL, "[class*='search-snippet']", "[class*='serp-item']"]:
        if not sel_candidate:
            continue
        try:
            page.wait_for_selector(sel_candidate, timeout=5000)
            found = True
            break
        except Exception:
            continue

    if not found:
        # Может быть CAPTCHA появилась после загрузки
        if detect_captcha(page):
            if not handle_captcha(page, headless):
                return []
            # Пробуем ещё раз найти результаты
            try:
                page.wait_for_selector(item_sel or ITEM_SEL, timeout=10000)
                found = True
            except Exception:
                pass

    if not found:
        log.warning("Результаты не найдены для запроса: %s", query)
        return []

    # Запускаем автодетект на живой странице с результатами
    engine.probe_and_detect(page, mode="list")

    if api_intercept:
        log.info("Режим API-перехвата")
        orgs = run_api_intercept(page, max_results, scroll_pause)
    else:
        # Стриминг: скроллим + парсим на лету
        orgs = scroll_and_parse(page, max_results, scroll_pause, on_org=on_org)

    log.info("Извлечено организаций: %d", len(orgs))
    return orgs


def _search_with_retry(
    page: Page,
    query: str,
    max_results: int,
    scroll_pause: float,
    api_intercept: bool,
    on_org: Any = None,
    headless: bool = True,
    max_retries: int = 3,
) -> list[Organization]:
    """Обёртка над _search_and_collect с retry при ошибках."""
    for attempt in range(1, max_retries + 1):
        try:
            orgs = _search_and_collect(
                page, query, max_results, scroll_pause,
                api_intercept, on_org=on_org, headless=headless,
            )
            if orgs:
                return orgs
            # Пустой результат — может стоит попробовать ещё
            if attempt < max_retries:
                log.warning("Пустой результат для «%s», попытка %d/%d…",
                            query, attempt, max_retries)
                page.wait_for_timeout(random.randint(5000, 10000))
            else:
                return []
        except Exception as exc:
            if attempt < max_retries:
                wait_sec = attempt * 10
                log.warning("Ошибка при поиске «%s»: %s. Retry через %d сек (попытка %d/%d)",
                            query, exc, wait_sec, attempt, max_retries)
                page.wait_for_timeout(wait_sec * 1000)
                # Возвращаемся на карты перед retry
                try:
                    page.goto(maps_url(), wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(random.randint(2000, 4000))
                except Exception:
                    pass
            else:
                log.error("Не удалось выполнить поиск «%s» после %d попыток: %s",
                          query, max_retries, exc)
                return []
    return []


def _enrich_orgs(ctx: BrowserContext, orgs: list[Organization], n_tabs: int = 3) -> None:
    """Обогатить организации данными с карточек.

    Использует n_tabs параллельных вкладок для ускорения.
    """
    if not orgs:
        return
    log.info("Обогащаю данные с карточек организаций (%d шт, %d вкладок)…", len(orgs), n_tabs)

    # Создаём пул вкладок
    pages: list[Page] = []
    for _ in range(min(n_tabs, len(orgs))):
        try:
            pages.append(ctx.new_page())
        except Exception:
            break
    if not pages:
        pages.append(ctx.new_page())

    detail_detected = [False]
    pbar = None
    if HAS_TQDM:
        pbar = tqdm(total=len(orgs), desc="Обогащение карточек", unit="орг")

    # Распределяем организации по вкладкам round-robin
    for idx, org in enumerate(orgs):
        page = pages[idx % len(pages)]
        enrich_from_detail(page, org, _detail_detected=detail_detected)
        if pbar:
            pbar.update(1)
        elif (idx + 1) % 20 == 0:
            log.info("Обогащено: %d / %d", idx + 1, len(orgs))
        # Небольшая пауза между карточками (не бомбить сервер)
        page.wait_for_timeout(random.randint(300, 800))

    if pbar:
        pbar.close()

    for p in pages:
        try:
            p.close()
        except Exception:
            pass


def _make_incremental_saver(out_path: Path, save_every: int = 25):
    """Создать callback для инкрементального сохранения по мере сбора.

    Возвращает (on_org_callback, all_orgs_list).
    Каждые save_every организаций промежуточный результат сбрасывается в файл.
    """
    all_orgs: list[Organization] = []

    def on_org(org: Organization, index: int) -> None:
        all_orgs.append(org)
        if index % save_every == 0:
            try:
                if out_path.suffix == ".csv":
                    save_csv(list(all_orgs), out_path)
                else:
                    save_xlsx(list(all_orgs), out_path)
                log.info("Промежуточное сохранение: %d записей → %s", len(all_orgs), out_path)
            except Exception as exc:
                log.debug("Ошибка промежуточного сохранения: %s", exc)

    return on_org, all_orgs


def _save_auto(orgs: list[Organization], out_path: Path) -> None:
    """Сохранить в формат по расширению файла (.xlsx / .csv / .json)."""
    suffix = out_path.suffix.lower()
    if suffix == ".csv":
        save_csv(orgs, out_path)
    elif suffix == ".json":
        save_json(orgs, out_path)
    else:
        save_xlsx(orgs, out_path)


_COVERAGE_FIELDS = [
    ("org_id", "С ID организации"),
    ("phone", "С телефоном"),
    ("website", "С сайтом"),
    ("email", "С email"),
    ("social_links", "С соцсетями"),
    ("latitude", "С координатами"),
    ("working_hours", "С часами работы"),
    ("description", "С описанием"),
    ("features", "С особенностями"),
    ("photo_url_template", "С фото"),
]


def compute_stats(orgs: list[Organization]) -> dict[str, Any]:
    """Посчитать сводную статистику (для консоли и для отчёта)."""
    total = len(orgs)
    if not total:
        return {"total": 0, "coverage": {}, "avg_rating": None, "top_categories": [], "ads": 0}

    coverage: dict[str, dict] = {}
    for field, human in _COVERAGE_FIELDS:
        cnt = sum(1 for o in orgs if getattr(o, field, ""))
        coverage[human] = {"count": cnt, "pct": cnt * 100 // total}

    ads = sum(1 for o in orgs if o.is_advert == "да")
    rating_vals = []
    for o in orgs:
        try:
            rating_vals.append(float(o.rating.replace(",", ".")))
        except (ValueError, AttributeError):
            pass
    avg_rating = round(sum(rating_vals) / len(rating_vals), 2) if rating_vals else None

    cat_counts: dict[str, int] = {}
    for o in orgs:
        for c in (o.category or "").split(","):
            c = c.strip()
            if c:
                cat_counts[c] = cat_counts.get(c, 0) + 1
    top_cats = sorted(cat_counts.items(), key=lambda x: -x[1])[:5]

    return {
        "total": total,
        "coverage": coverage,
        "ads": ads,
        "avg_rating": avg_rating,
        "rating_sample": len(rating_vals),
        "top_categories": top_cats,
    }


def print_stats(orgs: list[Organization], label: str = "Результаты") -> dict[str, Any]:
    """Напечатать сводную статистику и вернуть её как dict (для отчёта)."""
    stats = compute_stats(orgs)
    if not stats["total"]:
        print(f"\n{label}: 0 организаций")
        return stats

    print(f"\n{'=' * 50}")
    print(f"  {label}")
    print(f"{'=' * 50}")
    print(f"  Всего организаций:  {stats['total']}")
    for human, info in stats["coverage"].items():
        print(f"  {human + ':':20s}{info['count']} ({info['pct']}%)")
    print(f"  {'Рекламных:':20s}{stats['ads']} ({stats['ads'] * 100 // stats['total']}%)")
    if stats["avg_rating"] is not None:
        print(f"  Средний рейтинг:    {stats['avg_rating']:.1f} (из {stats['rating_sample']} оценок)")
    if stats["top_categories"]:
        print(f"  Топ категории:")
        for cat, cnt in stats["top_categories"]:
            print(f"    {cat}: {cnt}")
    print(f"{'=' * 50}\n")
    return stats


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
    global _warmed_up
    _warmed_up = False  # Сброс для нового контекста браузера
    out_path = Path(output)

    # Отчётность: файл-лог + снимок счётчиков для этого прогона
    setup_file_logging()
    _RUN_ERRORS.clear()
    _t_start = time.time()
    _captchas_before = get_throttle().captcha_count
    orgs: list[Organization] = []

    # Резюме: загружаем уже собранные данные
    resume = ResumeManager(resume_path) if resume_path else None
    if resume and resume.existing_count > 0:
        log.info("Резюме: %d организаций уже собраны", resume.existing_count)

    # Промежуточное сохранение: каждые 25 организаций сбрасываем в файл
    on_org, incremental_orgs = _make_incremental_saver(out_path, save_every=25)

    try:
        with sync_playwright() as pw:
            _, ctx = _create_browser_context(pw, headless, proxy_url=proxy_url)
            page = _setup_page(ctx)

            orgs = _search_with_retry(
                page, query, max_results, scroll_pause, api_intercept,
                on_org=on_org, headless=headless,
            )

            for org in orgs:
                org.search_query = query

            # Фильтруем уже известные (из резюме)
            if resume:
                before = len(orgs)
                orgs = [o for o in orgs if not resume.is_known(o.name, o.address)]
                if before != len(orgs):
                    log.info("Резюме: пропущено %d уже собранных", before - len(orgs))
                orgs = resume.existing_orgs() + orgs

            if detail:
                _enrich_orgs(ctx, orgs)

            # Persistent context сохраняет всё автоматически при закрытии
            ctx.close()
    except Exception as exc:
        record_error(f"Критическая ошибка прогона: {exc}")
        log.error("Прогон прерван ошибкой: %s", exc)

    _save_auto(orgs, out_path)
    stats = print_stats(orgs)
    save_run_report({
        "mode": "search",
        "label": query,
        "query": query,
        "domain": DOMAIN, "lang": LANG,
        "viewport_ll": MAP_LL, "viewport_z": MAP_Z,
        "max_results": max_results, "api_intercept": api_intercept,
        "detail": detail, "headless": headless, "proxy": bool(proxy_url),
        "output": str(out_path),
        "duration_sec": round(time.time() - _t_start, 1),
        "captchas": get_throttle().captcha_count - _captchas_before,
    }, stats)
    return orgs


# ---------------------------------------------------------------------------
# Category-based parser (аналог 2ГИС)
# ---------------------------------------------------------------------------

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
    global _warmed_up
    _warmed_up = False  # Сброс для нового контекста браузера
    out_path = Path(output)
    results: dict[str, list[Organization]] = {}
    seen_global: set[str] = set()

    # Отчётность
    setup_file_logging()
    _RUN_ERRORS.clear()
    _t_start = time.time()
    _captchas_before = get_throttle().captcha_count

    # Резюме
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

    # Прогресс по категориям
    cat_iter = enumerate(queries, 1)
    if HAS_TQDM:
        cat_iter_tqdm = tqdm(list(cat_iter), desc="Категории", unit="кат")
    else:
        cat_iter_tqdm = None

    try:
      with sync_playwright() as pw:
        _, ctx = _create_browser_context(pw, headless, proxy_url=proxy_url)
        page = _setup_page(ctx)

        for q_idx, cat_query in (cat_iter_tqdm if cat_iter_tqdm else enumerate(queries, 1)):
            full_query = f"{cat_query} {city}"

            # Пропускаем уже выполненные запросы (резюме)
            if resume and resume.is_query_done(full_query):
                log.info("Пропуск (резюме): %s", full_query)
                continue

            log.info("━━━ [%d/%d] %s ━━━", q_idx, total_queries, full_query)

            orgs = _search_with_retry(
                page, full_query, max_results_per_category,
                scroll_pause, api_intercept, headless=headless,
            )
            if not orgs:
                record_error(f"Пустой результат по запросу «{full_query}» "
                             f"(возможна капча/блокировка)")

            # Дедупликация по имени+адресу
            unique_orgs: list[Organization] = []
            for org in orgs:
                key = _dedup_key(org)
                if key not in seen_global:
                    seen_global.add(key)
                    org.search_query = full_query
                    unique_orgs.append(org)

            if detail:
                _enrich_orgs(ctx, unique_orgs)

            results[full_query] = unique_orgs
            log.info("Категория «%s»: %d организаций (уникальных)", cat_query, len(unique_orgs))

            # Промежуточное сохранение после каждой категории
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

            # Адаптивная пауза между категориями (увеличивается при капчах)
            if q_idx < total_queries:
                throttle = get_throttle()
                pause = throttle.get_pause(base_ms=random.randint(3000, 6000))
                log.debug("Пауза между категориями: %d мс (x%.1f)", pause, throttle.multiplier)
                page.wait_for_timeout(pause)

                # Иногда (20%) «гуляем» по карте между запросами — выглядит естественно
                if random.random() < 0.20:
                    for _ in range(random.randint(2, 4)):
                        page.mouse.move(
                            random.randint(500, 1000),
                            random.randint(200, 700),
                        )
                        page.wait_for_timeout(random.randint(200, 600))
                    page.mouse.wheel(0, random.randint(-100, 100))
                    page.wait_for_timeout(random.randint(500, 1500))

        # Persistent context сохраняет всё автоматически при закрытии
        ctx.close()
    except Exception as exc:
        record_error(f"Критическая ошибка прогона: {exc}")
        log.error("Прогон прерван ошибкой: %s", exc)

    # Финальное сохранение
    all_orgs: list[Organization] = []
    for v in results.values():
        all_orgs.extend(v)

    # Добавляем ранее собранные (из резюме)
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
    log.info("Всего собрано: %d организаций по %d категориям", total_orgs, total_queries)
    stats = print_stats(all_orgs, label=f"Статистика: {city}")
    # Автоматический отчёт о прогоне (+ разбивка по категориям)
    save_run_report({
        "mode": "categories",
        "label": city,
        "city": city,
        "categories": categories,
        "domain": DOMAIN, "lang": LANG,
        "viewport_ll": MAP_LL, "viewport_z": MAP_Z,
        "max_results": max_results_per_category, "api_intercept": api_intercept,
        "detail": detail, "headless": headless, "proxy": bool(proxy_url),
        "output": str(out_path),
        "duration_sec": round(time.time() - _t_start, 1),
        "captchas": get_throttle().captcha_count - _captchas_before,
        "per_query": {q: len(v) for q, v in results.items()},
    }, stats)
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Парсер организаций с Яндекс.Карт Казахстана (yandex.kz) — по запросу или по категориям (аналог 2ГИС)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Примеры:
  # По запросу
  python yandex_parser.py "кофейни Алматы"
  python yandex_parser.py "автосервис Астана" -n 200 -o авто.xlsx --api-intercept

  # По категориям
  python yandex_parser.py --city Алматы --category еда
  python yandex_parser.py --city Шымкент --category авто красота -n 100
  python yandex_parser.py --city Астана --all-categories --api-intercept

  # Другой домен / явный вьюпорт
  python yandex_parser.py "аптеки Ташкент" --tld uz
  python yandex_parser.py "кафе" --ll 76.8897,43.2389 --z 13

  # С прокси
  python yandex_parser.py "аптеки Алматы" --proxy http://user:pass@host:port
  python yandex_parser.py --city Алматы --category еда --proxy-file proxies.txt

  # Продолжить прерванный сбор
  python yandex_parser.py --city Алматы --all-categories --resume Алматы_categories.xlsx

  # Экспорт в JSON
  python yandex_parser.py "рестораны Караганда" -o results.json

  # С конфиг-файлом
  python yandex_parser.py --config config.yaml

  # Списки и автодетект
  python yandex_parser.py --list-categories
  python yandex_parser.py --list-cities
  python yandex_parser.py --detect-selectors --no-headless
""",
    )
    parser.add_argument(
        "query", nargs="?", default=None,
        help='Поисковый запрос, напр. "кофейни Алматы"',
    )
    parser.add_argument(
        "--list-categories", action="store_true",
        help="Показать каталог категорий и выйти",
    )
    parser.add_argument(
        "--city", type=str, default=None,
        help="Город для парсинга по категориям",
    )
    parser.add_argument(
        "--category", nargs="+", default=None,
        help="Категории или группы категорий (напр. еда рестораны кафе)",
    )
    parser.add_argument(
        "--all-categories", action="store_true",
        help="Парсить все категории из каталога",
    )
    parser.add_argument(
        "--max-results", "-n", type=int, default=500,
        help="Максимум организаций на запрос/категорию (по умолчанию 500)",
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help="Файл результатов: .xlsx, .csv или .json (по умолчанию results.xlsx / categories.xlsx)",
    )
    parser.add_argument(
        "--no-headless", action="store_true",
        help="Показывать браузер (для отладки)",
    )
    parser.add_argument(
        "--detail", action="store_true",
        help="Открывать карточку каждой организации для телефона/сайта",
    )
    parser.add_argument(
        "--scroll-pause", type=float, default=1.0,
        help="Пауза между прокрутками в секундах (по умолчанию 1.0)",
    )
    parser.add_argument(
        "--api-intercept", action="store_true",
        help="Перехватывать JSON из внутреннего API вместо парсинга DOM",
    )
    parser.add_argument(
        "--proxy", type=str, default=None,
        help="Прокси-сервер (http://host:port или socks5://user:pass@host:port)",
    )
    parser.add_argument(
        "--proxy-file", type=str, default=None,
        help="Файл со списком прокси (одна строка = один прокси, ротация round-robin)",
    )
    parser.add_argument(
        "--resume", type=str, default=None,
        help="Путь к файлу с предыдущими результатами для продолжения сбора",
    )
    parser.add_argument(
        "--config", type=str, default=None,
        help="Конфиг-файл (JSON или YAML) с параметрами парсера",
    )
    parser.add_argument(
        "--detect-selectors", action="store_true",
        help="Открыть Я.Карты, автоматически обнаружить актуальные CSS-селекторы "
             "и сохранить в selectors_cache.json",
    )
    parser.add_argument(
        "--show-selectors", action="store_true",
        help="Показать текущие активные селекторы (из кеша или hardcoded)",
    )
    parser.add_argument(
        "--reset-selectors", action="store_true",
        help="Удалить кеш селекторов и вернуться к hardcoded",
    )
    parser.add_argument(
        "--tld", type=str, default=None, choices=list(DOMAIN_REGISTRY),
        help="Домен Яндекс.Карт: kz (по умолчанию, Казахстан), ru, com, by, uz, tr",
    )
    parser.add_argument(
        "--ll", type=str, default=None,
        help="Центр карты 'долгота,широта' для гео-привязки выдачи (переопределяет город)",
    )
    parser.add_argument(
        "--z", type=int, default=None,
        help="Зум карты (11-13 город, 10 пригороды, 14 центр; по умолчанию 12)",
    )
    parser.add_argument(
        "--list-cities", action="store_true",
        help="Показать список городов Казахстана с координатами и выйти",
    )

    args = parser.parse_args()

    # Домен/язык — резолвим РАНО (до ветки --detect-selectors, которая уже
    # строит URL). Приоритет: --tld > config['tld'/'domain'] > 'kz'.
    tld = args.tld
    if not tld and args.config:
        _cfg = load_config(args.config)
        tld = _cfg.get("tld") or _cfg.get("domain")
    set_domain(tld)
    # Ранний вьюпорт (для --detect-selectors и как дефолт): CLI ll/city, иначе центр страны.
    set_viewport(city=args.city, ll=args.ll, z=args.z)
    # Логи всего происходящего пишутся в файл автоматически.
    setup_file_logging()

    # Список городов KZ
    if args.list_cities:
        print("\n🇰🇿 Города Казахстана (ll = долгота,широта):\n")
        for name, ll in KZ_CITIES.items():
            print(f"  {name:20s} ll={ll}")
        print(f"\n  Вся страна:          ll={KZ_COUNTRY_LL} (z=5)")
        return

    # Режим: показать текущие селекторы
    if args.show_selectors:
        engine = get_selector_engine()
        cache = engine._cache.all()
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

    # Режим: сбросить кеш
    if args.reset_selectors:
        if SELECTORS_CACHE_FILE.exists():
            SELECTORS_CACHE_FILE.unlink()
            print("Кеш селекторов удалён. Будут использоваться hardcoded-селекторы.")
        else:
            print("Кеш селекторов не найден.")
        return

    # Режим: показать каталог категорий
    if args.list_categories:
        list_categories()
        return

    # Режим: принудительная детекция селекторов
    if args.detect_selectors:
        test_query = args.query or "кофейни Алматы"
        print(f"\nЗапуск автодетекта селекторов (запрос: «{test_query}»)…\n")

        # Удаляем старый кеш для чистого детекта
        if SELECTORS_CACHE_FILE.exists():
            SELECTORS_CACHE_FILE.unlink()

        with sync_playwright() as pw:
            _, ctx = _create_browser_context(pw, headless=not args.no_headless)
            page = _setup_page(ctx)

            page.goto(
                search_url(test_query),
                wait_until="domcontentloaded", timeout=30000,
            )
            page.wait_for_timeout(4000)

            engine = get_selector_engine()

            # Детект списка
            print("--- Детекция селекторов списка результатов ---")
            list_sels = detect_selectors(page, mode="list")
            for k, v in list_sels.items():
                works = _probe_selector(page, v)
                status = "OK" if works else "??"
                print(f"  [{status}] {k:25s} → {v}")

            # Проверяем hardcoded для сравнения
            print("\n--- Проверка hardcoded-селекторов ---")
            for k, v in _HARDCODED.items():
                if k.startswith("detail_"):
                    continue
                works = _probe_selector(page, v)
                status = "OK" if works else "FAIL"
                print(f"  [{status}] {k:25s} → {v}")

            # Пробуем открыть первую карточку для детекции detail-селекторов
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
                        href = f"{base_url()}{href}"
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

        # Сохраняем результат
        if list_sels:
            cache = SelectorCache()
            cache.update(list_sels)
            cache.save()
            print(f"\nСохранено {len(list_sels)} селекторов → {SELECTORS_CACHE_FILE}")
        else:
            print("\nАвтодетект не нашёл селекторов.")
        return

    # Общие параметры
    headless = not args.no_headless

    # Конфиг-файл (перезаписывает дефолты, CLI-аргументы приоритетнее)
    if args.config:
        cfg = load_config(args.config)
        for k, v in cfg.items():
            arg_key = k.replace("-", "_")
            if hasattr(args, arg_key) and getattr(args, arg_key) is None:
                setattr(args, arg_key, v)

    # Повторно резолвим вьюпорт после мерджа конфига (city/ll/z могли прийти из конфига).
    set_viewport(city=args.city, ll=args.ll, z=args.z)
    log.info("Домен: %s | язык: %s | вьюпорт: ll=%s z=%s",
             DOMAIN, LANG, MAP_LL or "—", MAP_Z)

    # Прокси
    proxy_url = None
    if args.proxy:
        proxy_url = args.proxy
    elif args.proxy_file:
        global _proxy_rotator
        _proxy_rotator = ProxyRotator.from_file(args.proxy_file)
        proxy_url = _proxy_rotator.next()

    # Резюме
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
        # Может ввели текст напрямую
        if raw in options or allow_empty:
            return raw
        print("  Неверный выбор, попробуйте ещё раз.")


def _input_yn(prompt: str, default: bool = False) -> bool:
    """Да/нет вопрос."""
    hint = "[Y/n]" if default else "[y/N]"
    raw = input(f"{prompt} {hint}: ").strip().lower()
    if raw == "":
        return default
    return raw in ("y", "yes", "д", "да", "1")


def interactive_menu() -> None:
    """Пошаговое интерактивное меню — запускается при старте без аргументов."""
    print()
    print("=" * 55)
    print("  YAmap — Парсер Яндекс.Карт (Казахстан)")
    print("=" * 55)
    print()

    # 0. Домен (по умолчанию Казахстан)
    tld = _input_choice(
        "Регион/домен [1]:",
        ["Казахстан (yandex.kz)", "Россия (yandex.ru)", "yandex.com", "yandex.by", "yandex.uz"],
        allow_empty=True,
    ) or "Казахстан (yandex.kz)"
    _tld_map = {
        "Казахстан (yandex.kz)": "kz", "Россия (yandex.ru)": "ru",
        "yandex.com": "com", "yandex.by": "by", "yandex.uz": "uz",
    }
    set_domain(_tld_map.get(tld, "kz"))

    # 1. Режим работы
    print("\nВыберите режим:")
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
        # Формируем sys.argv и перезапускаем main
        sys.argv = [sys.argv[0], "--detect-selectors", "--no-headless"]
        main()
        return

    # 2. Город / запрос
    query = None
    city = None
    categories_input: list[str] = []

    if mode == "Поиск по запросу":
        query = input("\nПоисковый запрос (напр. кофейни Алматы): ").strip()
        if not query:
            print("Запрос не может быть пустым!")
            return

    elif mode in ("Парсинг по категориям (как 2ГИС)", "Все категории города"):
        if DOMAIN == "yandex.kz":
            print("\nГорода Казахстана: " + ", ".join(list(KZ_CITIES)[:9]) + " …")
        city = input("\nГород (напр. Алматы): ").strip()
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

    # 3. Максимум результатов
    raw_max = input("\nМаксимум организаций на запрос [500]: ").strip()
    max_results = int(raw_max) if raw_max.isdigit() else 500

    # 4. Формат вывода
    print("\nФормат сохранения:")
    fmt = _input_choice("Номер:", ["Excel (.xlsx)", "CSV (.csv)", "JSON (.json)"])
    ext_map = {"Excel (.xlsx)": ".xlsx", "CSV (.csv)": ".csv", "JSON (.json)": ".json"}
    ext = ext_map[fmt]

    # Имя файла
    if city:
        default_name = f"{city}_categories{ext}"
    elif query:
        safe_name = re.sub(r'[^\w\s-]', '', query)[:30].strip().replace(' ', '_')
        default_name = f"{safe_name}{ext}"
    else:
        default_name = f"results{ext}"

    raw_output = input(f"\nИмя файла [{default_name}]: ").strip()
    output = raw_output if raw_output else default_name

    # 5. Полнота данных
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

    # 6. Прокси
    proxy_url = None
    if _input_yn("Использовать прокси?", False):
        print("\n  1. Один прокси (ввести вручную)")
        print("  2. Файл с прокси-списком")
        proxy_mode = input("\nНомер [1]: ").strip()
        if proxy_mode == "2":
            proxy_file = input("Путь к файлу с прокси: ").strip()
            if proxy_file:
                global _proxy_rotator
                _proxy_rotator = ProxyRotator.from_file(proxy_file)
                proxy_url = _proxy_rotator.next()
        else:
            proxy_url = input("Прокси (http://host:port): ").strip() or None

    # 7. Резюме
    resume_path = None
    existing_file = Path(output)
    if existing_file.exists():
        if _input_yn(f"Файл {output} уже существует. Продолжить сбор (resume)?", True):
            resume_path = existing_file

    # 8. Подтверждение
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

    # Гео-привязка выдачи к городу (важно для KZ, чтобы регион не «уполз»).
    set_viewport(city=city)
    log.info("Домен: %s | язык: %s | вьюпорт: ll=%s z=%s",
             DOMAIN, LANG, MAP_LL or "—", MAP_Z)

    # 9. Запуск
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


if __name__ == "__main__":
    main()
