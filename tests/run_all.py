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

import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = ["t_cli", "t_par", "t_cities", "t_expand", "t_supervise", "t_showmore"]
JUNK = ["out", "logs", "crashy_worker.py", "fake_worker.py"]


def _clean() -> None:
    for name in JUNK:
        p = HERE / name
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        elif p.exists():
            p.unlink()
    for flag in HERE.glob("*.flag"):
        flag.unlink()


def main() -> int:
    failed: list[str] = []
    for name in TESTS:
        _clean()
        print(f"\n{'=' * 60}\n  {name}\n{'=' * 60}")
        proc = subprocess.run([sys.executable, str(HERE / f"{name}.py")],
                              cwd=HERE, capture_output=True, text=True)
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-6:])
        print(tail)
        if proc.returncode == 0:
            print(f"  → OK")
        elif "playwright" in tail.lower() or "executable doesn't exist" in tail.lower():
            print(f"  → ПРОПУЩЕН (нет браузера)")
        else:
            print(f"  → ПРОВАЛ (код {proc.returncode})")
            failed.append(name)
    _clean()
    print(f"\n{'=' * 60}")
    print("  ВСЁ ЗЕЛЁНОЕ" if not failed else f"  ПРОВАЛЫ: {', '.join(failed)}")
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
