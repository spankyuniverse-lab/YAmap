# -*- coding: utf-8 -*-
"""Режим «один город + категории» и зум вьюпорта.

Главное, что проверяется: фраза заказчика уходит в Яндекс ДОСЛОВНО.
«Продукты» и «Спорт» — это ещё и имена групп внутреннего каталога, поэтому
без пометки они молча разворачиваются в восемь и девять ЧУЖИХ запросов.
Человек при этом уверен, что ищет ровно то, что напечатал.
"""
import io
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


# ---------- 1. дословные фразы ----------
LITERAL = ["АЗС", "Продукты", "Где поесть", "Кафе", "Спорт"]
for phrase in LITERAL:
    got = y.resolve_categories([y.literal_query(phrase)])
    check(f"«{phrase}» уходит дословно", got == [phrase], got)

# Старое поведение не тронуто: без пометки каталог по-прежнему разворачивает.
check("без пометки «Продукты» всё ещё разворачиваются в группу",
      len(y.resolve_categories(["Продукты"])) == 8)
check("без пометки «Спорт» всё ещё разворачивается в группу",
      len(y.resolve_categories(["Спорт"])) == 9)
check("«еда» — группа, разворачивается как раньше",
      len(y.resolve_categories(["еда"])) == 13)
check("двойная пометка не ломается",
      y.resolve_categories(["==Продукты"]) == ["=Продукты"],
      y.resolve_categories(["==Продукты"]))
check("пометка не мешает дедупу",
      y.resolve_categories(["=АЗС", "=АЗС", "АЗС"]) == ["АЗС"])

# ---------- 2. --search доезжает до категорий ----------
def parse(argv):
    saved = sys.argv
    sys.argv = ["yandex_parser.py", *argv]
    seen = {}
    real = y.run_category_parser
    y.run_category_parser = lambda **kw: (seen.update(kw) or {})
    try:
        y.main()
    except SystemExit:
        pass
    finally:
        y.run_category_parser = real
        sys.argv = saved
    return seen


got = parse(["--city", "Алматы", "--search", "АЗС", "Продукты", "-o", "t.xlsx"])
check("--search доезжает до сборщика",
      y.resolve_categories(got.get("categories", [])) == ["АЗС", "Продукты"],
      got.get("categories"))
check("--city доезжает", got.get("city") == "Алматы", got.get("city"))

# ---------- 3. воркеры не разворачивают запросы второй раз ----------
# Родитель режет УЖЕ РАЗВЁРНУТЫЕ запросы по воркерам. Если отдать их голыми,
# воркер прогонит их через каталог ещё раз — и «Продукты» превратятся в восемь.
queries = y.resolve_categories([y.literal_query("Продукты")])
naked = y.resolve_categories(queries)
marked = y.resolve_categories([y.literal_query(q) for q in queries])
check("голая передача воркеру развернула бы запрос (так было)", len(naked) == 8, naked)
check("помеченная — не разворачивает", marked == ["Продукты"], marked)

# ---------- 4. порядок листов для словаря заказчика ----------
order = sorted(LITERAL, key=y._category_order)
check("лист 2 — АЗС", order[0] == "АЗС", order)
check("лист 3 — Продукты", order[1] == "Продукты", order)
gt = sorted([lbl for _s, lbl, _q in y.GT_SEGMENT], key=y._category_order)
check("для GT-словаря порядок прежний",
      gt[:2] == ["Заправки", "Продуктовые магазины"], gt)

# ---------- 5. зум ----------
check("зум населённого пункта — 11", y.DEFAULT_CITY_Z == 11, y.DEFAULT_CITY_Z)
y.set_viewport(city="Алматы")
check("вьюпорт города встаёт на 11", y.MAP_Z == 11, y.MAP_Z)
check("и координаты города из справочника",
      y.MAP_LL == y.PLACES_ALL["Алматы"], y.MAP_LL)
