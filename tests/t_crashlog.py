# -*- coding: utf-8 -*-
"""Смерть воркера должна быть ВИДНА.

Воркер, который не смог даже стартовать, пишет причину сам — интерпретатором,
без логгера: «can't open file», ModuleNotFoundError, Traceback. Родитель такие
строки не замечал: сводка здоровья показывала «Ошибок в логах: 0» над тремя
подряд неудачными запусками, а причина оставалась в файле, который никто не
открывал.

Проверяем: причина распознаётся, попадает в хвост лога и считается ошибкой —
и при этом обычная работа воркера ошибкой НЕ считается.
"""
import io
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import yandex_parser as y

fails = []


def check(name, cond, detail=""):
    print(("OK   | " if cond else "FAIL | ") + name
          + (f"\n       {detail}" if not cond and detail else ""))
    if not cond:
        fails.append(name)


# --- 1. что считаем признаком падения -------------------------------------
CRASH = [
    "can't open file '/x/yandex_parser.py': [Errno 2] No such file or directory",
    "ModuleNotFoundError: No module named 'patchright'",
    "Traceback (most recent call last):",
    "MemoryError",
    "OSError: [Errno 28] No space left on device",
]
for line in CRASH:
    check(f"падение видно: {line[:40]}…", y._is_crash_line(line))
    check(f"и считается ошибкой: {line[:40]}…",
          y._log_line_level(line) in ("ERROR", "CRITICAL"))

NOT_CRASH = [
    "12:05:31  INFO      Воркер 1/2 запущен (pid 123)",
    "12:05:31  INFO      Собрано 140 организаций",
    # В выдаче встречаются организации со словами из стоп-листа в названии.
    # Строка с уровнем INFO не становится падением от одного слова в тексте.
    "12:05:31  INFO      Найдено: ТОО «Трейдинг», ул. Абая",
]
for line in NOT_CRASH:
    check(f"не падение: {line[28:60]}…", not y._is_crash_line(line))

# WARNING остаётся WARNING, а не превращается в ошибку.
check("предупреждение не стало ошибкой",
      y._log_line_level("12:05:31  WARNING   Капча — жду") == "WARNING")


# --- 2. хвост лога: причина есть, прогресс выброшен ------------------------
with tempfile.TemporaryDirectory() as tmp:
    old_logs = y.LOGS_DIR
    y.LOGS_DIR = Path(tmp)
    try:
        (Path(tmp) / "worker1.log").write_text("\n".join([
            "12:05:00  INFO      Воркер 1/2 запущен (pid 123)",
            "   [12:05:31]  всего ~140",
            "     w1  собрано 70 ⟳1",
            "     w2  собрано 70",
            "12:06:00  INFO      Перехожу к Шымкенту",
            "ModuleNotFoundError: No module named 'patchright'",
        ]), encoding="utf-8")

        tail = y._worker_log_tail(1)
        check("причина в хвосте",
              any("ModuleNotFoundError" in s for s in tail), tail)
        check("строки прогресса выброшены",
              not any("собрано" in s or "всего ~" in s for s in tail), tail)
        check("хвост не длиннее лимита", len(tail) <= 5, tail)
        check("порядок сохранён — причина последняя",
              tail[-1].startswith("ModuleNotFoundError"), tail)

        # Лога нет вовсе — не падаем, а честно возвращаем пусто.
        check("нет файла — пустой хвост, без исключения",
              y._worker_log_tail(9) == [])

        # --- 3. сводка здоровья больше не врёт ---------------------------
        buf = io.StringIO()
        with redirect_stdout(buf):
            y._print_health([], n=1)
        out = buf.getvalue()
        check("сводка насчитала ошибку", "Ошибок в логах:   0" not in out,
              out.strip()[-200:])
        check("и показала какую", "ModuleNotFoundError" in out,
              out.strip()[-200:])
    finally:
        y.LOGS_DIR = old_logs

print()
if fails:
    print(f"❌ ПРОВАЛЫ ({len(fails)}): " + "; ".join(fails))
    sys.exit(1)
print("✅ ПРИЧИНА СМЕРТИ ВОРКЕРА ВИДНА")
