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
check(qs == ["Заправки", "Где поесть", "Продуктовые магазины",
             "Супермаркеты", "Гипермаркеты"], f"GT = ровно 5 категорий: {qs}")
check(len(y.resolve_city_list(None)) == 19, "по умолчанию 19 городов")
check(y.resolve_categories(["gt-fuel"]) == ["Заправки"],
      "gt-fuel = один запрос (без дублей АГЗС/АГНКС)")

# Слаги на всех пяти
slugs = [y.GT_SLUG_BY_QUERY.get(q) for q in qs]
check(all(slugs) and slugs == ["gt_zapravki", "gt_poest", "gt_produktovye",
                               "gt_supermarkety", "gt_gipermarkety"],
      f"слаги проставлены: {slugs}")

# Книга: лист на категорию, город — колонкой
orgs, res = [], {}
for city in ["Алматы", "Астана", "Шымкент"]:
    for q in qs:
        o = y.Organization(name=f"{q}-{city}", address=f"{city}, ул. 1",
                           search_query=q, gt_slug=y.GT_SLUG_BY_QUERY[q], city=city)
        res.setdefault(q, []).append(o)
        orgs.append(o)

out = Path(__file__).resolve().parent / "out" / "gt.xlsx"
out.parent.mkdir(parents=True, exist_ok=True)
y.save_xlsx_by_categories(res, out)

from openpyxl import load_workbook
wb = load_workbook(out)
sheets = [s for s in wb.sheetnames if s != "Все результаты"]
check(sorted(sheets) == sorted(qs),
      f"листов ровно 5, по категориям: {sheets}")

ws = wb["Все результаты"]
head = [c.value for c in ws[1]]
check("Код категории" in head and "Город (прогон)" in head,
      "в шапке есть «Код категории» и «Город (прогон)»")
i_slug, i_city = head.index("Код категории"), head.index("Город (прогон)")
row = [c.value for c in ws[2]]
check(row[i_slug].startswith("gt_") and row[i_city] in ("Алматы", "Астана", "Шымкент"),
      f"в строке заполнены слаг={row[i_slug]!r} и город={row[i_city]!r}")
check(ws.max_row - 1 == 15, f"строк 3 города × 5 категорий = 15 (вышло {ws.max_row - 1})")

print("\n" + ("✅ GT ОК" if not fails else f"❌ ПРОВАЛЫ: {fails}"))
sys.exit(1 if fails else 0)
