"""Меню в PyCharm: жмём ▶ без аргументов и проходим быстрый путь GT.
Проверяем, что выбор превращается в правильную командную строку."""
import sys, io
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import yandex_parser as y

captured = {}
def fake_main():
    captured["argv"] = list(sys.argv[1:])
y.main = fake_main

CASES = [
    # (ввод пользователя, ожидаемые аргументы)
    ("1\n1\n\n\n\n",  ["--all-cities", "--category", "gt-fuel", "-o", "kz_azs.xlsx",
                       "--workers", "2", "--api-intercept"]),
    ("1\n2\n3\n\n\n", ["--all-cities", "--category", "gt-grocery", "-o", "kz_grocery.xlsx",
                       "--workers", "3", "--api-intercept"]),
    ("1\n3\n1\nмой.xlsx\n\n", ["--all-cities", "--category", "gt", "-o", "мой.xlsx",
                               "--workers", "1", "--api-intercept"]),
    # мусор в числе браузеров → безопасный дефолт 2
    ("1\n1\n99\n\n\n", ["--all-cities", "--category", "gt-fuel", "-o", "kz_azs.xlsx",
                        "--workers", "2", "--api-intercept"]),
]
fails = []
for keys, expect in CASES:
    captured.clear()
    sys.argv = ["yandex_parser.py"]
    sys.stdin = io.StringIO("1\n" + keys)   # 1 = домен Казахстан
    buf, real = io.StringIO(), sys.stdout
    sys.stdout = buf
    try:
        y.interactive_menu()
    finally:
        sys.stdout = real
    got = captured.get("argv")
    ok = got == expect
    print(("OK  " if ok else "FAIL") + f" | ввод {keys!r}")
    if not ok:
        print(f"       ждал:  {expect}\n       вышло: {got}")
        fails.append(keys)

# Отказ на подтверждении не должен ничего запускать
captured.clear()
sys.argv = ["yandex_parser.py"]
sys.stdin = io.StringIO("1\n1\n1\n\n\nn\n")
buf, real = io.StringIO(), sys.stdout
sys.stdout = buf
try:
    y.interactive_menu()
finally:
    sys.stdout = real
if captured:
    print("FAIL | запустил сбор, хотя пользователь отказался")
    fails.append("отказ")
else:
    print("OK   | отказ на подтверждении ничего не запускает")

print("\n" + ("✅ МЕНЮ ОК" if not fails else f"❌ ПРОВАЛЫ: {fails}"))
sys.exit(1 if fails else 0)
