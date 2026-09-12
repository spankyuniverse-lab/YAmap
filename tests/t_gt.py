"""GT-таксономия: 5 категорий, 19 городов, лист на категорию (а не на пару
«категория+город»), слаг и город в колонках — чтобы матчилось с таблицей 2ГИС."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import yandex_parser as y

fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL") + " | " + msg)
    if not cond:
        fails.append(msg)

qs = y.resolve_categories(["gt"])
labels = []
for q in qs:
    lab = y.category_of(q)[0]
    if lab not in labels:
        labels.append(lab)
check(labels == ["Заправки", "Поесть", "Продуктовые магазины",
                 "Гипермаркеты"], f"GT = 4 категории: {labels}")
check(y.category_of("Супермаркет")[0] == "Продуктовые магазины",
      "«Супермаркет» ложится в «Продуктовые магазины», отдельной категории нет")
check(len(qs) > 5, f"под категориями несколько запросов Яндексу: {len(qs)}")
check(y.category_of("Ресторан")[0] == "Поесть", "«Ресторан» ложится в «Поесть»")
check(y.category_of("Кафе")[1] == "gt_poest", "слаг у всех запросов категории один")
# Спеки --cities считаются по активным странам; по умолчанию их три, поэтому
# «19 городов» верно только для Казахстана. Проверяем именно это, а не число.
y.set_countries(["kz"])
check(len(y.resolve_city_list(None)) == len(y.KZ_CITIES_MAJOR),
      f"по Казахстану умолчание = {len(y.KZ_CITIES_MAJOR)} крупнейших городов")
y.set_countries(None)
check(len(y.resolve_city_list(None)) > len(y.KZ_CITIES_MAJOR),
      "по трём странам крупнейших городов больше, чем по одному Казахстану")
check(y.resolve_categories(["gt-fuel"]) == ["Заправки"],
      "gt-fuel = один запрос (без дублей АГЗС/АГНКС)")

# Слаг есть у каждого запроса
slugs = {y.category_of(q)[1] for q in qs}
check(all(slugs) and slugs == {"gt_zapravki", "gt_poest", "gt_produktovye",
                               "gt_gipermarkety"},
      f"слаги проставлены: {sorted(slugs)}")

# Книга: лист на категорию, город — колонкой
orgs, res = [], {}
for city in ["Алматы", "Астана", "Шымкент"]:
    for q in qs:
        lab, slug = y.category_of(q)
        o = y.Organization(name=f"{q}-{city}", address=f"{city}, ул. 1",
                           search_query=lab, gt_slug=slug, source_query=q, city=city)
        res.setdefault(lab, []).append(o)
        orgs.append(o)

out = Path(__file__).resolve().parent / "out" / "gt.xlsx"
out.parent.mkdir(parents=True, exist_ok=True)
y.save_xlsx_by_categories(res, out)

from openpyxl import load_workbook
wb = load_workbook(out)
sheets = [s for s in wb.sheetnames if s != "Все результаты"]
check(sorted(sheets) == sorted(labels),
      f"лист на КАЖДУЮ категорию (не на запрос): {sheets}")

ws = wb["Все результаты"]
head = [c.value for c in ws[1]]
check("Код категории" in head and "Город (прогон)" in head,
      "в шапке есть «Код категории» и «Город (прогон)»")
i_slug, i_city = head.index("Код категории"), head.index("Город (прогон)")
row = [c.value for c in ws[2]]
check(row[i_slug].startswith("gt_") and row[i_city] in ("Алматы", "Астана", "Шымкент"),
      f"в строке заполнены слаг={row[i_slug]!r} и город={row[i_city]!r}")
check(ws.max_row - 1 == 3 * len(qs),
      f"строк 3 города × {len(qs)} запросов (вышло {ws.max_row - 1})")
check("Запрос" in head, "в шапке есть колонка «Запрос» (точный запрос Яндексу)")

print("\n" + ("✅ GT ОК" if not fails else f"❌ ПРОВАЛЫ: {fails}"))
sys.exit(1 if fails else 0)
