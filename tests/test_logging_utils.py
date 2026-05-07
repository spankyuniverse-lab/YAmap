"""Тесты yamap.logging_utils — setup_logging."""

from __future__ import annotations

import logging

import pytest

from yamap.logging_utils import setup_logging


@pytest.fixture(autouse=True)
def _reset_logger_after_each_test():
    """После каждого теста возвращаем логгер в дефолт (только консоль)."""
    yield
    setup_logging()


class TestSetupLogging:
    def test_default_creates_console_handler(self):
        setup_logging()
        root = logging.getLogger("yandex_parser")
        # Должен быть ровно один stream handler, без файла
        stream_handlers = [
            h for h in root.handlers if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
        ]
        assert len(stream_handlers) >= 1

    def test_file_logging_creates_file(self, tmp_path):
        log_path = tmp_path / "yamap.log"
        setup_logging(log_file=log_path)
        root = logging.getLogger("yandex_parser")
        root.info("hello world")
        # Принудительный flush
        for h in root.handlers:
            h.flush()
        assert log_path.exists()
        content = log_path.read_text(encoding="utf-8")
        assert "hello world" in content

    def test_debug_logged_to_file_only(self, tmp_path):
        log_path = tmp_path / "yamap.log"
        setup_logging(log_file=log_path)
        root = logging.getLogger("yandex_parser")
        root.debug("debug-msg")
        root.info("info-msg")
        for h in root.handlers:
            h.flush()
        content = log_path.read_text(encoding="utf-8")
        assert "debug-msg" in content
        assert "info-msg" in content

    def test_repeated_setup_clears_old_handlers(self, tmp_path):
        setup_logging()
        n1 = len(logging.getLogger("yandex_parser").handlers)
        setup_logging()
        n2 = len(logging.getLogger("yandex_parser").handlers)
        assert n1 == n2  # повторный вызов не наслаивает

    def test_creates_parent_dirs(self, tmp_path):
        # Лог-файл в несуществующей поддиректории создаётся
        log_path = tmp_path / "deep" / "nested" / "log.txt"
        setup_logging(log_file=log_path)
        root = logging.getLogger("yandex_parser")
        root.info("test")
        for h in root.handlers:
            h.flush()
        assert log_path.exists()

    def test_console_handler_at_info_level(self, tmp_path):
        log_path = tmp_path / "x.log"
        setup_logging(log_file=log_path)
        root = logging.getLogger("yandex_parser")
        # Консоль — INFO; файл — DEBUG
        for h in root.handlers:
            if isinstance(h, logging.FileHandler):
                assert h.level == logging.DEBUG
            else:
                assert h.level == logging.INFO
