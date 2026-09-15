"""Регресс на реальный баг: `-o 1` (имя без расширения).
Воркеры собирали данные, а слияние выдавало 0 — части назывались `1.w1`,
не читались как книга, и .cities.json считался как `1.cities.json`."""
import sys, json, argparse
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import yandex_parser as y

HERE = Path(__file__).resolve().parent
fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL") + " | " + msg)
    if not cond:
        fails.append(msg)

# 1. Имя нормализуется на входе main()
check(y._ensure_ext("1") == "1.xlsx", "-o 1 → 1.xlsx")

# 2. Даже если расширение всё-таки потерялось, части его получают
check(y._part_path(Path("1"), 1).name == "1.w1.xlsx",
      f"часть без расширения → {y._part_path(Path('1'), 1).name}")
check(y._part_path(Path("kz.xlsx"), 2).name == "kz.w2.xlsx", "обычное имя не трогаем")
check(y._part_path(Path("kz.csv"), 1).name == "kz.w1.csv", "csv сохраняет свой формат")

# 3. .cities.json части считается от ПОЛНОГО имени части
part = y._part_path(Path("1"), 1)
check(part.with_name(part.stem + ".cities.json").name == "1.w1.cities.json",
      f"прогресс части → {part.with_name(part.stem + '.cities.json').name}")

# 4. Сквозной прогон: воркеры «собрали», слияние обязано увидеть данные
out = HERE / "out" / "1"
outx = Path(y._ensure_ext(str(out)))
outx.parent.mkdir(parents=True, exist_ok=True)
total = 0
for i, cities in enumerate([["Алматы", "Шымкент"], ["Астана"]], 1):
    part = y._part_path(outx, i)
    orgs = [y.Organization(name=f"АЗС-{c}-{n}", address=f"{c}, ул. {n}",
                           search_query="Заправки", city=c)
            for c in cities for n in range(1, 4)]
    total += len(orgs)
    y.save_xlsx_by_categories({"Заправки": orgs}, part)
    part.with_name(part.stem + ".cities.json").write_text(
        json.dumps(cities, ensure_ascii=False), encoding="utf-8")

merged = y._merge_parts([y._part_path(outx, 1), y._part_path(outx, 2)], outx)
check(len(merged) == total, f"слияние вернуло {len(merged)} из {total} (был 0)")
check(outx.exists(), f"итоговый файл создан: {outx.name}")
check(y._done_cities(outx) == {"Алматы", "Шымкент", "Астана"},
      f"пройденные города видны: {sorted(y._done_cities(outx))}")

print("\n" + ("✅ ИМЕНА ОК" if not fails else f"❌ ПРОВАЛЫ: {fails}"))
sys.exit(1 if fails else 0)
