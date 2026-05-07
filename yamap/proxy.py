"""Ротация прокси-серверов."""

from __future__ import annotations

import logging
import urllib.parse
from pathlib import Path

log = logging.getLogger("yandex_parser")


class ProxyRotator:
    """Ротация прокси-серверов для обхода блокировок.

    Форматы прокси:
      - http://host:port
      - http://user:pass@host:port
      - socks5://host:port
    """

    def __init__(self, proxies: list[str] | None = None):
        self._proxies = proxies or []
        self._index = 0
        self._fail_counts: dict[str, int] = {}

    @classmethod
    def from_file(cls, path: str) -> "ProxyRotator":
        p = Path(path)
        if not p.exists():
            log.warning("Файл прокси не найден: %s", path)
            return cls([])
        lines = [
            line.strip() for line in p.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        log.info("Загружено %d прокси из %s", len(lines), path)
        return cls(lines)

    @property
    def has_proxies(self) -> bool:
        return len(self._proxies) > 0

    def next(self) -> str | None:
        if not self._proxies:
            return None
        proxy = self._proxies[self._index % len(self._proxies)]
        self._index += 1
        return proxy

    def current(self) -> str | None:
        if not self._proxies:
            return None
        return self._proxies[(self._index - 1) % len(self._proxies)]

    def mark_failed(self, proxy: str) -> None:
        self._fail_counts[proxy] = self._fail_counts.get(proxy, 0) + 1
        if self._fail_counts[proxy] >= 3:
            log.warning("Прокси %s — 3 ошибки, удаляю из ротации", proxy)
            self._proxies = [p for p in self._proxies if p != proxy]
            self._fail_counts.pop(proxy, None)

    def to_playwright_arg(self, proxy_url: str) -> dict:
        result: dict[str, str] = {"server": proxy_url}
        parsed = urllib.parse.urlparse(proxy_url)
        if parsed.username:
            result["username"] = parsed.username
        if parsed.password:
            result["password"] = parsed.password
        if parsed.username or parsed.password:
            result["server"] = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        return result


_proxy_rotator: ProxyRotator | None = None


def get_proxy_rotator() -> ProxyRotator:
    global _proxy_rotator
    if _proxy_rotator is None:
        _proxy_rotator = ProxyRotator()
    return _proxy_rotator


def set_proxy_rotator(rotator: ProxyRotator) -> None:
    global _proxy_rotator
    _proxy_rotator = rotator
