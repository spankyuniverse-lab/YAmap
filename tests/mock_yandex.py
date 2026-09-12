#!/usr/bin/env python3
"""Локальный макет Яндекс.Карт для сквозного теста.

Отдаёт страницу той же формы, что настоящая выдача:
  • список сниппетов в прокручиваемом контейнере (классы как у Яндекса),
  • SSR-состояние организаций в inline <script type="application/json">,
  • подгрузку следующей порции по кнопке «Показать ещё»,
  • кнопку «Все фильтры» в контейнере с подстрокой show-more — ту самую
    ловушку, на которой парсер спотыкался в бою.

Смысл — прогнать НАСТОЯЩИЙ парсер по всей цепочке (поиск → скролл → разбор →
книга), не выходя в интернет.
"""
from __future__ import annotations

import json
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

#: Сколько организаций отдаём на запрос — задаётся тестом.
CATALOG: dict[str, int] = {}

PAGE_SIZE = 12

#: Тумблер для теста отката: при True макет отвечает на /maps/api/search
#: ошибкой, и парсер обязан молча вернуться к обычному сбору по HTML.
DISABLE_API = False


#: Центры городов, которыми пользуются тесты (lon, lat).
_CITY_LL = {
    "Алматы": (76.89, 43.24),
    "Астана": (71.45, 51.17),
    "Шымкент": (69.60, 42.32),
    "Караганда": (73.11, 49.80),
}


def _city_ll(city: str) -> tuple[float, float]:
    return _CITY_LL.get((city or "").strip(), (76.89, 43.24))


def _org(query: str, city: str, n: int) -> dict:
    """Организация в том виде, в каком её кладёт в SSR настоящий Яндекс."""
    return {
        "id": f"{abs(hash((query, city, n))) % 10**11}",
        "seoname": f"org_{n}",
        "name": f"{query} №{n} {city}",
        "categories": [{"name": query}],
        # Как в настоящей выдаче: адрес строкой в props, координаты списком
        # [lon, lat] в порядке GeoJSON — парсер читает именно так.
        "address": f"{city}, улица Тестовая, {n}",
        "fullAddress": f"Казахстан, {city}, улица Тестовая, {n}",
        "compositeAddress": {"locality": city, "street": "улица Тестовая", "house": str(n)},
        # Координаты — рядом с ЗАПРОШЕННЫМ городом, как и у настоящего
        # Яндекса. Раньше макет всегда отдавал окрестности Алматы, из-за чего
        # выглядел правдоподобно ровно до тех пор, пока парсер не научился
        # отсеивать результаты, уехавшие от центра поиска (тёзки вроде двух
        # «Актау» в 1400 км друг от друга).
        "coordinates": [_city_ll(city)[0] + n / 1000, _city_ll(city)[1] + n / 1000],
        "phones": [{"type": "phone", "formatted": f"+7 701 000-{n:04d}"}],
        "urls": [f"https://example.kz/{n}"],
        "ratingData": {"ratingValue": 4.0, "reviewCount": 10 + n},
        "workingHours": {"text": "круглосуточно"},
        "type": "business",
    }


def _html(query: str, city: str, page: int, total: int) -> str:
    start = page * PAGE_SIZE
    items = [_org(query, city, i) for i in range(start, min(start + PAGE_SIZE, total))]
    has_more = start + PAGE_SIZE < total
    state = json.dumps({"data": {"items": items}}, ensure_ascii=False)

    snippets = "\n".join(
        f'''<li class="search-snippet-view">
              <div class="search-business-snippet-view__title">{o["name"]}</div>
              <div class="search-business-snippet-view__address">{o["address"]}</div>
              <div class="search-business-snippet-view__category">{query}</div>
              <a class="search-snippet-view__link-overlay"
                 href="/maps/org/{o["seoname"]}/{o["id"]}/"></a>
            </li>''' for o in items)

    more = ""
    if has_more:
        more = f'''
        <div class="_x_search-list-view__more">
          <button onclick="loadMore({page + 1})">Показать ещё</button>
        </div>'''

    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>{query} — Карты</title>
