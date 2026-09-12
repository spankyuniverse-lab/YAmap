#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Выкачать населённые пункты Казахстана из OpenStreetMap (Overpass API).

Зачем: встроенный справочник `KZ_SETTLEMENTS` собран вручную и покрывает
сотни НП, а реальных сёл и аулов в Казахстане — тысячи. OSM отдаёт их все,
бесплатно, без ключей и капчи. Полученный файл парсер читает флагом
`--cities osm` (или `--cities @файл`).

Парсер этот скрипт НЕ трогает: он лишь кладёт рядом JSON-справочник.

    python3 fetch_osm_places.py                   # города+посёлки+сёла+аулы
    python3 fetch_osm_places.py --kinds town,village
    python3 fetch_osm_places.py -o my_places.json --step 4

Результат: {"Название": "долгота,широта", ...} — тот же формат, что у
KZ_CITIES в парсере.
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

# Зеркала Overpass: если первое молчит/перегружено — идём к следующему.
ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.ru/api/interpreter",
]

#: Типы НП по убыванию людности. hamlet — это и есть «выебанные закоулки».
ALL_KINDS = ["city", "town", "village", "hamlet"]

#: Тот же bbox, что у парсера (lon_min, lat_min, lon_max, lat_max).
KZ_BBOX = (46.4, 40.5, 87.4, 55.5)

UA = "YAmap/1.0 (KZ settlements fetch; contact: local use)"


def build_query(kinds: list[str], bbox: tuple[float, float, float, float],
                timeout: int = 180) -> str:
    """Overpass QL: узлы и центры полигонов НП заданных типов внутри bbox."""
    lon_min, lat_min, lon_max, lat_max = bbox
    box = f"{lat_min},{lon_min},{lat_max},{lon_max}"
    kinds_re = "|".join(kinds)
    return (
        f"[out:json][timeout:{timeout}];"
        f'(node["place"~"^({kinds_re})$"]({box});'
        f' way["place"~"^({kinds_re})$"]({box});'
        f' relation["place"~"^({kinds_re})$"]({box}););'
        # ВАЖНО: «out center tags» — это verbosity=tags, то есть БЕЗ
        # координат: узлы приезжали бы без lat/lon и молча отбрасывались,
        # и в справочник попадали бы только НП, размеченные полигонами.
        # «out center» = verbosity по умолчанию (body: теги + координаты)
        # плюс центр для way/relation.
        f"out center;"
    )


