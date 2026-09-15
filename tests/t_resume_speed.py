# -*- coding: utf-8 -*-
"""Старт докачки: быстро, но ничего не теряя.

Два разных механизма, оба про «не читать лишнего»:

1. Счётчик «собрано» в параллельном режиме больше не разбирает чужую книгу
   целиком — считает строки в XML. Если он ошибётся, родитель покажет не то
   число; если он БРОСИТ исключение, finally цикла присмотра погасит ВСЕХ
   воркеров. Поэтому проверяем и цифры, и что он не падает ни на чём.

2. Погородные части, уже слитые в общую книгу, повторно не читаются. Тут
   ошибиться можно ровно в одну сторону — потерять собранное, поэтому
   отдельно проверяем оборванный прогон: часть новее книги обязана подняться.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import yandex_parser as y

fails = []


def check(name, cond, detail=""):
    print(("OK   | " if cond else "FAIL | ") + name
          + (f"\n       {detail}" if not cond and detail else ""))
    if not cond:
        fails.append(name)


def org(i, city="Алматы", q="Заправки"):
    return y.Organization(name=f"АЗС {city} {i}", address=f"{city}, ул. 1",
                          city=city, search_query=q, org_id=f"{city}-{i}")


with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)

    # ---------- 1. быстрый счётчик строк ----------
    multi = tmp / "multi.xlsx"
    res = {}
    for i in range(400):
        o = org(i, "Алматы" if i % 2 else "Агадырь",
                "Заправки" if i % 3 else "Продуктовые магазины")
        res.setdefault(o.search_query, []).append(o)
    y.save_xlsx_by_categories(res, multi)
    check("многолистовая книга: счёт совпадает с честным разбором",
          y._count_collected(multi) == len(y._load_existing_orgs(multi)),
          f"{y._count_collected(multi)} против {len(y._load_existing_orgs(multi))}")

    single = tmp / "single.xlsx"
    y.save_xlsx([org(i) for i in range(150)], single)
    check("однолистовая книга: счёт совпадает",
          y._count_collected(single) == 150, y._count_collected(single))

    empty = tmp / "empty.xlsx"
    y.save_xlsx_by_categories({"Заправки": []}, empty)
    check("пустая книга — ноль, а не ошибка", y._count_collected(empty) == 0,
          y._count_collected(empty))

    csvf = tmp / "data.csv"
    y.save_csv([org(i) for i in range(50)], csvf)
    check("CSV считается честным разбором, а не переводами строк",
          y._count_collected(csvf) == 50, y._count_collected(csvf))

    # Файл с многострочным полем: счёт по \n завысил бы почти вдвое.
    csv2 = tmp / "multiline.csv"
    y.save_csv([y.Organization(name=f"АЗС {i}", search_query="Заправки",
                               working_hours="пн-пт 9-18\nсб-вс выходной",
                               description="строка один\nстрока два")
                for i in range(30)], csv2)
    check("многострочные поля в CSV не раздувают счёт",
          y._count_collected(csv2) == 30, y._count_collected(csv2))

    # Ничто из этого не должно бросать: исключение отсюда гасит всех воркеров.
    broken = tmp / "broken.xlsx"
    broken.write_bytes("это не zip".encode("utf-8"))
    for name, path in (("битый файл", broken),
                       ("несуществующий файл", tmp / "нет.xlsx"),
                       ("пустой файл", tmp / "zero.xlsx")):
        if name == "пустой файл":
            path.write_bytes(b"")
        try:
            got = y._count_collected(path)
            ok = got in (0, None)
        except Exception as exc:
            got, ok = f"ИСКЛЮЧЕНИЕ {exc}", False
        check(f"{name} не роняет счётчик", ok, got)

    # Счёт должен быть заметно быстрее разбора — иначе смысла в нём нет.
    t0 = time.perf_counter(); y._load_existing_orgs(multi, quiet=True)
    t_honest = time.perf_counter() - t0
    t0 = time.perf_counter(); y._count_collected(multi)
    t_fast = time.perf_counter() - t0
    check("счёт быстрее разбора минимум вдесятеро",
          t_fast * 10 < t_honest, f"разбор {t_honest:.3f} c, счёт {t_fast:.3f} c")

    # ---------- 2. части, уже слитые в книгу ----------
    def scenario(stale_parts: int, fresh_parts: int):
        """Готовим прогон: часть НП в книге, часть — только в частях."""
        d = Path(tempfile.mkdtemp())
        out = d / "kz.xlsx"
        parts = d / "kz_parts"
        parts.mkdir()
        cities = [f"НП-{i}" for i in range(stale_parts + fresh_parts)]
        in_book = []
        for c in cities[:stale_parts]:
            orgs = [org(i, c) for i in range(5)]
            in_book += orgs
            y.save_xlsx_by_categories({"Заправки": orgs}, parts / f"{c}.xlsx")
        y.save_xlsx_by_categories({"Заправки": in_book}, out)
        # Старые части — заведомо старше книги.
        old = out.stat().st_mtime - 3600
        for c in cities[:stale_parts]:
            os.utime(parts / f"{c}.xlsx", (old, old))
        # Свежие части — после книги: их ещё не успели слить (обрыв).
        for c in cities[stale_parts:]:
            orgs = [org(i, c) for i in range(5)]
            y.save_xlsx_by_categories({"Заправки": orgs}, parts / f"{c}.xlsx")
            new = out.stat().st_mtime + 10
            os.utime(parts / f"{c}.xlsx", (new, new))
        (d / "kz.cities.json").write_text(
            __import__("json").dumps(cities, ensure_ascii=False), encoding="utf-8")
        return d, out, cities

    reads = {"n": 0}
    real_load = y._load_existing_orgs

    def counting_load(path, quiet=False):
        reads["n"] += 1
        return real_load(path, quiet=quiet)

    # Обычный случай: все части старше книги — читать их незачем.
    d, out, cities = scenario(stale_parts=12, fresh_parts=0)
    y._load_existing_orgs = counting_load
    y.run_category_parser = lambda **kw: {}
    try:
        res = y.run_cities_parser(cities=cities, categories=["gt-азс"],
                                  output=str(out))
    finally:
        y._load_existing_orgs = real_load
    total = sum(len(v) for v in res.values())
    check("все НП подняты из книги", total == 60, total)
    check("старые части не перечитывались", reads["n"] == 1,
          f"чтений файлов: {reads['n']} (ждали 1 — только книга)")

    # Обрыв: 3 НП есть в частях, но ещё не в книге. Потерять их нельзя.
    reads["n"] = 0
    d, out, cities = scenario(stale_parts=10, fresh_parts=3)
    y._load_existing_orgs = counting_load
    try:
        res = y.run_cities_parser(cities=cities, categories=["gt-азс"],
                                  output=str(out))
    finally:
        y._load_existing_orgs = real_load
    total = sum(len(v) for v in res.values())
    check("оборванный прогон: хвост из частей поднят, ничего не потеряно",
          total == 65, f"подняли {total}, ждали 65 (50 из книги + 15 из хвоста)")
    check("прочитаны только свежие части", reads["n"] == 4,
          f"чтений: {reads['n']} (ждали 4: книга + 3 свежие части)")

    names = {o.name for v in res.values() for o in v}
    check("именно хвостовые НП на месте",
          all(f"АЗС НП-{i} 0" in names for i in (10, 11, 12)),
          sorted(n for n in names if "НП-1" in n)[:6])

print()
if fails:
    print(f"❌ ПРОВАЛЫ ({len(fails)}): " + "; ".join(fails))
    sys.exit(1)
print("✅ СТАРТ ДОКАЧКИ БЫСТРЫЙ И НИЧЕГО НЕ ТЕРЯЕТ")
