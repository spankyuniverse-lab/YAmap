# -*- coding: utf-8 -*-
"""География сельского покрытия: полигон границы, сетка, трассы, справочник НП.

Смысл теста: --country/--routes не должны ни терять территорию Казахстана
(потерянный тайл = потерянные сёла), ни метать тайлы по чужим столицам
(Ташкент/Бишкек/Омск внутри KZ_BBOX!).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import yandex_parser as yp


def _ll(s: str) -> tuple[float, float]:
    lon, lat = (float(x) for x in s.split(","))
    return lon, lat


def t_border_present():
    assert len(yp.KZ_BORDER) >= 40, (
        f"полигон границы слишком грубый: {len(yp.KZ_BORDER)} вершин")
    for lon, lat in yp.KZ_BORDER:
        assert 40.0 <= lat <= 56.5 and 45.0 <= lon <= 88.5, (
            f"вершина полигона вне разумного диапазона: {lon},{lat}")


def t_cities_inside():
    # Все известные города и сёла обязаны попадать в полигон (с полутайловым
    # буфером _tile_in_kz — как их реально видит сетка).
    for name, ll in yp.KZ_PLACES_ALL.items():
        lon, lat = _ll(ll)
        assert yp._tile_in_kz(lon, lat, 0.25), (
            f"«{name}» ({ll}) выпал за полигон границы — свип его потеряет")


def t_foreign_outside():
    # Чужие города внутри KZ_BBOX — их тайлы обязаны быть отрезаны.
    foreign = {
        "Ташкент": (69.28, 41.31),
        "Бишкек": (74.59, 42.87),
        "Омск": (73.37, 54.99),
        "Оренбург": (55.10, 51.77),
        "Самара": (50.15, 53.20),
        "Астрахань": (48.04, 46.35),
        "Новосибирск": (82.92, 55.03),
        "Нукус": (59.60, 42.46),
        "Ургенч": (60.63, 41.55),
        "Талас (КР)": (72.24, 42.52),
        "Иссык-Куль": (77.50, 42.50),
        "Середина Каспия": (49.50, 42.00),
    }
    for name, (lon, lat) in foreign.items():
        assert not yp._point_in_kz(lon, lat), (
            f"{name} ({lon},{lat}) оказался ВНУТРИ полигона границы РК")


def t_grid_clipped():
    full = yp.country_grid(0.5, clip=False)
    clipped = yp.country_grid(0.5, clip=True)
    assert len(clipped) < len(full) * 0.80, (
        f"клип почти ничего не отрезал: {len(clipped)}/{len(full)} — "
        "полигон не работает")
    assert len(clipped) > len(full) * 0.35, (
        f"клип отрезал слишком много: {len(clipped)}/{len(full)} — "
        "теряем территорию")
    # Ни один тайл не должен быть ЦЕНТРИРОВАН на чужом городе (в пределах
    # полутайла). Ближе этого нельзя — но сами приграничные Ташкент/Бишкек
    # стоят в 20-25 км от казахстанских сёл, поэтому тайл В 0.5° от чужой
    # столицы — норма (за зарубежные адреса в нём отвечает _FOREIGN_ADDR_RE,
    # см. t_foreign_addr_filter), а вот НА столице — нет.
    # Тайлы, попавшие в сетку ТОЛЬКО из-за полутайлового буфера (их центр вне
    # КЗ), у приграничных столиц — норма: чужие адреса из них режет
    # _FOREIGN_ADDR_RE. А вот тайл, чей ЦЕНТР внутри КЗ и при этом совпал с
    # чужим городом, означал бы кривой полигон — вот это и ловим.
    for t in yp.country_grid(0.25, clip=True):
        lon, lat = _ll(t)
        if not yp._point_in_kz(lon, lat):
            continue
        for flon, flat in ((69.28, 41.31), (74.59, 42.87), (73.37, 54.99),
                            (55.10, 51.77), (82.92, 55.03), (50.15, 53.20)):
            assert abs(lon - flon) > 0.125 or abs(lat - flat) > 0.125, (
                f"тайл {t} центрирован на чужом городе {flon},{flat}")


def t_foreign_addr_filter():
    # Зарубежные адреса из приграничных вьюпортов должны отсеиваться.
    foreign = [
        "Узбекистан, Ташкент, ул. Навои, 1",
        "Кыргызстан, Бишкек, пр. Чуй, 10",
        "Россия, Омская обл, Омск, ул. Ленина, 5",
        "Оренбургская обл, Орск",
        "Китай, Синьцзян, Инин",
    ]
    for a in foreign:
        assert yp._FOREIGN_ADDR_RE.search(a), f"не отсёк зарубежный адрес: {a}"
    # Казахстанские адреса фильтр трогать не должен.
    ok = [
        "Казахстан, Алматы, ул. Абая, 1",
        "Туркестанская обл, Сарыагаш, ул. Абая",
        "Жамбылская обл, Кордай",
        "Кордай, трасса А-2",
        "Костанайская обл, Карабалык",
    ]
    for a in ok:
        assert not yp._FOREIGN_ADDR_RE.search(a), f"ложно отсёк адрес КЗ: {a}"


def t_settlements():
    assert len(yp.KZ_SETTLEMENTS) >= 200, (
        f"сельских НП подозрительно мало: {len(yp.KZ_SETTLEMENTS)}")
    # Ключи уникальны по построению dict; проверяем формат координат
    for name, ll in yp.KZ_SETTLEMENTS.items():
        lon, lat = _ll(ll)
        assert 40.0 <= lat <= 56.0 and 46.0 <= lon <= 88.0, (
            f"«{name}»: координаты {ll} вне Казахстана")
    # Сёла не дублируют города (город точнее — он выигрывает в KZ_PLACES_ALL)
    dup = set(yp.KZ_SETTLEMENTS) & set(yp.KZ_CITIES_ALL)
    assert not dup, f"сёла дублируют города: {sorted(dup)[:5]}"


def t_query_place_name():
    assert yp._query_place_name("Кабанбай (Абай)") == "Кабанбай"
    assert yp._query_place_name("Шелек") == "Шелек"
    assert yp._query_place_name("Отеген батыр") == "Отеген батыр"
    # Все имена справочника дают непустой чистый текст запроса
    for name in yp.KZ_PLACES_ALL:
        q = yp._query_place_name(name)
        assert q and "(" not in q, f"кривое имя для запроса: {name!r} -> {q!r}"


def t_routes():
    assert len(yp.KZ_ROUTES) >= 10, (
        f"магистралей подозрительно мало: {len(yp.KZ_ROUTES)}")
    for name, pts in yp.KZ_ROUTES:
        assert len(pts) >= 2, f"трасса «{name}»: меньше двух точек"
        for (lon1, lat1), (lon2, lat2) in zip(pts, pts[1:]):
            # ~250 км между соседними точками максимум (1° ≈ 70-110 км)
            assert abs(lon1 - lon2) <= 3.6 and abs(lat1 - lat2) <= 2.4, (
                f"трасса «{name}»: разрыв {lon1},{lat1} → {lon2},{lat2}")

    tiles = yp.route_tiles(0.25, corridor=3)
    assert 300 <= len(tiles) <= 12000, f"странное число тайлов коридора: {len(tiles)}"
    # Коридор внутри страны (с буфером клипа)
    for t in tiles[::7]:
        lon, lat = _ll(t)
        assert yp._tile_in_kz(lon, lat, 0.25)
    # Узкий коридор — строго меньше широкого
    assert len(yp.route_tiles(0.25, corridor=1)) < len(tiles)
    # Тайлы коридора лежат на решётке национальной сетки (дедуп между режимами)
    grid = set(yp.country_grid(0.25, clip=False))
    on_grid = sum(1 for t in tiles if t in grid)
    assert on_grid >= len(tiles) * 0.95, (
        f"тайлы коридора не на решётке: {on_grid}/{len(tiles)}")


def t_city_specs():
    assert len(yp.resolve_city_list(None)) == 19
    assert len(yp.resolve_city_list("all")) == len(yp.KZ_CITIES_ALL)
    aul = yp.resolve_city_list("аулы")
    assert set(aul) == set(yp.KZ_SETTLEMENTS)
    mx = yp.resolve_city_list("макс")
    assert set(mx) == set(yp.KZ_PLACES_ALL)
    assert len(mx) == len(set(mx))


def t_rural_preset():
    qs = yp.resolve_categories(["gt-село"])
    assert qs == yp.GT_RURAL
    # Каждый сельский запрос обязан ложиться в GT-категорию (лист выгрузки)
    for q in qs:
        label, slug = yp.category_of(q)
        assert slug.startswith("gt_"), f"запрос «{q}» без GT-категории"
    assert yp.resolve_categories(["gt-аул"]) == yp.GT_RURAL


def main() -> int:
    fails = 0
    for fn in (t_border_present, t_cities_inside, t_foreign_outside,
               t_grid_clipped, t_foreign_addr_filter, t_settlements,
               t_query_place_name, t_routes, t_city_specs, t_rural_preset):
        try:
            fn()
            print(f"  OK  {fn.__name__}")
        except AssertionError as exc:
            print(f"FAIL  {fn.__name__}: {exc}")
            fails += 1
    return fails


if __name__ == "__main__":
    sys.exit(main())