def fetch(query: str, timeout: int = 300, retries: int = 3) -> dict:
    """Сходить в Overpass, перебирая зеркала и повторяя при 429/504."""
    last_err: Exception | None = None
    for attempt in range(retries):
        for url in ENDPOINTS:
            try:
                data = urllib.parse.urlencode({"data": query}).encode()
                req = urllib.request.Request(url, data=data,
                                             headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                # При таймауте или нехватке памяти Overpass отвечает 200,
                # кладёт объяснение в «remark» и отдаёт УРЕЗАННЫЙ список.
                # Принять его за полный — значит тихо построить справочник из
                # огрызка и затереть им прошлый полный файл.
                remark = str(data.get("remark") or "")
                if remark:
                    last_err = RuntimeError(f"Overpass: {remark[:200]}")
                    print(f"  … {url.split('/')[2]}: частичный ответ "
                          f"({remark[:120]}) — пробую другое зеркало",
                          file=sys.stderr)
                    continue
                return data
            except Exception as exc:                     # noqa: BLE001
                last_err = exc
                print(f"  … {url.split('/')[2]}: {type(exc).__name__}: {exc}",
                      file=sys.stderr)
                continue
        if attempt < retries - 1:
            wait = 5 * (attempt + 1)
            print(f"  все зеркала молчат — жду {wait}с и пробую снова",
                  file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"Overpass недоступен: {last_err}")


_LATIN = re.compile(r"[A-Za-z]")


def pick_name(tags: dict) -> str:
    """Русское название НП — его и понимает Яндекс.

    Приоритет: name:ru → name (если кириллица) → name:kk. Латиницу берём
    только когда другого нет: Яндекс по ней ищет хуже.
    """
    for key in ("name:ru", "name"):
        v = (tags.get(key) or "").strip()
        if v and not _LATIN.search(v):
            return v
    for key in ("name", "name:kk", "int_name"):
        v = (tags.get(key) or "").strip()
        if v:
            return v
    return ""


def element_coords(el: dict) -> tuple[float, float] | None:
    """(lon, lat) узла или центра way/relation."""
    if "lon" in el and "lat" in el:
        return float(el["lon"]), float(el["lat"])
    center = el.get("center") or {}
    if "lon" in center and "lat" in center:
        return float(center["lon"]), float(center["lat"])
    return None


def elements_to_places(elements: list[dict],
                       inside=None,
                       kinds: list[str] | None = None) -> dict[str, str]:
    """Элементы Overpass → {имя: "lon,lat"}.

    inside(lon, lat) — необязательный фильтр (у нас: полигон границы РК),
    чтобы приграничная полоса bbox не тащила чужие сёла.

    Одноимённые НП (в Казахстане их море: десяток «Актоганов») получают
    уточнение «(2)», «(3)» — иначе они затирали бы друг друга в словаре.
    Для поискового запроса парсер скобки отбрасывает, а вьюпорт берёт из
    координат, так что каждый такой НП обходится отдельно и на своём месте.
    """
    out: dict[str, str] = {}
    counts: dict[str, int] = {}
    kept_kinds: dict[str, int] = {}
    skipped_outside = skipped_noname = skipped_nocoord = 0
    # Один и тот же объект приезжает дважды, если страна резалась на полосы
    # (--step): bbox включает границу, и объект на стыке попадает в обе
    # выборки. Без дедупа он становился фантомным «Имя (2)» с теми же
    # координатами — парсер честно обходил его второй раз впустую.
    seen_ids: set[tuple] = set()
    unique: list[dict] = []
    for el in elements:
        ident = (el.get("type"), el.get("id"))
        if ident != (None, None):
            if ident in seen_ids:
                continue
            seen_ids.add(ident)
        unique.append(el)
    # Нумерация одноимённых НП обязана быть ОДИНАКОВОЙ между запусками:
    # имена «Актоган (2)» уходят в резюме, и если после перевыкачки номер
    # достанется другому селу, докачка пропустит одно и соберёт другое
    # дважды. Порядок ответа Overpass не гарантирован — сортируем сами.
    elements = sorted(unique, key=lambda e: (str(e.get("type") or ""),
                                             int(e.get("id") or 0)))
    for el in elements:
        tags = el.get("tags") or {}
        if kinds and tags.get("place") not in kinds:
            continue
        name = pick_name(tags)
        if not name:
            skipped_noname += 1
            continue
        coords = element_coords(el)
        if not coords:
            skipped_nocoord += 1
            continue
        lon, lat = coords
        if inside is not None and not inside(lon, lat):
            skipped_outside += 1
            continue
        n = counts.get(name, 0) + 1
        counts[name] = n
        key = name if n == 1 else f"{name} ({n})"
        out[key] = f"{lon:.4f},{lat:.4f}"
        kept_kinds[tags.get("place", "?")] = kept_kinds.get(tags.get("place", "?"), 0) + 1
    if skipped_outside or skipped_noname or skipped_nocoord:
        print(f"  отброшено: вне границы РК {skipped_outside}, "
              f"без названия {skipped_noname}, без координат {skipped_nocoord}",
              file=sys.stderr)
    if skipped_nocoord > len(out):
        print("  ВНИМАНИЕ: без координат отброшено больше, чем сохранено — "
              "похоже, Overpass вернул ответ без координат.", file=sys.stderr)
    if kept_kinds:
        print("  по типам: " + ", ".join(f"{k}={v}" for k, v in
                                          sorted(kept_kinds.items(),
                                                 key=lambda x: -x[1])),
              file=sys.stderr)
    return out


def _load_border_filter():
    """Фильтр «точка внутри РК» из парсера. Нет парсера рядом — работаем без него."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import yandex_parser as yp
        if len(yp.KZ_BORDER) >= 3:
            return yp._point_in_kz
    except Exception as exc:                             # noqa: BLE001
        print(f"  (полигон границы не подключён: {exc})", file=sys.stderr)
    return None


def _dump(elements: list[dict], args, inside, kinds: list[str],
          partial: bool = False) -> dict[str, str]:
    """Собрать справочник и записать его атомарно. Возвращает записанное."""
    places = elements_to_places(elements, inside=inside, kinds=kinds)
    if not places:
        return {}
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(places, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    tmp.replace(out)
    if partial:
        print(f"  промежуточно сохранено: {len(places)} НП → {out}",
              file=sys.stderr)
    return places


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Выкачать населённые пункты Казахстана из OpenStreetMap")
    ap.add_argument("-o", "--out", default="kz_osm_places.json",
                    help="куда сохранить справочник (по умолч. kz_osm_places.json)")
    ap.add_argument("--kinds", default=",".join(ALL_KINDS),
                    help="типы НП через запятую: city,town,village,hamlet "
                         "(по умолч. все четыре)")
    ap.add_argument("--step", type=float, default=0,
                    help="резать страну на полосы по N градусов широты "
                         "(по умолч. 0 = одним запросом; ставь 4-5, если "
                         "Overpass валится по таймауту)")
    ap.add_argument("--timeout", type=int, default=300,
                    help="таймаут HTTP-запроса, сек (по умолч. 300)")
    ap.add_argument("--no-clip", action="store_true",
                    help="не резать по полигону границы РК (быстрее, но "
                         "затащит приграничные сёла России и Узбекистана)")
    args = ap.parse_args()

    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
    bad = [k for k in kinds if k not in ALL_KINDS]
    if bad:
        print(f"Неизвестные типы: {bad}. Доступны: {ALL_KINDS}", file=sys.stderr)
        return 2

    inside = None if args.no_clip else _load_border_filter()
    if inside is None and not args.no_clip:
        print("ВНИМАНИЕ: фильтр границы недоступен — в выгрузку попадут "
              "приграничные НП соседних стран.", file=sys.stderr)

    lon_min, lat_min, lon_max, lat_max = KZ_BBOX
    boxes = []
    if args.step and args.step > 0:
        lat = lat_min
        while lat < lat_max:
            top = min(lat + args.step, lat_max)
            boxes.append((lon_min, lat, lon_max, top))
            lat = top
    else:
        boxes.append(KZ_BBOX)

    print(f"Типы НП: {', '.join(kinds)}")
    print(f"Запросов к Overpass: {len(boxes)}")

    places: dict[str, str] = {}
    elements: list[dict] = []
    for i, box in enumerate(boxes, 1):
        print(f"[{i}/{len(boxes)}] полоса широт {box[1]:.1f}..{box[3]:.1f}° …")
        try:
            data = fetch(build_query(kinds, box), timeout=args.timeout)
        except RuntimeError as exc:
            # Полосы копились только в памяти: отказ на последней терял всю
            # выкачку целиком. Сохраняем то, что уже есть, и идём дальше.
            print(f"  полоса не далась: {exc}", file=sys.stderr)
            if elements:
                _dump(elements, args, inside, kinds, partial=True)
            continue
        got = data.get("elements") or []
        print(f"  получено элементов: {len(got)}")
        elements.extend(got)
        if i < len(boxes):
            time.sleep(2)          # вежливость к публичному инстансу

    places = _dump(elements, args, inside, kinds)
    if not places:
        print("Пусто — ничего не сохраняю.", file=sys.stderr)
        return 1
    out = Path(args.out)
    print(f"\nГотово: {len(places)} населённых пунктов → {out}")
    print(f"Теперь можно гнать парсер по ним:\n"
          f"  ./run.sh --all-cities --cities osm --category gt-село "
          f"-o kz_osm.xlsx --workers 2 --api-intercept")
    return 0


if __name__ == "__main__":
    sys.exit(main())
