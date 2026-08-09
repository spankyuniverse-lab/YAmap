import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import yandex_parser as y
from patchright.sync_api import sync_playwright

MARK = "document.body.setAttribute('data-clicked', this.getAttribute('data-id'))"
HTML = f"""
<div class="sidebar-dropdown-container" style="position:relative">
  <div class="_1a2b_search-full-filters-view__show-more">
    <button role="button" aria-pressed="true" aria-haspopup="true" data-id="filters"
            aria-label="Все фильтры" class="button _view_secondary-gray _ui"
            onclick="{MARK}">Все фильтры</button>
  </div>
  <div class="search-full-filters-view__title" style="position:absolute;inset:0;z-index:99">overlay</div>
</div>
<div class="_x_search-list-view">
  <div class="_x_search-list-view__more">
    <button class="button _y_show-more" data-id="real" onclick="{MARK}">Показать ещё</button>
  </div>
</div>
"""
HTML_NO_BUTTON = f"""
<div class="sidebar-dropdown-container">
  <div class="_1a2b_search-full-filters-view__show-more">
    <button aria-label="Все фильтры" data-id="filters" class="button" onclick="{MARK}">Все фильтры</button>
  </div>
</div>
"""
# Кнопка есть, но скрыта (Яндекс так делает, когда подгружать больше нечего)
HTML_HIDDEN = f"""
<div class="_x_search-list-view"><div class="_x_search-list-view__more" style="display:none">
  <button class="_y_show-more" data-id="hidden" onclick="{MARK}">Показать ещё</button>
</div></div>
"""

with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium", headless=True)
    page = b.new_page()
    y.get_selector_engine = lambda: {"show_more": y.SHOW_MORE_SEL}
    fails = []
    for label, html, expect in [("настоящая кнопка + фильтры-ловушка", HTML, "real"),
                                ("только фильтры", HTML_NO_BUTTON, None),
                                ("кнопка скрыта", HTML_HIDDEN, None)]:
        page.set_content(html)
        old = "[class*='show-more'] button, [class*='search-list-view__more'] button"
        c = page.locator(old).first
        was = (c.get_attribute("aria-label") or c.inner_text()) if c.count() else "ничего"
        ok = y._click_show_more(page)
        clicked = page.get_attribute("body", "data-clicked")
        good = clicked == expect and ok == (expect is not None)
        print(f"{'OK ' if good else 'FAIL'} | {label}")
        print(f"       старый селектор ловил: {was!r}")
        print(f"       новый: rc={ok}, кликнуто={clicked!r} (ждём {expect!r})")
        if not good:
            fails.append(label)
    b.close()
print("\n" + ("✅ ВСЁ ОК" if not fails else f"❌ ПРОВАЛЫ: {fails}"))
sys.exit(1 if fails else 0)
