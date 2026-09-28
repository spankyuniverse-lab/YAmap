"""Строка поиска обязана очищаться перед новым запросом.

Регресс на боевой баг: очистка делалась через «Ctrl+A → Delete», а на macOS
выделяет всё Cmd+A. Поле не чистилось, запросы склеивались («Кафе Астана
заправки Астана»), и все категории после первой искались мусором.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import yandex_parser as y
from patchright.sync_api import sync_playwright

HTML = """<!doctype html><meta charset="utf-8">
<input class="input__control" placeholder="Поиск мест и адресов">
<div id="log"></div>"""

fails = []
def check(cond, msg):
    print(("OK  " if cond else "FAIL") + " | " + msg)
    if not cond:
        fails.append(msg)

exe = os.environ.get("YAMAP_BROWSER_PATH") or next(
    (c for c in ("/opt/pw-browsers/chromium",
                 "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                 "/usr/bin/google-chrome", "/usr/bin/chromium")
     if Path(c).exists()), None)

with sync_playwright() as pw:
    b = (pw.chromium.launch(executable_path=exe, headless=True) if exe
         else pw.chromium.launch(headless=True))
    page = b.new_page()
    page.set_content(HTML)
    sel = "input[class*='input__control']"

    ok1 = y._type_like_human(page, sel, "Заправки Астана")
    v1 = page.input_value(sel)
    check(ok1 and v1 == "Заправки Астана", f"первый запрос введён: {v1!r}")

    # Тот же элемент, второй запрос — старый текст должен исчезнуть
    ok2 = y._type_like_human(page, sel, "Кафе Астана")
    v2 = page.input_value(sel)
    check(v2 == "Кафе Астана", f"второй запрос НЕ склеился со старым: {v2!r}")
    check("Заправки" not in v2, "от прошлого запроса не осталось хвоста")
    check(ok2, "функция подтвердила, что в поле ровно нужный текст")

    # Третий подряд — на случай накопления
    y._type_like_human(page, sel, "Магазин продуктов Алматы")
    v3 = page.input_value(sel)
    check(v3 == "Магазин продуктов Алматы", f"третий запрос чистый: {v3!r}")

    # Кириллица с пробелами и длинный текст
    long_q = "Магазин смешанных товаров Усть-Каменогорск"
    y._type_like_human(page, sel, long_q)
    check(page.input_value(sel) == long_q, "длинный запрос с дефисом введён целиком")
    b.close()

print("\n" + ("✅ СТРОКА ПОИСКА ОК" if not fails else f"❌ ПРОВАЛЫ: {fails}"))
sys.exit(1 if fails else 0)
