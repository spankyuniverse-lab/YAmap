# -*- coding: utf-8 -*-
"""Перенос прогонов старой раскладки в папки с датой.

До появления runs/ всё складывалось рядом со скриптом: по десятку файлов на
прогон, и понять, когда какой собран, можно было только по дате в Finder.
Перенос обязан быть безопасным — он двигает уже собранные мегабайты.
"""
import json
import os
import sys
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
    import tempfile
    start = Path.cwd()
    with tempfile.TemporaryDirectory() as td:
        os.chdir(td)
        try:
            f = Path(".")
            # Раскладка как у живого пользователя: два прогона, чужие файлы
            # и прогон, у которого главную книгу переименовали.
            for n in ("kz_aul.xlsx", "kz_aul.w1.xlsx", "kz_aul.w2.xlsx",
                      "kz_gt.w1.xlsx", "kz_gt.w2.xlsx",
                      "готовая_выгрузка.xlsx"):
                f.joinpath(n).write_bytes(b"x" * 64)
            f.joinpath("Архив.zip").write_bytes(b"z" * 64)
            for n in ("kz_aul.w1.cities.json", "kz_gt.w1.cities.json"):
                f.joinpath(n).write_text(json.dumps(["Алматы"]), encoding="utf-8")
            for d in ("kz_aul.w1_parts", "kz_gt.w1_parts"):
                f.joinpath(d).mkdir()
                f.joinpath(d, "Алматы.xlsx").write_bytes(b"y" * 32)

            # kz_gt состарен — папка должна получить ЕГО дату, не сегодняшнюю
            when = time.mktime((2026, 8, 13, 18, 48, 0, 0, 0, -1))
            for n in list(f.glob("kz_gt*")):
                os.utime(n, (when, when))

            moved = y.migrate_legacy_runs()
            check("перенос что-то сделал", moved > 0, moved)

            runs = sorted(p.name for p in Path("runs").iterdir())
            check("оба прогона разложены по папкам", len(runs) == 2, runs)
            check("папка названа датой САМОГО прогона, а не сегодняшней",
                  "2026-08-13_18-48__kz_gt" in runs, runs)
            check("у прогона без главной книги имя взято из частей",
                  any(r.endswith("__kz_gt") for r in runs), runs)

            gt = Path("runs/2026-08-13_18-48__kz_gt")
            check("части воркеров переехали",
                  (gt / "kz_gt.w1.xlsx").exists() and (gt / "kz_gt.w2.xlsx").exists())
            check("прогресс по НП переехал — иначе докачка начнётся с нуля",
                  (gt / "kz_gt.w1.cities.json").exists())
            check("папки частей переехали целиком",
                  (gt / "kz_gt.w1_parts" / "Алматы.xlsx").exists())

            check("чужие файлы не тронуты",
                  Path("Архив.zip").exists() and Path("готовая_выгрузка.xlsx").exists())
            check("в корне не осталось файлов прогонов",
                  not list(f.glob("kz_aul*")) and not list(f.glob("kz_gt.*")))

            # Докачка обязана найти перенесённое и продолжить ТАМ ЖЕ.
            cont = Path(y.run_output_path("kz_aul.xlsx"))
            check("следующий запуск продолжает в перенесённой папке",
                  cont.parent.name.endswith("__kz_aul")
                  and cont.parent.parent.name == "runs", cont)

            # Повторный перенос не должен ничего ломать.
            again = y.migrate_legacy_runs()
            check("повторный перенос — пустая операция, а не ошибка",
                  again == 0, again)

            # Занятую папку не перезаписываем.
            f.joinpath("kz_new.xlsx").write_bytes(b"x" * 64)
            f.joinpath("kz_new.cities.json").write_text("[]", encoding="utf-8")
            stamp = time.strftime("%Y-%m-%d_%H-%M")
            Path("runs", f"{stamp}__kz_new").mkdir(parents=True)
            Path("runs", f"{stamp}__kz_new", "занято.txt").write_text("!", encoding="utf-8")
            y.migrate_legacy_runs()
            check("занятую папку не трогаем, файлы остаются на месте",
                  Path("kz_new.xlsx").exists()
                  and not Path("runs", f"{stamp}__kz_new", "kz_new.xlsx").exists())
        finally:
            os.chdir(start)

    print("\n" + ("✅ ПЕРЕНОС ОК" if not fails else f"❌ ПРОВАЛЫ: {fails}"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