y.set_viewport(city="Алматы", z=13)
check("явный --z сильнее умолчания", y.MAP_Z == 13, y.MAP_Z)
y.set_viewport(city="Агадырь")
check("аул тоже на 11", y.MAP_Z == 11, y.MAP_Z)

# Национальный свип считает зум сам, под размер тайла — его не трогали.
check("зум национального свипа считается отдельно и не сдвинут",
      y._zoom_for_span(0.25) == 11, y._zoom_for_span(0.25))
check("на мелком тайле свип по-прежнему уходит глубже",
      y._zoom_for_span(0.03125, 43.24) == 14, y._zoom_for_span(0.03125, 43.24))

# Расширение окна не должно начать отсекать законные точки: порог «уехало
# далеко» — 1.2°, половина окна на z=11 — 0.37°.
half_window = (1500 / 2 ** 11) / 2
check("половина окна втрое меньше порога отсечки",
      half_window * 3 < y.MAX_VIEWPORT_DRIFT_DEG,
      f"половина окна {half_window:.3f}°, порог {y.MAX_VIEWPORT_DRIFT_DEG}°")

# ---------- 6. меню ----------
def menu(keys):
    got = {}
    real_main = y.main
    y.main = lambda: got.update(argv=list(sys.argv[1:]))
    saved_in, saved_out, saved_argv = sys.stdin, sys.stdout, sys.argv
    sys.argv = ["yandex_parser.py"]
    sys.stdin = io.StringIO(keys)
    sys.stdout = io.StringIO()
    text = ""
    try:
        y.interactive_menu()
    except Exception as exc:
        got["err"] = f"{type(exc).__name__}: {exc}"
    finally:
        text = sys.stdout.getvalue()
        y.main = real_main
        sys.stdin, sys.stdout, sys.argv = saved_in, saved_out, saved_argv
    return got.get("argv"), text, got.get("err")


argv, _text, err = menu("\n2\nАлматы\n1 2\n\n\n\n\n\n")
check("меню: город + две категории → команда", argv == [
    "--city", "Алматы", "--search", "АЗС", "Продукты",
    "-o", "Алматы.xlsx", "--workers", "1", "--api-intercept"], argv or err)

argv, _text, err = menu("\n2\nШымкент\n1-3\n\n\n\n\n\n")
check("меню: диапазон «1-3»",
      argv and argv[3:6] == ["АЗС", "Продукты", "Где поесть"], argv or err)

argv, _text, err = menu("\n2\nАстана\n\nШиномонтаж, Автомойка\n2\nсвой.xlsx\n\n\n\n")
check("меню: Enter = все пять, плюс свои фразы",
      argv and argv[3:10] == ["АЗС", "Продукты", "Где поесть", "Кафе", "Спорт",
                              "Шиномонтаж", "Автомойка"], argv or err)
check("меню: своё имя файла и число браузеров",
      argv and argv[-4:] == ["свой.xlsx", "--workers", "2", "--api-intercept"],
      argv or err)

argv, text, err = menu("\n2\nАлмата\nn\nАлматы\n1\n\n\n\n\n")
check("меню: опечатка в городе не проходит молча",
      "в справочнике нет" in text, err)
check("меню: и предлагает похожее", "Алматы" in text.split("Возможно:")[-1][:80]
      if "Возможно:" in text else False, text[-200:])
check("меню: после исправления команда верная",
      argv and argv[:2] == ["--city", "Алматы"], argv or err)

argv, text, err = menu("\n2\nАлматы\n9\n1\n\n\n\n\n\n")
check("меню: номер вне списка переспрашивает, а не молчит",
      "Не понял: 9" in text, text[-200:])

# Пункт GT остался ПЕРВЫМ: на этом завязан t_menu.
argv, text, err = menu("\n1\n\n\n\n\n\n")
check("GT остался первым пунктом меню",
      argv and argv[0] == "--all-cities", argv or err)

print()
if fails:
    print(f"❌ ПРОВАЛЫ ({len(fails)}): " + "; ".join(fails))
    sys.exit(1)
print("✅ ОДИН ГОРОД + КАТЕГОРИИ ОК")
