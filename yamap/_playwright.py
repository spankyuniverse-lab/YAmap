"""Импорт patchright или fallback на playwright.

Patchright — drop-in замена Playwright без Runtime.enable CDP-утечки.
SmartCaptcha/Cloudflare/DataDome детектят Playwright через Runtime.enable —
patchright обходит это, выполняя JS в изолированных execution contexts.
"""

from __future__ import annotations

try:
    from patchright.sync_api import (
        Browser,
        BrowserContext,
        Page,
        Response,
        sync_playwright,
    )
    LIB_NAME = "patchright"
except ImportError:
    from playwright.sync_api import (  # type: ignore[assignment]
        Browser,
        BrowserContext,
        Page,
        Response,
        sync_playwright,
    )
    LIB_NAME = "playwright"


__all__ = ["Browser", "BrowserContext", "Page", "Response", "sync_playwright", "LIB_NAME"]
