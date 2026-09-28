# -*- coding: utf-8 -*-
"""Сколько раз за прогон переписывается ОБЩАЯ книга.

Полная перезапись книги — самая дорогая операция прогона: на большом сборе
это гигабайты памяти и минуты времени. Раньше на каждом доведённом до конца
прогоне она случалась лишним разом: цикл сохранял на последнем НП, а сразу
после цикла «досохранение» писало ровно ту же книгу второй раз.

Проверяем: лишней записи нет, но и данные не теряются, если прогон оборвали
между пачками.
"""
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import yandex_parser as y

fails = []
REAL_SAVE = y.save_xlsx_by_categories


def check(name, cond, detail=""):
    print(("OK   | " if cond else "FAIL | ") + name
          + (f"\n       {detail}" if not cond and detail else ""))
    if not cond:
        fails.append(name)


def run(cities, out, boom_at=None):
    """Прогон по списку НП. boom_at — на каком по счёту НП оборвать (Ctrl+C)."""
    writes = []

    def counting_save(results, path, *a, **kw):
        writes.append(str(path))
        return REAL_SAVE(results, path, *a, **kw)

    seq = {"n": 0}

    def fake(city, categories, output, **kw):
        seq["n"] += 1
        if boom_at and seq["n"] == boom_at:
            raise KeyboardInterrupt
        org = y.Organization(name=f"АЗС-{city}", address=f"{city}, ул. 1",
                             org_id=f"{seq['n']}", search_query="Заправки")
        res = {"Заправки": [org]}
        Path(output).parent.mkdir(parents=True, exist_ok=True)
        REAL_SAVE(res, Path(output))
        return res

    y.save_xlsx_by_categories = counting_save
    y.run_category_parser = fake
    try:
        y.run_cities_parser(cities=cities, categories=["gt-азс"], output=str(out))
    finally:
        y.save_xlsx_by_categories = REAL_SAVE
    return [w for w in writes if w == str(out)]


BASE = Path("savecount")
shutil.rmtree(BASE, ignore_errors=True)
BASE.mkdir()

# --- короткий список: save_every = 1, пишем на каждом НП -------------------
short = [f"НП-{i}" for i in range(1, 4)]
w = run(short, BASE / "a" / "kz.xlsx")
check("3 НП → 3 записи книги, не 4", len(w) == 3, f"записей: {len(w)}")

# --- длинный список: save_every = 25 --------------------------------------
long_ = [f"НП-{i}" for i in range(1, 71)]
w = run(long_, BASE / "b" / "kz.xlsx")
check("70 НП при пачке 25 → 3 записи (25, 50, 70), не 4",
      len(w) == 3, f"записей: {len(w)}")

# --- обрыв между пачками: досохранение обязано сработать ------------------
w = run(long_, BASE / "c" / "kz.xlsx", boom_at=30)
check("обрыв на 30-м НП → 2 записи: пачка на 25 и досохранение",
      len(w) == 2, f"записей: {len(w)}")

book = BASE / "c" / "kz.xlsx"
check("после обрыва книга на диске есть", book.exists())
if book.exists():
    got = len(y._load_existing_orgs(book))
    # 29 успешных НП: 30-й бросил Ctrl+C, не дойдя до сбора.
    check("и в ней всё собранное до обрыва, а не только первая пачка",
          got == 29, f"в книге {got} организаций, ожидалось 29")

# --- нечего сохранять — не пишем вовсе ------------------------------------
w = run([], BASE / "d" / "kz.xlsx")
check("пустой список НП → книгу не трогаем", len(w) == 0, f"записей: {len(w)}")

shutil.rmtree(BASE, ignore_errors=True)

print()
if fails:
    print(f"❌ ПРОВАЛЫ ({len(fails)}): " + "; ".join(fails))
    sys.exit(1)
print("✅ ЛИШНЕЙ ПЕРЕЗАПИСИ КНИГИ НЕТ")
