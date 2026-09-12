# -*- coding: utf-8 -*-
"""Регресс на «молчаливые» баги: когда парсер работает, а данных нет.

Все проверки тут про один класс ошибок — парсер отдаёт пустоту или огрызок
и НИЧЕГО об этом не говорит. Такое не ловится глазами: прогон идёт сутки,
файл не пустой, а половины полей нет.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import yandex_parser as y

fails = []


def check(name, cond, detail=""):
    print(("OK   | " if cond else "FAIL | ") + name
          + (f"\n       {detail}" if not cond and detail else ""))
    if not cond:
        fails.append(name)


class FakeLoc:
    def __init__(self, n, visible):
        self._n, self._v = n, visible

    @property
    def first(self):
        return self

    def count(self):
        return self._n

    def is_visible(self):
        return self._v


class FakePage:
    """Страница, где селектор совпадает, но элемент скрыт (или наоборот)."""

    def __init__(self, url="https://yandex.kz/maps/?text=кафе", hits=None):
        self.url = url
        self.hits = hits or {}

    def locator(self, sel):
        n, vis = self.hits.get(sel, (0, False))
        return FakeLoc(n, vis)


def main() -> int:
    # ---------- капча: скрытый виджет не капча ----------
    hidden = FakePage(hits={"[class*='smartcaptcha']": (1, False)})
    check("предзагруженный скрытый SmartCaptcha не считается капчей",
          y.detect_captcha(hidden) is False)

    shown = FakePage(hits={"[class*='smartcaptcha']": (1, True)})
    check("видимая капча — капча", y.detect_captcha(shown) is True)

    # Слово «captcha» в поисковом запросе — не бан.
    q = FakePage(url="https://yandex.kz/maps/?text=captcha%20service")
    check("слово captcha в запросе не принимается за бан",
          y.detect_captcha(q) is False, q.url)

    real = FakePage(url="https://yandex.kz/showcaptcha?retpath=x")
    check("страница showcaptcha — капча", y.detect_captcha(real) is True)

    class Boom(FakePage):
        def locator(self, sel):
            raise RuntimeError("страница закрыта")

    check("исчезнувшая страница не роняет проверку капчи",
          y.detect_captcha(Boom()) is False)

    check("голый #js-button убран из признаков капчи",
          "#js-button" not in y._CAPTCHA_SELECTORS)

    # ---------- статика не API ----------
    css = "https://maps.yastatic.net/s3/front/maps/chunks/search/a1b2.css"
    check("кусок бандла по пути chunks/search не принимается за API",
          y._is_static_asset(css) is True, css)
    api = "https://yandex.kz/maps/api/search?text=кафе&lang=ru"
    check("настоящий эндпоинт поиска статикой не считается",
          y._is_static_asset(api) is False)

    src = Path(y.__file__).read_text(encoding="utf-8")
    body = src.split("def run_api_intercept(", 1)[1].split("\ndef ", 1)[0]
    check("перехватчик в run_api_intercept тоже отсекает статику",
          "_is_static_asset" in body)
    check("перехватчик ловит BaseException (CancelledError не Exception)",
          "except BaseException" in body)
    check("перехватчик не держит свой отдельный список шаблонов",
          "_API_URL_PATTERNS" in body and "api_patterns" not in body)
    check("холостой перехват прекращается, а не крутит 12 прокруток",
          "matched == 0" in body)

    # ---------- «честный ноль» ----------
    check("ноль по точному totalResultCount — это правда ноль",
          y._explicit_zero({"totalResultCount": 0}) is True)
    check("ноль в data.totalResultCount тоже",
          y._explicit_zero({"data": {"totalResultCount": "0"}}) is True)
    check("общий count:0 в чужом конверте нолём не считается",
          y._explicit_zero({"count": 0, "banners": []}) is False)
    check("total:0 в чужом конверте нолём не считается",
          y._explicit_zero({"total": 0}) is False)
    check("незнакомый конверт — не ноль", y._explicit_zero({"foo": "bar"}) is False)

    # ---------- карточки в глубоком конверте ----------
    card = {"title": "АЗС Гелиос", "coordinates": [76.9, 43.2],
            "fullAddress": "Алматы, ул. Абая 1"}
    deep = card
    for i in range(12):                      # 12 уровней обёрток
        deep = {"lvl%d" % i: deep}
    found = y._deep_find_business_list({"stack": [deep, {"items": [card, card]}]})
    check("карточки находятся и под десятком обёрток",
          bool(found), found)

    # ---------- фильтр «похоже на организацию» ----------
    check("карточка с point вместо coordinates проходит",
          y._is_business_item({"name": "Кафе", "point": {"lat": 43, "lon": 76}}))
    check("карточка с адресом-объектом проходит",
          y._is_business_item({"name": "Кафе", "address": {"formatted": "Абая 1"}}))
    check("значение фильтра («АИ-92») организацией не считается",
          not y._is_business_item({"name": "АИ-92", "seoname": "ai92"}))

    # ---------- порядок селекторов ----------
    sel_src = src.split("def probe_and_detect(", 1)[1].split("\n    def ", 1)[0]
    hc = sel_src.find("Шаг 1: пробуем hardcoded")
    cache = sel_src.find("Шаг 2: пробуем кеш")
    check("hardcoded пробуется РАНЬШЕ кеша — плохой автодетект не отравляет "
          "все будущие прогоны", 0 < hc < cache, (hc, cache))
    check("есть принудительный передетект (force) для смены вёрстки",
          "force: bool = False" in sel_src)

    # ---------- автодетект достижим при полном промахе ----------
    coll = src.split("def _collect_current_results(", 1)[1].split("\ndef ", 1)[0]
    force_at = coll.find("probe_and_detect(page, mode=\"list\", force=True)")
    give_up = coll.find('log.warning("Результаты не найдены для запроса')
    check("автодетект запускается ДО того, как сдаться",
          0 < force_at < give_up, (force_at, give_up))
    check("капча на ожидании выдачи учитывается в троттлинге",
          "get_throttle().on_captcha()" in coll)

    # ---------- тревога по покрытию ----------
    def org(**kw):
        o = y.Organization(name=kw.pop("name", "X"))
        for k, v in kw.items():
            setattr(o, k, v)
        return o

    thin = [org(name=f"Кафе {i}", address="Алматы") for i in range(25)]
    y._RUN_ERRORS.clear()
    y.print_stats(thin, label="тест")
    check("25 карточек без телефона и координат — это записанная авария",
          any("телефон" in e for e in y._RUN_ERRORS), y._RUN_ERRORS)

    rich = [org(name=f"Кафе {i}", phone="+7701", latitude="43.2") for i in range(25)]
    y._RUN_ERRORS.clear()
    y.print_stats(rich, label="тест")
    check("полные карточки тревогу не поднимают", not y._RUN_ERRORS, y._RUN_ERRORS)

    few = [org(name=f"Кафе {i}") for i in range(5)]
    y._RUN_ERRORS.clear()
    y.print_stats(few, label="тест")
    check("пяти пустых карточек мало для вывода — не пугаем зря",
          not y._RUN_ERRORS, y._RUN_ERRORS)

    # ---------- слепок незнакомого ответа ----------
    import tempfile, os
    start = Path.cwd()
    with tempfile.TemporaryDirectory() as td:
        os.chdir(td)
        try:
            y._unknown_shape_saved = False
            weird = {"env": {"ver": 3}, "blocks": [
                {"kind": "banner"},
                {"kind": "list", "rows": [{"title": "Кафе", "point": [1, 2]}] * 4},
            ]}
            y._dump_unknown_shape("https://yandex.kz/maps/api/search?x=1", weird)
            f = Path("reports/api_unknown_shape.json")
            check("слепок незнакомого ответа пишется без всяких флагов", f.exists())
            if f.exists():
                snap = json.loads(f.read_text(encoding="utf-8"))
                check("в слепке есть верхние ключи и образец списка",
                      "env" in snap["top_keys"] and snap["biggest_list_sample"],
                      snap.get("top_keys"))
                check("слепок компактный (не мегабайты)",
                      f.stat().st_size < 100_000, f.stat().st_size)
            y._dump_unknown_shape("https://yandex.kz/maps/api/search?x=2",
                                  {"other": 1})
            snap2 = json.loads(f.read_text(encoding="utf-8"))
            check("слепок пишется один раз за прогон, а не на каждый тайл",
                  "env" in snap2["top_keys"], snap2.get("top_keys"))
        finally:
            os.chdir(start)


    # ================= вторая пачка: тихая порча данных =================

    # --- инкрементальные сохранения при --fast-api ---
    import inspect
    cb = None
    for line in inspect.getsource(y._make_incremental_saver).splitlines():
        if line.strip().startswith("def on_org("):
            cb = line.strip()
    check("колбэк сохранения принимает (организация, номер)",
          cb and "index" in cb, cb)
    via = src.split("def _collect_via_api(", 1)[1].split("\ndef ", 1)[0]
    check("--fast-api зовёт колбэк С НОМЕРОМ — иначе сейвов нет вообще",
          "on_org(org, len(orgs))" in via)
    check("сорванное сохранение теперь видно в логе, а не глотается",
          "Промежуточное сохранение не сработало" in via)

    # --- шаблон чужого запроса ---
    tmpl = src.split("def _api_template_for(", 1)[1].split("\ndef ", 1)[0]
    check("совпадение запроса проверяется ТОЧНО, не вхождением подстроки",
          "got != want" in tmpl and "not in want" not in tmpl)

    # --- дедуп ---
    def o(**kw):
        x = y.Organization(name=kw.pop("name", "X"))
        for k, v in kw.items():
            setattr(x, k, v)
        return x

    check("одна точка с разной записью координат — один ключ",
          y._dedup_key(o(name="АЗС", latitude="43.238949", longitude="76.889709"))
          == y._dedup_key(o(name="АЗС", latitude="43.23894900000001",
                            longitude="76.889709")))
    check("ё и е — одна и та же сеть",
          y._dedup_key(o(name="Пятёрочка", address="ул. Абая, 1"))
          == y._dedup_key(o(name="Пятерочка", address="улица Абая, 1")))
    check("разные филиалы на улицах-тёзках сёл не схлопываются",
          y._dedup_key(o(name="Магнум", address="Достык, 5"))
          != y._dedup_key(o(name="Магнум", address="Кабанбай батыра, 5")))
    # Обрезка города нужна ради сопоставления: в сниппете адрес с городом,
    # в API — без. Сломав её, мы получили бы дубль на каждую организацию.
    for dom, api in (("Алматы, Достык, 5", "Достык, 5"),
                     ("Алматы, Абая 5", "Абая 5"),
                     ("Алматы, ул. Абая, 1", "ул. Абая, 1"),
                     ("Алматинская область, Талгар, ул. Ленина, 2",
                      "ул. Ленина, 2")):
        check(f"адрес из DOM и из API сходятся: {dom}",
              y._loose_addr(dom) == y._loose_addr(api),
              (y._loose_addr(dom), y._loose_addr(api)))
    check("соседние дома на улице-тёзке села различаются",
          y._loose_addr("Достык, 5") != y._loose_addr("Достык, 7"))
    check("город с явным типом всё так же срезается",
          y._loose_addr("г. Алматы, ул. Абая, 1") == y._loose_addr("ул. Абая, 1"))
    check("«Автостанция» не принимается за станцию-НП",
          y._loose_addr("Автостанция, ул. Абая, 1").startswith("автостанция"))
    check("«ТРЦ Город» не принимается за город",
          y._loose_addr("ТРЦ Город, ул. Сейфуллина, 500").startswith("трц город"))

    # --- число отзывов ---
    check("«1,2 тыс. отзывов» — это 1200, а не 12",
          y._parse_count("1,2 тыс. отзывов") == "1200")
    check("«3,5 тыс. оценок» — 3500", y._parse_count("3,5 тыс. оценок") == "3500")
    check("обычное число не портится", y._parse_count("2 345 отзывов") == "2345")

    # --- соцсети по хосту ---
    check("relax.com.kz не соцсеть", not y._is_social_link("https://relax.com.kz"))
    check("vostok.ru не соцсеть", not y._is_social_link("https://www.vostok.ru"))
    check("viberi.kz не соцсеть", not y._is_social_link("https://viberi.kz"))
    check("vk.com соцсеть", y._is_social_link("https://vk.com/club1"))
    check("t.me соцсеть", y._is_social_link("https://t.me/shop"))

    # --- координаты из URL ---
    check("sll= (центр карты) не выдаётся за координаты организации",
          y._extract_coords_from_url(
              "https://yandex.kz/maps/?sll=37.6173,55.7558&text=АЗС") is None)
    check("ll= читается даже когда раньше стоит sll=",
          y._extract_coords_from_url(
              "https://yandex.kz/maps/?sll=37.6,55.7&ll=76.9286,43.2380")
          == ("43.2380", "76.9286"))
    check("отрицательная долгота не теряется",
          y._extract_coords_from_url("https://yandex.kz/maps/?ll=-0.1276,51.5074")
          == ("51.5074", "-0.1276"))

    # --- прокси ---
    r = y.ProxyRotator()
    check("прокси без порта не превращается в «:None»",
          r.to_playwright_arg("http://u:p@proxy.example.com")["server"]
          == "http://proxy.example.com")
    check("%40 в пароле раскодируется",
          r.to_playwright_arg("http://us%40r:p%40ss@1.2.3.4:8080")["password"]
          == "p@ss")

    # --- падение браузера ---
    check("таймаут запуска браузера опознаётся как отказ браузера",
          y._is_browser_failure(RuntimeError(
              "Timeout 30000ms exceeded waiting for browser to start")))
    check("отсутствующий справочник НЕ выдаётся за смерть браузера",
          not y._is_browser_failure(
              FileNotFoundError("No such file or directory: 'kz_osm_places.json'")))
    check("занятый профиль Chrome — отказ браузера",
          y._is_browser_failure(RuntimeError("Profile is already in use")))

    # --- докачка не теряет ноль ---
    load = src.split("class ResumeManager", 1)[1]
    check("ноль при докачке остаётся нулём, а не «нет данных»",
          '"" if v is None else str(v)' in load)

    # --- счётчик «всего найдено» ---
    check("true в поле счётчика не превращается в «да»",
          y._extract_total_count({"found": True}) == "")
    check("настоящее число читается",
          y._extract_total_count({"totalResultCount": 812}) == "812")

    # --- hasMore ---
    check("hasMore=\"no\" означает «больше нет»",
          y._has_more({"hasMore": "no"}) is False)

    # --- статика: хост, а не подстрока ---
    check("имя CDN в параметрах не выбрасывает настоящий ответ API",
          not y._is_static_asset(
              "https://yandex.kz/maps/api/search?text=АЗС&p=avatars.mds.yandex.net"))

    # --- капча в пути ---
    check("карточка /maps/org/anticaptcha/ не считается капчей",
          y.detect_captcha(FakePage(url="https://yandex.kz/maps/org/anticaptcha/1/"))
          is False)

    # --- неизвестный домен ---
    y.set_domain("kz")
    y._RUN_ERRORS.clear()
    y.set_domain("ua")
    check("неизвестный домен не подменяется молча", y.DOMAIN == "yandex.kz")
    y.set_domain("kz")

    # --- неизвестные НП в списке ---
    y._RUN_ERRORS.clear()
    y.resolve_city_list("Алматы,Кокшетау,Нетакойгород,Ещёодносело")
    check("неизвестные НП в СПИСКЕ тоже попадают в отчёт, а не только "
          "одиночное имя", any("без координат" in e for e in y._RUN_ERRORS),
          y._RUN_ERRORS)

    # --- типы телефонов ---
    item = {"title": "АЗС", "phones": [
        {"formatted": "+7 727 111-11-11", "type": "phone"},
        {"formatted": "+7 727 222-22-22", "type": "phone"},
        {"formatted": "+7 727 333-33-33", "type": "fax"}]}
    got = y._org_from_item(item)
    check("типов телефонов ровно столько же, сколько номеров",
          len(got.phone.split(";")) == len(got.phone_types.split(";")),
          (got.phone, got.phone_types))

    print("\n" + ("✅ МОЛЧАЛИВЫЕ БАГИ ЗАКРЫТЫ" if not fails else f"❌ ПРОВАЛЫ: {fails}"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
