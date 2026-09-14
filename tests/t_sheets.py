# -*- coding: utf-8 -*-
"""Структура итоговой книги Excel.

  Лист 1  «Все результаты»   — всё
  Лист 2  «Заправки»         — рубрика
  Лист 3  «Продуктовые…»     — рубрика
  Лист 4  «Крупные города»   — 15 названных городов
  Лист 5  «Аулы и сёла»      — всё остальное

Главное, что проверяется: ни одна строка не теряется и не удваивается.
Листы городов и аулов — это ДРУГОЙ ВЗГЛЯД на те же данные, а не копия,
которую докачка потом посчитает второй раз.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import yandex_parser as y

fails = []
БОЛЬШИЕ = "Крупные города"
СЁЛА = "Аулы и сёла"


def check(name, cond, detail=""):
    print(("OK   | " if cond else "FAIL | ") + name
          + (f"\n       {detail}" if not cond and detail else ""))
    if not cond:
        fails.append(name)


def org(name, city, cat, addr=None):
    o = y.Organization(name=name)
    o.city = city
    o.search_query = cat
    o.address = addr if addr is not None else (
        f"{city}, улица Тестовая, 1" if city else "улица Тестовая, 1")
    o.phone = "+7 701 000-00-00"
    return o


def book(path):
    from openpyxl import load_workbook
    wb = load_workbook(path)
    return wb, wb.sheetnames


def rows_of(wb, title):
    ws = wb[title]
    head = [c.value for c in ws[1]]
    return [dict(zip(head, r)) for r in ws.iter_rows(min_row=2, values_only=True)
            if any(v not in (None, "") for v in r)]


def main() -> int:
    results = {
        "Заправки": [
            org("АЗС Гелиос", "Алматы", "Заправки"),
            org("АЗС КМГ", "Астана", "Заправки"),
            org("АЗС Сокол", "Достык", "Заправки"),
            org("АЗС Трасса", "Жанибек", "Заправки"),
        ],
        "Продуктовые магазины": [
            org("Магнум", "Алматы", "Продуктовые магазины"),
            org("Сельпо", "Достык", "Продуктовые магазины"),
            org("Лавка", "Благовещенка [KG]", "Продуктовые магазины"),
        ],
    }
    total = sum(len(v) for v in results.values())

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "kz.xlsx"
        y.save_xlsx_by_categories(results, out)
        wb, names = book(out)

        # ---------- порядок листов ----------
        check("лист 1 — сводный «Все результаты»",
              names[0] == "Все результаты", names)
        check("лист 2 — Заправки", names[1] == "Заправки", names)
        check("лист 3 — продуктовые",
              names[2] == "Продуктовые магазины", names)
        check("лист 4 — крупные города", names[3] == БОЛЬШИЕ, names)
        check("лист 5 — аулы и сёла", names[4] == СЁЛА, names)
        check("лист 6 — сводка по НП", names[5] == "Сводка по НП", names)
        check("листов ровно шесть — по НП книга не разрастается",
              len(names) == 6, names)

        # ---------- содержимое ----------
        всё = rows_of(wb, "Все результаты")
        check("на сводном листе ВСЕ строки", len(всё) == total, len(всё))
        check("на листе рубрики только её строки",
              len(rows_of(wb, "Заправки")) == 4)

        города = rows_of(wb, БОЛЬШИЕ)
        сёла = rows_of(wb, СЁЛА)
        check("Алматы и Астана — в крупных городах",
              {r["Город (прогон)"] for r in города} == {"Алматы", "Астана"},
              {r["Город (прогон)"] for r in города})
        check("Достык, Жанибек и Благовещенка — в сёлах",
              {r["Город (прогон)"] for r in сёла}
              == {"Достык", "Жанибек", "Благовещенка [KG]"},
              {r["Город (прогон)"] for r in сёла})
        check("города + сёла = всё, ни одна строка не потерялась",
              len(города) + len(сёла) == total,
              (len(города), len(сёла), total))
        check("на листе городов строки ВСЕХ рубрик, а не одной",
              len({r["Поисковый запрос"] for r in города}) == 2,
              [(r["Название"], r["Поисковый запрос"]) for r in города])

        # ---------- сводка ----------
        ws = wb["Сводка по НП"]
        свод = [list(r) for r in ws.iter_rows(values_only=True)]
        шапка = свод[0]
        check("в сводке колонка на каждую рубрику",
              шапка[2:4] == ["Заправки", "Продуктовые магазины"], шапка)
        check("первой строкой ИТОГО — общая картина сразу, без прокрутки",
              свод[1][0] == "ИТОГО", свод[1])
        check("ИТОГО сходится со сводным листом",
              свод[1][-1] == total, (свод[1][-1], total))
        check("в сводке строка на каждый НП",
              len(свод) - 2 == len({r["Город (прогон)"] for r in всё}),
              len(свод) - 2)
        check("сводка отсортирована по убыванию собранного",
              [r[-1] for r in свод[2:]] == sorted((r[-1] for r in свод[2:]),
                                                  reverse=True),
              [r[-1] for r in свод[2:]])
        check("в сводке видно, где крупный город, а где аул",
              {r[1] for r in свод[2:]} == {"Крупный город", "Аул/село"},
              {r[1] for r in свод[2:]})
        сумма = sum(r[-1] for r in свод[2:])
        check("сумма по НП равна ИТОГО — ничего не потеряно и не удвоено",
              сумма == total, (сумма, total))

        # ---------- докачка не должна удвоить ----------
        rm = y.ResumeManager(out)
        check("докачка читает ТОЛЬКО сводный лист — удвоения нет",
              rm.existing_count == total, rm.existing_count)

    # ---------- один НП: делить не на что ----------
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "one.xlsx"
        y.save_xlsx_by_categories(
            {"Заправки": [org("АЗС", "Алматы", "Заправки")]}, out)
        wb, names = book(out)
        check("по одному НП ни листов городов/сёл, ни сводки — это была бы копия",
              names == ["Все результаты", "Заправки"], names)

    # ---------- только сёла: пустой лист городов не нужен ----------
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "rural.xlsx"
        y.save_xlsx_by_categories({"Заправки": [
            org("АЗС 1", "Достык", "Заправки"),
            org("АЗС 2", "Жанибек", "Заправки")]}, out)
        wb, names = book(out)
        check("пустой лист «Крупные города» не создаётся",
              БОЛЬШИЕ not in names and СЁЛА in names, names)

    # ---------- город опознаётся по адресу, когда поля «город» нет ----------
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "sweep.xlsx"
        y.save_xlsx_by_categories({"Заправки": [
            org("АЗС в городе", "", "Заправки", "Алматы, проспект Абая, 10"),
            org("АЗС у трассы", "", "Заправки", "Алматинская область, трасса А-2"),
        ]}, out)
        wb, names = book(out)
        check("в свипе по стране поля «город» нет — берём его из адреса",
              БОЛЬШИЕ in names and len(rows_of(wb, БОЛЬШИЕ)) == 1,
              names)
        check("придорожная точка ушла в сёла, а не в города",
              len(rows_of(wb, СЁЛА)) == 1)

    # ---------- имена листов ----------
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "names.xlsx"
        y.save_xlsx_by_categories({
            "Очень длинное название рубрики которое не влезет": [
                org("A", "Достык", "x")],
            "Кафе": [org("B", "Достык", "x")],
            "кафе": [org("C", "Достык", "x")],
        }, out)
        wb, names = book(out)
        check("имена листов укладываются в 31 символ",
              all(len(n) <= 31 for n in names),
              [n for n in names if len(n) > 31])
        low = [n.casefold() for n in names]
        check("нет листов, различающихся только регистром",
              len(low) == len(set(low)), names)
        check("рубрики не схлопнулись в один лист",
              len(names) >= 4, names)

    # ---------- тёзки: село Актау — это не город Актау ----------
    y.set_countries(["kz"])
    def big(city):
        return y._major_city_of(org("x", city, "Заправки"))

    check("город Актау — крупный", big("Актау") == "Актау")
    check("село «Актау (Карагандинская)» НЕ крупный город — это тёзка "
          "за полторы тысячи километров", not big("Актау (Карагандинская)"))
    check("село «Актау (Улытау)» — тоже тёзка", not big("Актау (Улытау)"))
    check("«Кызылжар» в справочнике — село, а не Петропавловск",
          not big("Кызылжар"), big("Кызылжар"))
    check("Петропавловск под своим именем — крупный",
          big("Петропавловск") == "Петропавловск")

    # ---------- псевдонимы ----------
    for alias, canon in (("Оскемен", "Усть-Каменогорск"), ("Нур-Султан", "Астана"),
                         ("Орал", "Уральск"), ("Гурьев", "Атырау"),
                         ("Чимкент", "Шымкент"), ("Алма-Ата", "Алматы")):
        check(f"«{alias}» опознаётся как {canon}", big(alias) == canon, big(alias))

    # ---------- границы слова в адресе ----------
    def by_addr(addr):
        o = org("x", "", "Заправки", addr)
        return y._major_city_of(o)
    check("«Актауский район» не превращает село в город Актау",
          not by_addr("Актауский район, село Кызылсай"), by_addr("Актауский район, село Кызылсай"))
    check("«Семейкино» — не Семей", not by_addr("Семейкино, улица Мира 1"))
    check("«Алматинская область, Талгар» — не Алматы",
          not by_addr("Алматинская область, Талгар, ул. Ленина 2"))
    check("адрес с настоящим городом опознаётся",
          by_addr("Казахстан, Алматы, проспект Абая 10") == "Алматы")

    # ---------- ровно список заказчика ----------
    mx = y.resolve_city_list("макс")
    попало = {big(n) for n in mx if big(n)}
    check("в крупные города попадают ровно те 15, что назвал заказчик",
          попало == set(y.MAJOR_CITY_SHEET),
          sorted(попало ^ set(y.MAJOR_CITY_SHEET)))
    y.set_countries(None)

    print("\n" + ("✅ СТРУКТУРА КНИГИ ОК" if not fails else f"❌ ПРОВАЛЫ: {fails}"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
