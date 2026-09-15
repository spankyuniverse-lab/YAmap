#!/usr/bin/env python3
"""СКВОЗНОЙ тест: настоящий парсер + настоящий браузер против макета Яндекса.

Это то, чего не хватало: остальные тесты проверяют куски, а тут гоняется вся
цепочка — запуск браузера, поиск, скролл с подгрузкой, разбор SSR, дедуп,
запись книги, докачка, параллельные воркеры и слияние.

Требует браузер (patchright/playwright). Нет браузера — тест помечается
пропущенным, а не падает.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import mock_yandex  # noqa: E402

CATALOG = {"Заправки": 30, "Супермаркет": 18, "Кафе": 14, "Ресторан": 11}

fails: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("OK   " if cond else "FAIL ") + "| " + msg)
    if not cond:
        fails.append(msg)


def rows(path: Path) -> list[dict]:
    from openpyxl import load_workbook
    wb = load_workbook(path)
    ws = wb["Все результаты"] if "Все результаты" in wb.sheetnames else wb.active
    head = [c.value for c in ws[1]]
    return [dict(zip(head, [c.value for c in r])) for r in ws.iter_rows(min_row=2)]


def run(args: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(HERE.parent / "yandex_parser.py"), *args],
        cwd=HERE, env=env, capture_output=True, text=True, timeout=600)


def main() -> int:
    browser = os.environ.get("YAMAP_BROWSER_PATH")
    if not browser:
        for cand in ("/opt/pw-browsers/chromium",
                     "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
            if Path(cand).exists():
                browser = cand
                break
    if not browser:
        print("ПРОПУСК: браузер не найден (playwright/chromium не установлен)")
        return 0

    base, srv = mock_yandex.start(CATALOG)
    print(f"макет Яндекса поднят на {base}\n")

    env = dict(os.environ)
    env.update({
        "YAMAP_BASE_URL": base,
        "YAMAP_BROWSER_PATH": browser,
        "YAMAP_HEADLESS": "1",          # в тесте окно ни к чему
        "PYTHONIOENCODING": "utf-8",
    })
    out = HERE / "e2e.xlsx"
    for junk in HERE.glob("e2e*"):
        if junk.is_file():
            junk.unlink()

    try:
        # ---- 1. Один город, три категории ----
        print("--- прогон: один город, 3 категории ---")
        r = run(["--city", "Алматы", "--category", "Заправки", "Супермаркет",
                 "Кафе", "Ресторан", "-o", str(out), "--api-intercept", "-n", "500"], env)
        check(r.returncode == 0, f"парсер отработал без ошибки (код {r.returncode})")
        if r.returncode != 0:
            print(r.stdout[-3000:], r.stderr[-3000:])
            return 1
        check(out.exists(), "книга создана")

        data = rows(out)
        got = len(data)
        want = sum(CATALOG.values())
        check(got == want, f"собрано {got} из {want} (все страницы подгрузились)")

        by_cat: dict[str, int] = {}
        for row in data:
            by_cat[row.get("Поисковый запрос") or "?"] = \
                by_cat.get(row.get("Поисковый запрос") or "?", 0) + 1
        # «Кафе» и «Ресторан» — два запроса ОДНОЙ категории «Поесть»
        want_cat = {"Заправки": 30, "Продуктовые магазины": 18, "Поесть": 14 + 11}
        check(by_cat == want_cat, f"разбивка по категориям: {by_cat} (ждали {want_cat})")
        check(sum(1 for row in data if row.get("Запрос") == "Ресторан") == 11,
              "точный запрос сохранён в колонке «Запрос»")

        filled = lambda k: sum(1 for row in data if str(row.get(k) or "").strip())
        check(filled("Телефон") == got, f"телефон у всех: {filled('Телефон')}/{got}")
        check(filled("Адрес") == got, f"адрес у всех: {filled('Адрес')}/{got}")
        check(filled("Широта") == got, f"координаты у всех: {filled('Широта')}/{got}")
        check(filled("Город (прогон)") == got,
              f"город проставлен: {filled('Город (прогон)')}/{got}")
        check(all(row.get("Город (прогон)") == "Алматы" for row in data),
              "город именно тот, что просили")

        names = [row.get("Название") for row in data]
        check(len(names) == len(set(names)), "дублей нет")

        # Ловушка «Все фильтры» не должна быть нажата
        check("Timeout" not in r.stdout and "Timeout" not in r.stderr,
              "по кнопке «Все фильтры» не кликали (нет таймаутов)")

        # ---- 2. Докачка: повторный прогон не дублирует ----
        print("\n--- прогон: докачка поверх готового файла ---")
        r2 = run(["--city", "Алматы", "--category", "Заправки", "-o", str(out),
                  "--api-intercept", "--resume", str(out)], env)
        check(r2.returncode == 0, "докачка отработала")
        data2 = rows(out)
        check(len(data2) == want, f"после докачки по-прежнему {len(data2)}, не {want * 2}")

        # ---- 3. Параллельный режим + слияние ----
        print("\n--- прогон: 2 воркера + слияние ---")
        par = HERE / "e2e_par.xlsx"
        for junk in HERE.glob("e2e_par*"):
            if junk.is_file():
                junk.unlink()
        r3 = run(["--all-cities", "--cities", "Алматы,Астана,Шымкент,Караганда",
                  "--category", "Заправки", "-o", str(par),
                  "--workers", "2", "--api-intercept"], env)
        check(r3.returncode == 0, f"параллельный прогон отработал (код {r3.returncode})")
        if not par.exists():
            print(r3.stdout[-4000:])
        check(par.exists(), "итоговая книга слита")
        if par.exists():
            pdata = rows(par)
            cities = {row.get("Город (прогон)") for row in pdata}
            check(cities == {"Алматы", "Астана", "Шымкент", "Караганда"},
                  f"в файле все 4 города: {sorted(c for c in cities if c)}")
            check(len(pdata) == 4 * CATALOG["Заправки"],
                  f"строк {len(pdata)}, ждали {4 * CATALOG['Заправки']}")
            check("Слито из 2 воркеров: 0" not in r3.stdout,
                  "слияние не обнулило результат")
    finally:
        srv.shutdown()

    print("\n" + ("✅ СКВОЗНОЙ ТЕСТ ПРОЙДЕН" if not fails
                  else f"❌ ПРОВАЛЫ ({len(fails)}): " + "; ".join(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
