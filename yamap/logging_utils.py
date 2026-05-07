"""Настройка логирования: stderr + опциональный файл с ротацией."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


_LOG_FORMAT = "%(asctime)s  %(levelname)-8s  %(message)s"
_LOG_DATEFMT = "%H:%M:%S"
_FILE_LOG_FORMAT = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
_FILE_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(log_file: str | Path | None = None, level: int = logging.INFO) -> None:
    """Настроить корневой логгер: консоль + опционально файл с ротацией.

    Файл-хендлер ротируется по 5 МБ, держит 3 архивных копии.
    Если включён файл-лог, root опускается до DEBUG чтобы handler сам фильтровал.
    """
    root = logging.getLogger("yandex_parser")
    # Если есть файл-лог, нужен DEBUG на корне — handler уровни решат остальное
    root.setLevel(logging.DEBUG if log_file else level)

    # Чистим обработчики на случай повторного вызова
    for h in list(root.handlers):
        root.removeHandler(h)

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))
    console.setLevel(level)
    root.addHandler(console)

    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            path,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        fh.setFormatter(logging.Formatter(_FILE_LOG_FORMAT, datefmt=_FILE_LOG_DATEFMT))
        # В файл — DEBUG, чтобы был полный лог для расследования
        fh.setLevel(logging.DEBUG)
        root.addHandler(fh)
        root.info("Лог-файл: %s (ротация 5MB × 3 копии)", path)