<script type="application/json" class="state-view">{state}</script>
<style>
  .scroll__container {{ height: 400px; overflow-y: auto; }}
  li {{ height: 90px; list-style: none; border-bottom: 1px solid #eee; }}
</style></head><body>
<!-- Ловушка из боевого прогона: «Все фильтры» лежит в контейнере, у которого
     в классе есть show-more, а сверху оверлей, перехватывающий клики. -->
<div class="sidebar-dropdown-container" style="position:relative">
  <div class="_1a2b_search-full-filters-view__show-more">
    <button aria-label="Все фильтры" aria-haspopup="true"
            onclick="document.body.setAttribute('data-filters-opened','1')">Все фильтры</button>
  </div>
  <div class="search-full-filters-view__title"
       style="position:absolute;inset:0;z-index:99"></div>
</div>

<div class="_x_search-list-view">
  <div class="scroll__container"><ul id="list">{snippets}</ul></div>
  {more}
</div>

<script>
function loadMore(next) {{
  fetch('/maps/api/search?text=' + encodeURIComponent({json.dumps(query)}) +
        '&city=' + encodeURIComponent({json.dumps(city)}) + '&page=' + next)
    .then(r => r.json())
    .then(d => {{
      const ul = document.getElementById('list');
      for (const o of d.data.items) {{
        const li = document.createElement('li');
        li.className = 'search-snippet-view';
        li.innerHTML =
          '<div class="search-business-snippet-view__title">' + o.name + '</div>' +
          '<div class="search-business-snippet-view__address">' +
              o.address + '</div>' +
          '<a class="search-snippet-view__link-overlay" href="/maps/org/' +
              o.seoname + '/' + o.id + '/"></a>';
        ul.appendChild(li);
      }}
      const box = document.querySelector('._x_search-list-view__more');
      if (box && !d.hasMore) box.remove();
      if (box) box.querySelector('button').setAttribute('onclick', 'loadMore(' + (next + 1) + ')');
    }});
}}
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):          # тишина в тестовом выводе
        pass

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        text = (q.get("text") or [""])[0]
        # «Заправки Алматы» → категория + город
        parts = text.rsplit(" ", 1)
        query, city = (parts[0], parts[1]) if len(parts) == 2 else (text, "")
        total = CATALOG.get(query, 0)

        if u.path.startswith("/maps/api/search"):
            if DISABLE_API:
                self.send_response(503)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            page = int((q.get("page") or ["0"])[0])
            city = (q.get("city") or [city])[0]
            api_text = (q.get("text") or [query])[0]
            # Страница зовёт API с раздельными text=<рубрика>&city=<город>,
            # а прямой запрос (--fast-api) — одним text=«Рубрика Город».
            # Принимаем оба: сперва целиком, потом как «рубрика + город».
            if api_text in CATALOG:
                query, total = api_text, CATALOG[api_text]
            else:
                bits = api_text.rsplit(" ", 1)
                if len(bits) == 2 and bits[0] in CATALOG:
                    query, city = bits[0], city or bits[1]
                    total = CATALOG[query]
                else:
                    query, total = api_text, 0
            start = page * PAGE_SIZE
            items = [_org(query, city, i) for i in range(start, min(start + PAGE_SIZE, total))]
            body = json.dumps({"data": {"items": items},
                               "hasMore": start + PAGE_SIZE < total},
                              ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        body = _html(query, city, 0, total).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start(catalog: dict[str, int]) -> tuple[str, ThreadingHTTPServer]:
    """Поднять макет. Возвращает (base_url, server) — server.shutdown() гасит."""
    CATALOG.clear()
    CATALOG.update(catalog)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}", srv


if __name__ == "__main__":
    url, srv = start({"Заправки": 30})
    print("макет на", url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
