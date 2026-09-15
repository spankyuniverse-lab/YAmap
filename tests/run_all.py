#!/usr/bin/env python3
"""Прогнать все тесты. Запуск: python tests/run_all.py

Тесты не ходят в интернет и не открывают Яндекс — они проверяют логику,
которая ломалась в бою: сборку командной строки воркеров, нарезку работы,
дедупликацию при слиянии, докачку, подъём упавшего воркера и клик по
«Показать ещё» (на локальном HTML, без сети).

t_showmore требует установленного браузера (patchright/playwright); если его
нет, тест помечается как пропущенный, остальные всё равно идут.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = ["t_cli", "t_dedup", "t_gt", "t_badname", "t_progress", "t_menu", "t_par", "t_cities", "t_expand", "t_supervise", "t_showmore", "t_search_input", "t_kz_geo", "t_opts", "t_fastapi", "t_rundir", "t_silent", "t_speed", "t_migrate", "t_sheets", "t_crashlog", "t_savecount", "t_onecity", "t_resume_speed"]
JUNK = ["out", "logs", "runs", "reports", "crashy_worker.py", "fake_worker.py",
        "selectors_cache.json", "e2e.xlsx", "e2e_par.xlsx",
        "kz_osm_places.json"]


#: Тесты гоняют настоящий парсер против макета на 127.0.0.1. Выдержки, которые
#: в бою оберегают от капчи, тут изображают человека перед несуществующим
#: Яндексом: один прогон парсера против макета — 41 секунда, из них 14 уходит
#: на прогрев. Ужимаем намеренные паузы в 50 раз; технические таймауты
#: (загрузка страницы, появление карточек) множитель не трогает.
#: Сами значения пауз проверяет t_resume_speed при PACING = 1.0 — то есть
#: регрессию «кто-то убрал паузу» ловит он, а не секундомер.
TEST_ENV = {**os.environ, "YAMAP_PACING": os.environ.get("YAMAP_PACING", "0.02")}


def _clean() -> None:
    for name in JUNK:
        p = HERE / name
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink()
    for flag in HERE.glob("*.flag"):
        flag.unlink()
    # Профиль Chrome между тестами сносим. Persistent-профиль держит
    # SingletonLock, и если предыдущий тест не успел погасить браузер, все
    # следующие падают на «profile is already in use» — то есть на пустом
    # месте, но выглядит это как провал парсера.
    for prof in HERE.glob(".browser_profile*"):
        shutil.rmtree(prof, ignore_errors=True)
    for junk in list(HERE.glob("speed*")) + list(HERE.glob("fa_*.xlsx")):
        if junk.is_dir():
            shutil.rmtree(junk, ignore_errors=True)
        else:
            junk.unlink()


#: Замок на каталог тестов. Прогон сносит общее хозяйство (out/, logs/,
#: crashy_worker.py, флаги, профили Chrome), поэтому два прогона в одной папке
#: калечат друг друга: пока t_supervise ждёт перезапуска воркера, соседний
#: прогон удаляет скрипт воркера — и воркер «падает с кодом 2» на ровном
#: месте. Ловилось это раз на два десятка запусков и выглядело как плавающая
#: ошибка парсера, хотя парсер тут ни при чём.
LOCK = HERE / ".run_all.lock"


def _take_lock() -> bool:
    """Занять каталог под прогон. False — занято живым процессом."""
    try:
        with open(LOCK, "x", encoding="utf-8") as f:
            f.write(str(os.getpid()))
        return True
    except FileExistsError:
        pass
    try:
        pid = int(LOCK.read_text(encoding="utf-8").strip())
    except Exception:
        pid = 0
    alive = False
    if pid:
        try:
            os.kill(pid, 0)         # сигнал 0 — только проверка, что процесс жив
            alive = True
        except OSError:
            alive = False
    if alive:
        print(f"Здесь уже идёт прогон тестов (pid {pid}).")
        print("Дождись его или закрой: два прогона в одной папке портят "
              "друг другу файлы.")
        return False
    # Замок от процесса, которого больше нет: прошлый прогон убили. Забираем.
    print(f"Снимаю замок от прогона, которого больше нет (pid {pid or '?'}).")
    LOCK.unlink(missing_ok=True)
    return _take_lock()


def main() -> int:
    if not _take_lock():
        return 2
    try:
        return _run()
    finally:
        LOCK.unlink(missing_ok=True)


def _run() -> int:
    failed: list[str] = []
    times: list[tuple[float, str]] = []
    started = time.time()
    for name in TESTS:
        _clean()
        print(f"\n{'=' * 60}\n  {name}\n{'=' * 60}")
        t0 = time.time()
        proc = subprocess.run([sys.executable, str(HERE / f"{name}.py")],
                              cwd=HERE, capture_output=True, text=True,
                              env=TEST_ENV)
        spent = time.time() - t0
        times.append((spent, name))
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-6:])
        print(tail)
        if proc.returncode == 0:
            print(f"  → OK ({spent:.1f} c)")
        elif "playwright" in tail.lower() or "executable doesn't exist" in tail.lower():
            print(f"  → ПРОПУЩЕН (нет браузера, {spent:.1f} c)")
        else:
            print(f"  → ПРОВАЛ (код {proc.returncode}, {spent:.1f} c)")
            failed.append(name)
    _clean()
    total = time.time() - started
    print(f"\n{'=' * 60}")
    print("  ВСЁ ЗЕЛЁНОЕ" if not failed else f"  ПРОВАЛЫ: {', '.join(failed)}")
    # Что именно съедает время — иначе ускорять приходится вслепую.
    print(f"  Всего {total:.0f} c. Самые долгие:")
    for spent, name in sorted(times, reverse=True)[:5]:
        print(f"    {spent:6.1f} c  {name}  ({spent / total * 100:.0f}%)")
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
