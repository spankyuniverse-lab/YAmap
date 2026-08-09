import sys, json
from pathlib import Path
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import yandex_parser as y

calls = []
def fake(city, categories, output, **kw):
    calls.append(city)
    orgs = []
    for q in y.resolve_categories(categories):
        o = y.Organization(name=f"{q}-{city}", address=f"{city}, ул. Тест 1", search_query=q)
        orgs.append(o)
    # общий для всех городов дубль
    dup = y.Organization(name="Сетевой дубль", address="ул. Общая 1", search_query="АЗС")
    orgs.append(dup)
    res = {}
    for o in orgs:
        res.setdefault(o.search_query, []).append(o)
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    y.save_xlsx_by_categories(res, Path(output))
    return res

y.run_category_parser = fake
out = Path("out/kz_gt.xlsx")
res = y.run_cities_parser(cities=["Алматы","Астана","Шымкент"], categories=["gt-азс"], output=str(out))
total = sum(len(v) for v in res.values())
print("cities:", calls, "total:", total, "queries:", list(res))
print("exists:", out.exists(), "progress:", json.loads((out.parent/"kz_gt.cities.json").read_text(encoding="utf-8")))

# resume: второй запуск не должен идти по пройденным городам
calls.clear()
res2 = y.run_cities_parser(cities=["Алматы","Астана","Шымкент","Караганда"], categories=["gt-азс"], output=str(out))
print("resume calls:", calls, "total:", sum(len(v) for v in res2.values()))
