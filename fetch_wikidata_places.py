#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Выкачать населённые пункты из Wikidata — структурированных данных Википедии.

Зачем отдельно от OSM: у Wikidata русские названия лежат отдельным полем
(`ruLabel`), то есть приходят ровно в том виде, в каком их подписывает
Яндекс. В OSM русское имя есть не у всех объектов, зато охват шире. Два
источника дополняют друг друга, и скрипт умеет их сливать (`--merge`).

Википедию напрямую парсить незачем: Wikidata — это её же данные, только
машинно-читаемые, с координатами и без разбора вики-разметки.

    python3 fetch_wikidata_places.py                       # KZ + UZ + KG
    python3 fetch_wikidata_places.py --countries kz
    python3 fetch_wikidata_places.py --merge kz_osm_places.json

Результат: {"Название": "долгота,широта", ...} — тот же формат, что у
KZ_CITIES в парсере. Читается флагом `--cities wiki` (или `--cities @файл`).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENDPOINTS = [
    "https://query.wikidata.org/sparql",
    "https://query.wikidata.org/bigdata/namespace/wdq/sparql",
]

#: Wikidata-идентификаторы стран.
COUNTRY_QID = {"kz": "Q232", "uz": "Q265", "kg": "Q813"}

#: Классы населённых пунктов. Q486972 (населённый пункт) покрывает почти всё
#: через подклассы, но подклассы в Wikidata размечены неровно, поэтому
#: перечисляем и конкретные типы.
PLACE_QIDS = [
    "Q486972",   # населённый пункт
    "Q532",      # село
    "Q3558970",  # аул
    "Q3957",     # малый город
    "Q515",      # город
    "Q15078955", # посёлок городского типа
    "Q2514025",  # сельский населённый пункт
]

UA = "YAmap/1.0 (settlement fetch for map parsing; local use)"


def build_query(codes: list[str], limit: int = 10000, offset: int = 0) -> str:
    """SPARQL: все НП заданных стран с координатами и русским названием.

    Запрашиваем и русскую метку, и английскую: у мелких аулов русской может
    не быть, и тогда лучше взять хоть что-то, чем потерять точку.
    """
    countries = " ".join(f"wd:{COUNTRY_QID[c]}" for c in codes)
    kinds = " ".join(f"wd:{q}" for q in PLACE_QIDS)
    return f"""
SELECT ?item ?ruLabel ?enLabel ?coord WHERE {{
  VALUES ?country {{ {countries} }}
  VALUES ?kind {{ {kinds} }}
  ?item wdt:P31/wdt:P279* ?kind ;
        wdt:P17 ?country ;
        wdt:P625 ?coord .
  OPTIONAL {{ ?item rdfs:label ?ruLabel . FILTER(LANG(?ruLabel) = "ru") }}
  OPTIONAL {{ ?item rdfs:label ?enLabel . FILTER(LANG(?enLabel) = "en") }}
}}
LIMIT {limit} OFFSET {offset}
""".strip()


def fetch(query: str, timeout: int = 180, retries: int = 3) -> dict:
    """Запрос к Wikidata с перебором зеркал и повтором при отказе."""
    last_err: Exception | None = None
    for attempt in range(retries):
        for url in ENDPOINTS:
            try:
                data = urllib.parse.urlencode({"query": query,
                                               "format": "json"}).encode()
                req = urllib.request.Request(
                    url, data=data,
                    headers={"User-Agent": UA,
                             "Accept": "application/sparql-results+json"})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except Exception as exc:                     # noqa: BLE001
                last_err = exc
                print(f"  … {url.split('/')[2]}: {type(exc).__name__}: {exc}",
                      file=sys.stderr)
                continue
        if attempt < retries - 1:
            wait = 10 * (attempt + 1)
            print(f"  зеркала молчат — жду {wait}с (Wikidata душит частые "
                  f"запросы) и пробую снова", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"Wikidata недоступна: {last_err}")


_POINT_RE = re.compile(r"Point\(\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*\)")
_LATIN = re.compile(r"[A-Za-z]")


def parse_point(value: str) -> tuple[float, float] | None:
    """WKT «Point(lon lat)» → (lon, lat). Именно в таком порядке, не наоборот."""
    m = _POINT_RE.match((value or "").strip())
    if not m:
        return None
    return float(m.group(1)), float(m.group(2))


def pick_name(ru: str, en: str) -> str:
    """Русское имя приоритетно: Яндекс по нему ищет лучше, чем по латинице."""
    ru = (ru or "").strip()
    if ru and not _LATIN.search(ru):
        return ru
    return ru or (en or "").strip()


