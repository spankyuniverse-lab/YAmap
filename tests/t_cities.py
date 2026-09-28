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


# --- Регресс: после параллельного прогона запуск в ОДИН браузер не должен
# затирать слитый файл (штатный совет при капче — «уменьши --workers»).
import json as _json
out2 = Path("out/kz_sw.xlsx")
out2.parent.mkdir(parents=True, exist_ok=True)
par_orgs = [y.Organization(name=f"АЗС-{c}", address=f"{c}, ул. 1", org_id=str(i),
                           search_query="Заправки", city=c)
            for i, c in enumerate(["Алматы", "Астана"], 1)]
y.save_xlsx_by_categories({"Заправки": par_orgs}, out2)
# прогресс лежит в ЧАСТЯХ воркеров, своего kz_sw.cities.json нет
(out2.parent / "kz_sw.w1.cities.json").write_text(
    _json.dumps(["Алматы"], ensure_ascii=False), encoding="utf-8")
(out2.parent / "kz_sw.w2.cities.json").write_text(
    _json.dumps(["Астана"], ensure_ascii=False), encoding="utf-8")

res_sw = y.run_cities_parser(cities=["Алматы", "Астана", "Шымкент"],
                             categories=["gt-азс"], output=str(out2))
total_sw = sum(len(v) for v in res_sw.values())
names_sw = {o.name for v in res_sw.values() for o in v}
assert {"АЗС-Алматы", "АЗС-Астана"} <= names_sw,     f"параллельные данные затёрты: {sorted(names_sw)}"
assert "Шымкент" in {o.city for v in res_sw.values() for o in v if o.city} or total_sw > 2,     "новый город не добрался"
print(f"✅ смена --workers 2 → 1 не теряет данные ({total_sw} записей, "
      f"старые города на месте)")
