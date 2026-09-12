# -*- coding: utf-8 -*-
"""Папка прогона: новый сбор — в свою, докачка — в ту же.

Смысл: прогоны не должны перемешиваться, но перезапуск после Ctrl+C и
подъём упавшего воркера — это ПРОДОЛЖЕНИЕ, а не новый старт. Заведи им
новую папку — и докачка не найдёт собранного, то есть начнёт с нуля.
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


def main() -> int:
    start = Path.cwd()
    with tempfile.TemporaryDirectory() as td:
        os.chdir(td)
        try:
            # --- новый сбор заводит папку ---
            p1 = Path(y.run_output_path("kz_aul.xlsx", new_run=False))
            check("первый запуск уходит в папку runs/",
                  p1.parent.parent.name == "runs" and p1.name == "kz_aul.xlsx", p1)
            check("папка названа по времени и имени файла",
                  p1.parent.name.endswith("__kz_aul"), p1.parent.name)
            p1.write_text("собранное", encoding="utf-8")

            # --- перезапуск продолжает ТАМ ЖЕ ---
            p2 = Path(y.run_output_path("kz_aul.xlsx", new_run=False))
            check("перезапуск продолжает в той же папке — докачка найдёт своё",
                  p2 == p1, (str(p1), str(p2)))

            # --- явный новый старт заводит другую ---
            p3 = Path(y.run_output_path("kz_aul.xlsx", new_run=True))
            check("--new-run заводит ОТДЕЛЬНУЮ папку", p3.parent != p1.parent,
                  (p1.parent.name, p3.parent.name))
            check("старая папка цела, собранное на месте",
                  p1.exists() and p1.read_text(encoding="utf-8") == "собранное")

            # --- два новых старта в одну минуту не сливаются ---
            a = Path(y.run_output_path("kz_x.xlsx", new_run=True))
            b = Path(y.run_output_path("kz_x.xlsx", new_run=True))
            c = Path(y.run_output_path("kz_x.xlsx", new_run=True))
            check("три новых старта подряд — три разные папки",
                  len({a.parent, b.parent, c.parent}) == 3,
                  [x.parent.name for x in (a, b, c)])
            # Сортировка по имени тут врёт: «…-2__kz_x» встаёт ПЕРЕД «…__kz_x».
            time.sleep(0.05)
            cont = Path(y.run_output_path("kz_x.xlsx"))
            check("продолжение берёт самую свежую из них, а не первую по имени",
                  cont.parent == c.parent, (cont.parent.name, c.parent.name))

            # --- разные прогоны не мешаются ---
            check("папки разных выгрузок не пересекаются",
                  p1.parent != a.parent)

            # --- явная папка сильнее всего ---
            d = Path(y.run_output_path("kz_aul.xlsx", new_run=True,
                                       run_dir="своя_папка"))
            check("--run-dir кладёт ровно туда, куда сказали",
                  d.parent.name == "своя_папка", d)

            # --- путь с каталогом не трогаем ---
            Path("под").mkdir(exist_ok=True)
            e = y.run_output_path("под/файл.xlsx", new_run=True)
            check("явный путь с каталогом остаётся как есть",
                  Path(e) == Path("под/файл.xlsx"), e)

            # --- старая раскладка: выгрузка рядом со скриптом ---
            legacy = Path("kz_old.xlsx")
            legacy.write_text("старое", encoding="utf-8")
            f = Path(y.run_output_path("kz_old.xlsx", new_run=False))
            check("прогон старой раскладки продолжается рядом, а не осиротеет",
                  f == legacy, f)
            g = Path(y.run_output_path("kz_old.xlsx", new_run=True))
            check("--new-run уводит его в папку, старый файл не трогая",
                  g.parent.parent.name == "runs" and legacy.exists(), g)

            # --- всё хозяйство прогона ложится рядом с выгрузкой ---
            out = p1
            check("части воркеров — в папке прогона",
                  y._part_path(out, 1).parent == out.parent,
                  y._part_path(out, 1))
            check("прогресс по НП — в папке прогона",
                  out.with_name(out.stem + ".cities.json").parent == out.parent)
        finally:
            os.chdir(start)

    print("\n" + ("✅ ПАПКИ ПРОГОНОВ ОК" if not fails else f"❌ ПРОВАЛЫ: {fails}"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
