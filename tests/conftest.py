"""Pytest конфиг: подменяем patchright, чтобы тесты не требовали браузера."""

from __future__ import annotations

import sys
import types


def _stub_playwright() -> None:
    """Заменить patchright на заглушки до импорта yamap.*"""
    if "patchright" in sys.modules:
        return

    fake_pr = types.ModuleType("patchright")
    fake_api = types.ModuleType("patchright.sync_api")

    class _Stub:
        def __init__(self, *args, **kwargs):
            pass

    for name in ("Browser", "BrowserContext", "Page", "Response"):
        setattr(fake_api, name, _Stub)
    fake_api.sync_playwright = _Stub

    sys.modules["patchright"] = fake_pr
    sys.modules["patchright.sync_api"] = fake_api


_stub_playwright()