def rows_to_places(bindings: list[dict], inside=None) -> dict[str, str]:
    """Строки SPARQL → {имя: "lon,lat"}.

    Одноимённые НП (их в регионе множество) получают «(2)», «(3)». Нумерация
    детерминирована: строки сортируются по идентификатору Wikidata, поэтому
    повторная выкачка даст те же номера — иначе резюме парсера указывало бы
    после обновления на другие сёла.
    """
    out: dict[str, str] = {}
    counts: dict[str, int] = {}
    skipped_outside = skipped_noname = skipped_nocoord = 0

    def qid(b: dict) -> str:
        uri = (b.get("item") or {}).get("value", "")
        m = re.search(r"/Q(\d+)$", uri)
        return f"{int(m.group(1)):012d}" if m else uri

    for b in sorted(bindings, key=qid):
        name = pick_name((b.get("ruLabel") or {}).get("value", ""),
                         (b.get("enLabel") or {}).get("value", ""))
        # Метка вида «Q12345» — это объект без имени, брать нечего.
        if not name or re.fullmatch(r"Q\d+", name):
            skipped_noname += 1
            continue
        coords = parse_point((b.get("coord") or {}).get("value", ""))
        if not coords:
            skipped_nocoord += 1
            continue
        lon, lat = coords
        if inside is not None and not inside(lon, lat):
            skipped_outside += 1
            continue
        n = counts.get(name, 0) + 1
        counts[name] = n
        out[name if n == 1 else f"{name} ({n})"] = f"{lon:.4f},{lat:.4f}"

    if skipped_outside or skipped_noname or skipped_nocoord:
        print(f"  отброшено: вне границ {skipped_outside}, без названия "
              f"{skipped_noname}, без координат {skipped_nocoord}",
              file=sys.stderr)
    return out


def _load_border_filter(codes: list[str]):
    """Полигоны границ из парсера, чтобы выкачка и сбор резали одинаково."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import yandex_parser as yp
        polys = [yp.COUNTRIES[c].border for c in codes
                 if c in yp.COUNTRIES and len(yp.COUNTRIES[c].border) >= 3]
        if not polys:
            print("  (полигонов границ нет — фильтр выключен)", file=sys.stderr)
            return None
        return lambda lon, lat: any(
            yp._point_in_polygon(lon, lat, poly) for poly in polys)
    except Exception as exc:                             # noqa: BLE001
        print(f"  (полигоны границ не подключены: {exc})", file=sys.stderr)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Выкачать населённые пункты из Wikidata (данные Википедии)")
    ap.add_argument("-o", "--out", default="wikidata_places.json",
                    help="куда сохранить (по умолч. wikidata_places.json)")
    ap.add_argument("--countries", default="kz,uz,kg",
                    help="страны через запятую: kz, uz, kg")
    ap.add_argument("--merge", default=None,
                    help="слить с готовым справочником (напр. выгрузкой OSM); "
                         "координаты ИЗ НЕГО приоритетнее")
    ap.add_argument("--page", type=int, default=10000,
                    help="строк за запрос (по умолч. 10000)")
    ap.add_argument("--timeout", type=int, default=180)
    ap.add_argument("--no-clip", action="store_true",
                    help="не резать по полигонам границ")
    args = ap.parse_args()

    codes = [c.strip().lower() for c in args.countries.split(",") if c.strip()]
    bad = [c for c in codes if c not in COUNTRY_QID]
    if bad:
        print(f"Неизвестные страны: {bad}. Доступны: {list(COUNTRY_QID)}",
              file=sys.stderr)
        return 2

    inside = None if args.no_clip else _load_border_filter(codes)
    print(f"Страны: {', '.join(codes)}")

    # Странами по одной: общий запрос на три страны Wikidata часто валит по
    # таймауту, а по одной проходит.
    bindings: list[dict] = []
    for code in codes:
        print(f"[{code}] запрашиваю Wikidata …")
        offset = 0
        while True:
            data = fetch(build_query([code], args.page, offset), args.timeout)
            got = (data.get("results") or {}).get("bindings") or []
            print(f"  получено строк: {len(got)} (offset {offset})")
            bindings.extend(got)
            if len(got) < args.page:
                break
            offset += args.page
            time.sleep(2)          # вежливость к публичному сервису

    places = rows_to_places(bindings, inside=inside)
    if not places:
        print("Пусто — ничего не сохраняю.", file=sys.stderr)
        return 1

    if args.merge:
        base = Path(args.merge)
        if base.exists():
            try:
                other = json.loads(base.read_text(encoding="utf-8"))
            except Exception as exc:
                print(f"Не читается {base}: {exc}", file=sys.stderr)
                return 1
            before = len(places)
            # Существующий справочник приоритетнее: его координаты уже
            # проверены прогонами, перетирать их незачем.
            merged = dict(places)
            merged.update(other)
            places = merged
            print(f"Слито с {base}: было {before}, стало {len(places)}")
        else:
            print(f"Файла для слияния нет: {base}", file=sys.stderr)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(places, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(out)
    print(f"\nГотово: {len(places)} населённых пунктов → {out}")
    print("Гнать парсер по ним:\n"
          "  ./run.sh --all-cities --cities wiki --category gt-село "
          "-o wiki.xlsx --workers 2 --api-intercept")
    return 0


if __name__ == "__main__":
    sys.exit(main())
