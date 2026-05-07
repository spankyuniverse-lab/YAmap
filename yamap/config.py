"""YAML/JSON конфиг."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any


log = logging.getLogger("yandex_parser")


DEFAULT_CONFIG: dict[str, Any] = {
    "max_results": 500,
    "scroll_pause": 1.0,
    "headless": True,
    "detail": False,
    "api_intercept": False,
    "output": "results.xlsx",
    "proxy": None,
    "proxy_file": None,
    "city": None,
    "categories": None,
}


def load_config(path: str) -> dict[str, Any]:
    """Загрузить конфиг из YAML или JSON файла."""
    p = Path(path)
    if not p.exists():
        log.warning("Конфиг-файл не найден: %s", path)
        return {}

    text = p.read_text(encoding="utf-8")
    if p.suffix in (".yaml", ".yml"):
        try:
            import yaml
            return yaml.safe_load(text) or {}
        except ImportError:
            log.warning("PyYAML не установлен — пробую как JSON")
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        log.warning("Ошибка чтения конфига %s: %s", path, exc)
        return {}
