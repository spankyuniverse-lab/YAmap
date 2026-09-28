"""Выдача, ушедшая к тёзке, капча без «собрано», НП по адресу.

Три дыры из прогона 13.09.2026:
  • «Заправки Октябрьский» Яндекс уводил в российский Октябрьский — по 76 НП
    из 504 не собрано ничего своего; теперь повтор голой рубрики по карте НП;
  • нерешённая капча давала пустой список, и НП уходил в .cities.json —
    докачка его больше не трогала;
  • точка записывалась на НП поиска: 1249 точек Астаны числились за селом
    Кабанбай батыра, и «Сводка по НП» врала.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import yandex_parser as y  # noqa: E402

fails: list[str] = []


def check(cond, msg, extra=""):
    print(("OK   | " if cond else "FAIL | ") + msg + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(msg)


def km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    h = (math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2)
         * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 6371 * 2 * math.asin(math.sqrt(h))


# ---------------------------------------------------------------- капча ----
def captcha_cases() -> None:
    print("--- нерешённая капча: НП не «собран» ---")
    real = y.run_category_parser
    calls: list[str] = []
    stuck = {"Астана"}

    def fake(city, categories, output, **kw):
        calls.append(city)
        if city in stuck and kw.get("captcha_missed") is not None:
            kw["captcha_missed"].append("Заправки")
            return {}
        o = y.Organization(name=f"АЗС-{city}", address=f"{city}, ул. 1",
                           org_id=f"id-{city}", search_query="Заправки", city=city)
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        y.save_xlsx_by_categories({"Заправки": [o]}, Path(output))
        return {"Заправки": [o]}

    y.run_category_parser = fake
    # Пауза «остыть после капчи» — намеренная; против фейка её ужимаем.
    saved_pacing, y.PACING = y.PACING, 0.001
    try:
        out = HERE / "out" / "drift_cap.xlsx"
        cities = ["Алматы", "Астана", "Шымкент"]
        y.run_cities_parser(cities=cities, categories=["gt-азс"], output=str(out))
        done = set(json.loads((out.parent / "drift_cap.cities.json").read_text("utf-8")))
        check(done == {"Алматы", "Шымкент"},
              "НП с нерешённой капчей не записан в .cities.json", sorted(done))

        calls.clear()
        stuck.clear()
        y.run_cities_parser(cities=cities, categories=["gt-азс"], output=str(out))
        check(calls == ["Астана"], "повторный запуск добирает именно его", calls)
        done = set(json.loads((out.parent / "drift_cap.cities.json").read_text("utf-8")))
        check(done == set(cities), "после добора НП отмечен собранным", sorted(done))

        # Капча не уходит НП за НП — это бан, крутиться дальше незачем.
        calls.clear()
        stuck.update(y.KZ_CITIES_MAJOR)
        out2 = HERE / "out" / "drift_ban.xlsx"
        y.run_cities_parser(cities=list(y.KZ_CITIES_MAJOR), categories=["gt-азс"],
                            output=str(out2))
        check(len(calls) == y.MAX_CAPTCHA_NP_STREAK,
              f"после {y.MAX_CAPTCHA_NP_STREAK} НП подряд с капчей сбор встаёт",
              len(calls))
        check(y._captcha_stop_flag(out2).exists(),
              "остановка на капче оставляет флаг — родитель не бросится добирать")
    finally:
        y.run_category_parser = real
        y.PACING = saved_pacing

    check(not hasattr(y, "_try_click_captcha"),
          "автоклика по капче нет: парсер её не решает")


# ---------------------------------------------------------- НП по адресу ----
def place_cases() -> None:
    print("\n--- НП по адресу, а не по месту поиска ---")
    y.set_countries(["kz"])
    O = y.Organization
    cases = [
        (O(name="a", locality="Астана", latitude="51.13", longitude="71.43",
           city="Кабанбай батыра"), "Астана", "город из адреса, а не село поиска"),
        (O(name="b", locality="село Отеген батыра", latitude="43.42",
           longitude="77.01"), "Отеген батыр", "родительный падеж и «село»"),
        (O(name="c", locality="Қаскелең", latitude="43.2", longitude="76.62"),
         "Каскелен", "казахские буквы"),
        (O(name="d", locality="Іргелі ауылы", latitude="43.23", longitude="76.77"),
         "Иргели", "«… ауылы»"),
        (O(name="e", locality="Актау", latitude="43.65", longitude="51.17",
           city="Актау (Карагандинская)"), "Актау", "тёзка разведён по координатам"),
        (O(name="f", locality="село Мичуринское", latitude="50", longitude="70"),
         "Мичуринское", "НП вне справочника — имя из адреса"),
        (O(name="g", city="Достык"), "Достык", "адреса нет — НП поиска"),
        (O(name="h", locality="Орал", latitude="51.2", longitude="51.37"),
         "Уральск", "казахское имя города"),
    ]
    for org, want, why in cases:
        got = y._place_of(org)
        check(got == want, f"{why}: {org.locality or '—'} → {want}", got)

    # Сводка и разрез «города / сёла» — по НП из адреса
    out = HERE / "out" / "drift_place.xlsx"
    # Два НП поиска (иначе листов-срезов нет: книга одного НП — это копия
    # сводного листа), а точка Астаны нашлась из пригородного села.
    astana = O(name="АЗС на Кенесары", locality="Астана", latitude="51.16",
               longitude="71.44", city="Караоткель", org_id="p1",
               search_query="Заправки")
    aul = O(name="АЗС в ауле", locality="село Кабанбай батыра", latitude="50.99",
            longitude="71.45", city="Кабанбай батыра", org_id="p2",
            search_query="Заправки")
    y.save_xlsx_by_categories({"Заправки": [astana, aul]}, out)
    from openpyxl import load_workbook
    wb = load_workbook(out)
    summary = {r[0]: r[1] for r in wb["Сводка по НП"].iter_rows(min_row=3, values_only=True)}
    check(summary.get("Астана") == "Крупный город",
          "точка Астаны в сводке у Астаны, а не у села поиска", summary)
    check("Караоткель" not in summary, "у села поиска её нет", summary)
    check(summary.get("Кабанбай батыра") == "Аул/село", "а сельская — у села", summary)
    head = [c.value for c in wb["Все результаты"][1]]
    check("НП (по адресу)" in head and "Город (прогон)" in head,
          "обе колонки в книге: где нашли и где стоит", head[-3:])

    # Старая книга «до Супермаркетов» переразмечается при чтении
    old = HERE / "out" / "drift_old.xlsx"
    sup = O(name="Magnum", category="Супермаркет, Магазин продуктов",
            category_seonames="supermarket; grocery", org_id="s1",
            search_query="Продуктовые магазины", gt_slug="gt_produktovye",
            source_query="Магазин продуктов", city="Алматы")
    y.save_xlsx_by_categories({"Продуктовые магазины": [sup]}, old)
    back = y.ResumeManager(old).existing_orgs()
    check(bool(back) and back[0].search_query == "Супермаркеты"
          and back[0].gt_slug == "gt_supermarkety",
          "старая строка супермаркета уходит в «Супермаркеты»",
          back and (back[0].search_query, back[0].gt_slug))
    check(y.ResumeManager(old).is_query_done("Магазин продуктов"),
          "докачка по-прежнему видит запрос выполненным")
    y.set_countries(None)


# ------------------------------------------------- дрейф без браузера ----
class FakePage:
    """Страница-заглушка: помнит переходы и паузы, ничего не грузит."""

    def __init__(self, fail_goto: bool = False):
        self.fail_goto = fail_goto
        self.gotos: list[str] = []
        self.waits: list[int] = []
        self.url = "about:blank"

    def goto(self, url, **kw):
        self.gotos.append(url)
        if self.fail_goto:
            raise TimeoutError("Timeout 30000ms exceeded")

    def wait_for_timeout(self, ms):
        self.waits.append(ms)


def drift_units() -> None:
    print("\n--- дрейф: признак, повтор, сбои ---")
    y.set_countries(["kz"])
    O = y.Organization
    center = y.PLACES_ALL["Павлодар"]
    lon0, lat0 = (float(x) for x in center.split(","))

    def org(i, dlat, dlon, addr="Казахстан, ул. 1"):
        return O(name=f"o{i}", org_id=f"i{i}", address=addr,
                 latitude=str(lat0 + dlat), longitude=str(lon0 + dlon))

    # Павлодар: первая страница своя, хвост скролла уехал к Барнаулу — не дрейф
    local = [org(i, 0.01 * (i % 5), 0.01) for i in range(20)]
    tail = [org(100 + i, 1.0, 0.5) for i in range(80)]
    check(not y._is_drifted(local + tail, center)[0],
          "законно расширенная к хвосту выдача (Павлодар → Барнаул) — не дрейф")
    away = [org(200 + i, 2.5, -1.0) for i in range(15)]
    check(y._is_drifted(away, center)[0], "первая страница за 250 км — дрейф")

    # НП вне справочника: центра нет — и судить не по чему
    y.set_viewport(city="Такого-НП-нет")
    check(not y.MAP_LL_IS_PLACE, "запасной центр страны не считается центром НП")
    y.set_viewport(city="Павлодар")
    check(y.MAP_LL_IS_PLACE, "центр НП из справочника — считается")

    real = y._search_with_retry
    try:
        # Повтор: пауза перед ним, ушедшие-но-правдоподобные точки остаются
        page = FakePage()
        near_retry = [org(300 + i, 0.02, 0.02) for i in range(5)]
        y._search_with_retry = lambda *a, **k: list(near_retry)
        plausible_far = org(400, 0.6, 0.3)             # ~70 км, в пределах 1.2°
        foreign = org(401, 3.0, 1.0, addr="Россия, Барнаул")
        got, started = y._retry_if_drifted(
            page, away + [plausible_far, foreign], "Заправки", "Заправки Павлодар",
            center, 50, 1.0, False, True)
        check(started is not None, "повтор был")
        check(page.waits and page.waits[0] >= y._pace(3000) * 0.5,
              "перед повтором — пауза как между рубриками", page.waits[:2])
        check(any("z=12" in u for u in page.gotos), "повтор идёт по карте НП на z=12",
              page.gotos)
        names = {o.name for o in got}
        check({o.name for o in near_retry} <= names, "находки повтора в выдаче")
        check("o400" in names, "правдоподобная дальняя точка не выброшена")
        check("o401" not in names and not any(n.startswith("o2") for n in names),
              "зарубежные и через полстраны — выброшены")
        check(y.MAP_LL == center, "вьюпорт после повтора восстановлен", y.MAP_LL)

        # Сбой перехода на карту — повтор отменяется, НП не обрывается
        bad = FakePage(fail_goto=True)
        same, _ = y._retry_if_drifted(bad, away, "Заправки", "Заправки Павлодар",
                                      center, 50, 1.0, False, True)
        check(same == away, "сбой goto при повторе не роняет рубрику")
    finally:
        y._search_with_retry = real

    # Капча посреди скролла: повторяем, а держится до конца — «не решена»
    real_sac = y._search_and_collect
    calls = []

    def sac(*a, **k):
        calls.append(1)
        raise y.CaptchaMidScroll("Капча посреди скролла — собрано лишь 7")

    y._search_and_collect = sac
    try:
        try:
            y._search_with_retry(FakePage(), "Заправки Павлодар", 50, 1.0, False)
            raised = False
        except y.CaptchaUnsolved:
            raised = True
        check(raised and len(calls) == 3,
              "капча посреди скролла: 3 попытки, потом «не решена», а не «пусто»",
              (raised, len(calls)))
    finally:
        y._search_and_collect = real_sac

    # НП по адресу: казахские написания, дачи, псевдоним города у села
    for loc, la, lo, city, want_place, want_major in [
            ("Екібастұз", "51.72", "75.32", "Солнечный", "Экибастуз", ""),
            ("Балқаш", "46.85", "74.98", "", "Балхаш", ""),
            ("Тасбөгет кентi", "44.77", "65.55", "", "Тасбогет", ""),
            ("садоводческий кооператив Массив", "50.41", "80.25", "Абай", "Семей", "Семей"),
            ("село Жамбыл", "43.2", "76.7", "Иргели", "Жамбыл", ""),
            # известное имя, но за 300 км — адресу не верим, НП поиска
            ("Усть-Каменогорск", "50.75", "78.54", "Курчатов", "Курчатов", ""),
            # село «Садовое» — не дача
            ("Садовое", "52.1", "76.9", "Павлодар", "Садовое", ""),
    ]:
        o = O(name="x", locality=loc, latitude=la, longitude=lo, city=city)
        y._fill_places([o])             # как при сохранении книги
        check((o.place, y._major_city_of(o)) == (want_place, want_major),
              f"{loc} → {want_place}{' (крупный)' if want_major else ''}",
              (y._place_of(o), y._major_city_of(o)))
    # Метка страны у тёзки не ломается латиницей → кириллицей
    y.set_countries(None)
    check(y._place_norm("Благовещенка [KG]") == "благовещенка",
          "метка [KG] срезается", y._place_norm("Благовещенка [KG]"))


# ------------------------------------------------------ дрейф (браузер) ----
def drift_e2e() -> None:
    print("\n--- выдача ушла к тёзке: повтор по карте НП ---")
    browser = os.environ.get("YAMAP_BROWSER_PATH")
    if not browser:
        for cand in ("/opt/pw-browsers/chromium",
                     "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
            if Path(cand).exists():
                browser = cand
                break
    if not browser:
        print("ПРОПУСК: браузер не найден (playwright/chromium не установлен)")
        return
    import mock_yandex
    base, srv = mock_yandex.start({"Заправки": 7})
    env = dict(os.environ)
    env.update({"YAMAP_BASE_URL": base, "YAMAP_BROWSER_PATH": browser,
                "YAMAP_HEADLESS": "1", "PYTHONIOENCODING": "utf-8",
                "YAMAP_PACING": os.environ.get("YAMAP_PACING", "0.02")})
    out = HERE / "out" / "drift_e2e.xlsx"
    try:
        r = subprocess.run(
            [sys.executable, str(HERE.parent / "yandex_parser.py"),
             "--all-cities", "--cities", "Алматы,Октябрьский", "--countries", "kz",
             "--category", "Заправки", "-n", "50", "-o", str(out)],
            cwd=HERE, env=env, capture_output=True, text=True, timeout=600)
    finally:
        srv.shutdown()
    log = r.stdout + r.stderr
    check(r.returncode == 0, f"прогон отработал (код {r.returncode})", log[-800:])
    drifts = log.count("выдача ушла от НП")
    check(drifts == 1, "повтор ровно один — у Октябрьского, не у Алматы", drifts)
    check("Ищу «Заправки» по карте НП" in log, "повтор — голая рубрика без названия")
    if not out.exists():
        check(False, "книга создана")
        return
    from openpyxl import load_workbook
    ws = load_workbook(out)["Все результаты"]
    head = [c.value for c in ws[1]]
    ic, ila, ilo = head.index("Город (прогон)"), head.index("Широта"), head.index("Долгота")
    rows = [r for r in ws.iter_rows(min_row=2, values_only=True)]
    okt = [r for r in rows if r[ic] == "Октябрьский"]
    lon0, lat0 = (float(x) for x in y.PLACES_ALL["Октябрьский"].split(","))
    check(len(okt) == 7, f"у Октябрьского свои 7 заправок: {len(okt)}")
    far = [r for r in okt if km(lat0, lon0, float(r[ila]), float(r[ilo])) > 40]
    check(not far, "ни одной точки из российского тёзки", len(far))
    check(sum(1 for r in rows if r[ic] == "Алматы") == 7, "Алматы собрана как обычно")


def _clean_own() -> None:
    """Свои файлы прошлого запуска — долой: докачка честно продолжила бы с них."""
    import shutil
    for junk in (HERE / "out").glob("drift_*"):
        if junk.is_dir():
            shutil.rmtree(junk, ignore_errors=True)
        else:
            junk.unlink()


def main() -> int:
    _clean_own()
    captcha_cases()
    place_cases()
    drift_units()
    drift_e2e()
    print()
    if fails:
        print(f"❌ ПРОВАЛОВ: {len(fails)}")
        return 1
    print("✅ ДРЕЙФ, КАПЧА И НП ПО АДРЕСУ ОК")
    return 0


if __name__ == "__main__":
    sys.exit(main())
