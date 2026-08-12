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
    # Все 5 категорий GT (Enter = дефолт), 2 браузера (Enter = дефолт)
    ("1\n\n\n\n\n", ["--all-cities", "--category", "gt", "-o", "kz_gt.xlsx",
                     "--workers", "2", "--api-intercept"]),
    # Только Заправки, 3 браузера
    ("1\n2\n3\n\n\n", ["--all-cities", "--category", "gt-fuel", "-o", "kz_azs.xlsx",
                       "--workers", "3", "--api-intercept"]),
    # Только магазины, 1 браузер, своё имя файла
    ("1\n3\n1\nмой.xlsx\n\n", ["--all-cities", "--category", "gt-grocery",
                               "-o", "мой.xlsx", "--workers", "1", "--api-intercept"]),
    # Только «Где поесть»
    ("1\n4\n2\n\n\n", ["--all-cities", "--category", "gt-food", "-o", "kz_food.xlsx",
                       "--workers", "2", "--api-intercept"]),
    # Мусор в числе браузеров → безопасный дефолт 2
    ("1\n1\n99\n\n\n", ["--all-cities", "--category", "gt", "-o", "kz_gt.xlsx",
                        "--workers", "2", "--api-intercept"]),
    # Имя без расширения → дописываем .xlsx, а не создаём файл «1»
    ("1\n2\n2\n1\n\n", ["--all-cities", "--category", "gt-fuel", "-o", "1.xlsx",
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
