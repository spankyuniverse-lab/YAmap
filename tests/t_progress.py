"""Живой прогресс должен показывать, ЧЕМ занят воркер, а не голый счётчик.
Кормим парсер настоящим форматом строк из лога воркера."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import yandex_parser as y

HERE = Path(__file__).resolve().parent
y.LOGS_DIR = HERE / "logs"
y.LOGS_DIR.mkdir(parents=True, exist_ok=True)

(y.LOGS_DIR / "worker1.log").write_text("""
02:46:51  INFO      Города: 10 к сбору (всего 10) | категорий: 5
02:46:52  INFO      ═══ Город 1/10: Алматы ═══
02:46:55  INFO      ━━━ [1/5] Заправки Алматы ━━━
02:48:10  INFO      Категория «Заправки»: 128 организаций (уникальных)
02:48:11  INFO      ━━━ [2/5] Где поесть Алматы ━━━
02:52:40  INFO      Категория «Где поесть»: 342 организаций (уникальных)
02:52:41  INFO      ═══ Город 2/10: Шымкент ═══
02:52:45  INFO      ━━━ [1/5] Заправки Шымкент ━━━
""".strip(), encoding="utf-8")

st = y._worker_state(1)
print("состояние w1:", st)
fails = []
for must in ("Шымкент", "2/10", "Заправки"):
    ok = must in st
    print(("OK  " if ok else "FAIL") + f" | видно «{must}»")
    if not ok:
        fails.append(must)

# Пустой/отсутствующий лог не должен ронять родителя
(y.LOGS_DIR / "worker2.log").write_text("", encoding="utf-8")
print("пустой лог →", repr(y._worker_state(2)))
print("нет файла  →", repr(y._worker_state(99)))
ok = y._worker_state(2) == "стартует…" and y._worker_state(99) == "стартует…"
print(("OK  " if ok else "FAIL") + " | пустой/отсутствующий лог не роняет")
if not ok:
    fails.append("empty")

# Хвост большого лога читается без вычитывания целиком
big = y.LOGS_DIR / "worker3.log"
big.write_text("мусор\n" * 200000 + "02:00:00  INFO      ═══ Город 7/19: Актау ═══\n",
               encoding="utf-8")
st3 = y._worker_state(3)
print("большой лог →", st3)
ok = "Актау" in st3
print(("OK  " if ok else "FAIL") + " | хвост большого лога разобран")
if not ok:
    fails.append("big")

# Имя файла без расширения
cases = [("1", "1.xlsx"), ("", "results.xlsx"), ("kz.xlsx", "kz.xlsx"),
         ("данные.csv", "данные.csv"), ('"мой файл"', "мой файл.xlsx")]
for raw, exp in cases:
    got = y._ensure_ext(raw)
    ok = got == exp
    print(("OK  " if ok else "FAIL") + f" | имя {raw!r} → {got!r}")
    if not ok:
        fails.append(raw)

print("\n" + ("✅ ПРОГРЕСС ОК" if not fails else f"❌ ПРОВАЛЫ: {fails}"))
sys.exit(1 if fails else 0)
