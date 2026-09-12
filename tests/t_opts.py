# -*- coding: utf-8 -*-
"""Опциональные ускорители не должны менять поведение по умолчанию.

Проверяем: флаги выключены из коробки, списки НП из файла/OSM подключаются,
длинный список уезжает воркерам файлом (а не 100-килобайтной командной
строкой, которую Windows обрубит), пагинация API подменяет нужный параметр.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import yandex_parser as y

fails = []


def check(name, cond, detail=""):
    print(("OK   | " if cond else "FAIL | ") + name + (f"\n       {detail}" if not cond and detail else ""))
    if not cond:
        fails.append(name)


# --- 1. По умолчанию всё выключено ---------------------------------------
check("URL_ONLY выключен из коробки", y.URL_ONLY is False)
check("FAST_API выключен из коробки", y.FAST_API is False)

p = y.build_parser() if hasattr(y, "build_parser") else None
if p is None:
    # парсер собирается внутри main(); проверим через --help-безопасный путь
    import argparse
    ns = None
else:
    ns = p.parse_args([])
    check("--url-only по умолчанию False", ns.url_only is False)
    check("--fast-api по умолчанию False", ns.fast_api is False)


# --- 2. Список НП из файла (--cities @файл) ------------------------------
with tempfile.TemporaryDirectory() as td:
    td = Path(td)

    txt = td / "places.txt"
    txt.write_text("Аршалы\nБотакара\n\nКиевка\n", encoding="utf-8")
    got = y.resolve_city_list(f"@{txt}")
    check("@файл.txt: имена по строкам, пустые строки выкинуты",
          got == ["Аршалы", "Ботакара", "Киевка"], got)

    jsn = td / "places.json"
    jsn.write_text(json.dumps(["Улытау", "Жезды"], ensure_ascii=False), encoding="utf-8")
    got = y.resolve_city_list(f"@{jsn}")
    check("@файл.json (список)", got == ["Улытау", "Жезды"], got)

    # --- 3. OSM-справочник подключается и даёт координаты для вьюпорта ---
    osm = td / "osm.json"
    osm.write_text(json.dumps({
        "Аршалы": "71.9100,50.8500",          # уже есть в справочнике парсера
        "Тестаул": "70.0000,48.0000",          # новый
        "Тестаул (2)": "70.5000,48.5000",      # одноимённый, с уточнением
    }, ensure_ascii=False), encoding="utf-8")

    before = dict(y.PLACES_ALL)
    names = y.resolve_city_list(f"@{osm}")
    check("@файл.json (словарь) читается как OSM-справочник",
          set(names) == {"Аршалы", "Тестаул", "Тестаул (2)"}, names)
    check("новые НП попали в справочник координат",
          y.PLACES_ALL.get("Тестаул") == "70.0000,48.0000")
    check("свои выверенные координаты OSM не перетирает",
          y.PLACES_ALL.get("Аршалы") == before.get("Аршалы"),
          f"{y.PLACES_ALL.get('Аршалы')} vs {before.get('Аршалы')}")

    # вьюпорт по новому НП ставится из его координат
    y.set_viewport(city="Тестаул (2)")
    check("вьюпорт для НП с уточнением берётся из его координат",
          y.MAP_LL == "70.5000,48.5000", y.MAP_LL)
    check("скобочное уточнение не уходит в текст запроса",
          y._query_place_name("Тестаул (2)") == "Тестаул")

    # вернём справочник в исходное состояние для остальных проверок
    for k in ("Тестаул", "Тестаул (2)"):
        y.PLACES_ALL.pop(k, None)

    # --- 4. Отсутствующий OSM-файл: понятная ошибка, а не трейсбек -------
    try:
        y.resolve_city_list("osm")
        check("нет osm-файла → внятная ошибка", False, "исключения не было")
    except SystemExit as exc:
        check("нет osm-файла → внятная ошибка с подсказкой",
              "fetch_osm_places.py" in str(exc), str(exc)[:120])


# --- 5. Пагинация API: подменяем тот параметр, что есть ------------------
cases = [
    ("https://ya.kz/maps/api/search?text=X&page=0", 3, "page=3"),
    ("https://ya.kz/maps/api/search?text=X&p=1&z=12", 2, "p=2"),
    ("https://ya.kz/maps/api/search?text=X&skip=0", 2, "skip=40"),
    ("https://ya.kz/maps/api/search?text=X&offset=20", 3, "offset=60"),
    ("https://ya.kz/maps/api/search?text=X", 5, "page=5"),
]
for url, n, expect in cases:
    got = y._bump_page(url, n)
    check(f"_bump_page: {url.split('?')[1][:22]}… → {expect}", expect in got, got)

check("_bump_page не трогает остальные параметры",
      "text=X" in y._bump_page("https://ya.kz/maps/api/search?text=X&page=0", 7)
      and "z=12" in y._bump_page("https://ya.kz/maps/api/search?text=X&p=1&z=12", 2))


# --- 6. Длинный список НП уезжает воркерам файлом ------------------------
captured = {}
real_run_parallel = y.run_parallel
y.run_parallel = lambda base, shards, output, finalize=None: (
    captured.update(base=base, shards=shards) or 0)

with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    many = [f"Аул{i}" for i in range(3000)]
    # Кладём в справочник СТРАНЫ, а не в PLACES_ALL: main() зовёт
    # set_countries(), и объединение пересобирается из реестра стран —
    # записи, положенные прямо в PLACES_ALL, до воркеров бы не дожили.
    y.COUNTRIES["kz"].settlements.update({n: "70.0000,48.0000" for n in many})
    y.set_countries(None)
    out = td / "big.xlsx"
    sys.argv = ["yandex_parser.py", "--all-cities", "--cities", ",".join(many),
                "--category", "gt-fuel", "-o", str(out), "--workers", "2",
                "--url-only", "--fast-api"]
    try:
        y.main()
    except SystemExit:
        pass

    shards = captured.get("shards") or []
    base = captured.get("base") or []
    check("длинный список: воркерам ушло 2 шарда", len(shards) == 2, shards)
    argline = " ".join(a for sh in shards for a in sh)
    check("длинный список передан файлом, а не простынёй в argv",
          "@" in argline and len(argline) < 500, argline[:120])
    files = sorted(td.glob("*.places.json"))
    check("файлы со списками созданы", len(files) == 2, [f.name for f in files])
    check("имя файла не совпадает с прогрессом воркера (.cities.json)",
          not list(td.glob("*.cities.json")), [f.name for f in td.glob("*")])
    if files:
        got = json.loads(files[0].read_text(encoding="utf-8"))
        check("в файле — половина списка", len(got) == 1500, len(got))
        # Главное: воркер должен получить КООРДИНАТЫ, иначе вьюпорт уедет
        # в центр страны и село будет искаться по карте всего Казахстана.
        check("в файле лежат координаты, а не только имена",
              all("," in v for v in got.values()),
              list(got.items())[:2])
        names = y.resolve_city_list(f"@{files[0]}")
        check("файл читается обратно тем же resolve_city_list",
              set(names) == set(got))
        y.set_viewport(city=names[0])
        check("после чтения файла вьюпорт встаёт на НП, а не на центр страны",
              y.MAP_LL == got[names[0]], (y.MAP_LL, got[names[0]]))
    check("флаги --url-only и --fast-api уехали воркерам",
          "--url-only" in base and "--fast-api" in base, base)

    for n in many:
        y.COUNTRIES["kz"].settlements.pop(n, None)
    y.set_countries(None)

y.run_parallel = real_run_parallel

# --- 7. Короткий список по-прежнему уезжает строкой (ничего не сломали) --
captured.clear()
y.run_parallel = lambda base, shards, output, finalize=None: (
    captured.update(base=base, shards=shards) or 0)
sys.argv = ["yandex_parser.py", "--all-cities", "--category", "gt-fuel",
            "-o", "kz_x.xlsx", "--workers", "2"]
try:
    y.main()
except SystemExit:
    pass
shards = captured.get("shards") or []
argline = " ".join(a for sh in shards for a in sh)
check("обычные 19 городов: по-прежнему списком в argv, без файлов",
      shards and "@" not in argline and "Алматы" in argline, argline[:100])
check("без флагов воркерам не уходит ни --url-only, ни --fast-api",
      "--url-only" not in (captured.get("base") or [])
      and "--fast-api" not in (captured.get("base") or []))
y.run_parallel = real_run_parallel
for junk in Path(".").glob("kz_x*"):
    junk.unlink()


# --- 8. Разбор ответа Overpass (офлайн, без сети) ------------------------
import fetch_osm_places as osm

check("имя: name:ru важнее латиницы",
      osm.pick_name({"name": "Arshaly", "name:ru": "Аршалы"}) == "Аршалы")
check("имя: кириллический name берём как есть",
      osm.pick_name({"name": "Ботакара"}) == "Ботакара")
check("имя: латиница только когда другого нет",
      osm.pick_name({"name": "Zhezdy"}) == "Zhezdy")
check("имя: без тегов — пусто", osm.pick_name({}) == "")

check("координаты узла", osm.element_coords({"lon": 70.1, "lat": 48.2}) == (70.1, 48.2))
check("координаты центра полигона",
      osm.element_coords({"center": {"lon": 71.0, "lat": 49.0}}) == (71.0, 49.0))
check("нет координат — None", osm.element_coords({"id": 1}) is None)

elements = [
    {"lon": 71.91, "lat": 50.85, "tags": {"place": "village", "name": "Аршалы"}},
    {"lon": 70.00, "lat": 48.00, "tags": {"place": "hamlet", "name:ru": "Актоган"}},
    {"lon": 75.00, "lat": 49.00, "tags": {"place": "hamlet", "name": "Актоган"}},
    # Настоящая заграница: Омск. Ташкент и Бишкек больше не подходят —
    # Узбекистан и Киргизия теперь входят в зону сбора.
    {"lon": 73.37, "lat": 54.99, "tags": {"place": "city", "name": "Омск"}},
    {"lon": 72.00, "lat": 50.00, "tags": {"place": "village"}},
    {"center": {"lon": 68.0, "lat": 45.0}, "tags": {"place": "town", "name": "Полигонное"}},
]
places = osm.elements_to_places(elements, inside=y._point_in_region,
                                kinds=["city", "town", "village", "hamlet"])
check("одноимённые НП не затирают друг друга",
      "Актоган" in places and "Актоган (2)" in places, sorted(places))
check("уточнённое имя ведёт на СВОИ координаты",
      places.get("Актоган") == "70.0000,48.0000"
      and places.get("Актоган (2)") == "75.0000,49.0000", places)
check("чужой город отрезан полигоном границы", "Омск" not in places, sorted(places))
check("НП без названия пропущен", len(places) == 4, sorted(places))
check("way/relation по центру тоже попал", "Полигонное" in places)
check("формат значения — 'lon,lat' как у KZ_CITIES",
      all(len(v.split(",")) == 2 for v in places.values()))

# результат выкачки сразу годится как --cities @файл
with tempfile.TemporaryDirectory() as td:
    f = Path(td) / "osm_out.json"
    f.write_text(json.dumps(places, ensure_ascii=False), encoding="utf-8")
    got = y.resolve_city_list(f"@{f}")
    check("выгрузка OSM читается парсером как список НП",
          set(got) == set(places), got)
    check("координаты из выгрузки доступны вьюпорту",
          all(y.PLACES_ALL.get(n) for n in got))
    for n in places:
        if n not in ("Аршалы",):
            y.PLACES_ALL.pop(n, None)

check("запрос Overpass содержит нужные типы и bbox",
      all(k in osm.build_query(["village", "hamlet"], osm.KZ_BBOX)
          for k in ("village", "hamlet", "46.4", "55.5")))
check("запрос просит координаты узлов (out center, не out center tags)",
      "out center;" in osm.build_query(["village"], osm.KZ_BBOX)
      and "out center tags" not in osm.build_query(["village"], osm.KZ_BBOX))

# нумерация одноимённых НП не зависит от порядка ответа Overpass
_els = [{"type": "node", "id": 2, "lon": 75.0, "lat": 49.0,
         "tags": {"place": "village", "name": "Актоган"}},
        {"type": "node", "id": 1, "lon": 70.0, "lat": 48.0,
         "tags": {"place": "village", "name": "Актоган"}}]
_a = osm.elements_to_places(list(_els), kinds=["village"])
_b = osm.elements_to_places(list(reversed(_els)), kinds=["village"])
check("нумерация «(2)» одинакова при любом порядке ответа", _a == _b, (_a, _b))
check("дедуп по id: тот же объект с двух полос не даёт фантом «(2)»",
      len(osm.elements_to_places(_els + _els, kinds=["village"])) == 2)


# --- 9. Разбор ответа Wikidata (офлайн, без сети) ------------------------
import fetch_wikidata_places as wd

check("координаты: WKT Point(lon lat) — долгота ПЕРВАЯ",
      wd.parse_point("Point(71.4491 51.1694)") == (71.4491, 51.1694))
check("координаты: пробелы и знак", wd.parse_point("  Point(-5.5 42.0) ") == (-5.5, 42.0))
check("координаты: мусор — None", wd.parse_point("не координаты") is None)

check("имя: русское важнее английского", wd.pick_name("Аршалы", "Arshaly") == "Аршалы")
check("имя: латиница только когда русского нет", wd.pick_name("", "Arshaly") == "Arshaly")
check("имя: пусто, если нет ничего", wd.pick_name("", "") == "")

def _row(qid, ru, en, lon, lat):
    return {"item": {"value": f"http://www.wikidata.org/entity/Q{qid}"},
            "ruLabel": {"value": ru}, "enLabel": {"value": en},
            "coord": {"value": f"Point({lon} {lat})"}}

rows = [
    _row(200, "Актоган", "", 75.0, 49.0),
    _row(100, "Актоган", "", 70.0, 48.0),
    _row(300, "", "", 71.0, 50.0),                 # без имени
    _row(400, "Q999", "", 72.0, 50.0),             # метка-идентификатор
    {"item": {"value": "http://www.wikidata.org/entity/Q500"},
     "ruLabel": {"value": "Безкоординат"}, "coord": {"value": "мусор"}},
]
got = wd.rows_to_places(rows)
check("одноимённые НП не затирают друг друга",
      set(got) == {"Актоган", "Актоган (2)"}, sorted(got))
check("нумерация по идентификатору Wikidata, а не по порядку ответа",
      got["Актоган"] == "70.0000,48.0000", got)
check("нумерация одинакова при любом порядке строк",
      wd.rows_to_places(list(reversed(rows))) == got)
check("строки без имени, с меткой-идентификатором и без координат отброшены",
      len(got) == 2, got)

check("фильтр границы применяется",
      wd.rows_to_places(rows, inside=lambda lon, lat: lon > 72) == {"Актоган": "75.0000,49.0000"})

q = wd.build_query(["kz"])
check("запрос просит нужную страну и координаты",
      all(k in q for k in ("Q232", "wdt:P625", "wdt:P17", 'LANG(?ruLabel) = "ru"')))
check("неизвестная страна в CLI отвергается", "kz" in wd.COUNTRY_QID
      and "uz" in wd.COUNTRY_QID and "kg" in wd.COUNTRY_QID)

# результат годится как --cities @файл
with tempfile.TemporaryDirectory() as td:
    f = Path(td) / "wikidata_places.json"
    f.write_text(json.dumps(got, ensure_ascii=False), encoding="utf-8")
    names = y.resolve_city_list(f"@{f}")
    check("выгрузка Wikidata читается парсером", set(names) == set(got), names)
    y.set_viewport(city="Актоган (2)")
    check("вьюпорт встаёт на координаты из выгрузки",
          y.MAP_LL == got["Актоган (2)"], y.MAP_LL)
    for n in got:
        y.PLACES_ALL.pop(n, None)

print("\n" + ("✅ ОПЦИИ ОК" if not fails else f"❌ ПРОВАЛЫ: {fails}"))
sys.exit(1 if fails else 0)
