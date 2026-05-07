"""Адаптивный троттлинг — замедляемся при появлении капчи."""

from __future__ import annotations

import logging
import random


log = logging.getLogger("yandex_parser")


class AdaptiveThrottle:
    """Адаптивное управление скоростью парсинга.

    При появлении капчи увеличиваем паузы.
    После 3 успешных запросов подряд возвращаемся к нормальной скорости.
    """

    def __init__(self) -> None:
        self._captcha_count = 0
        self._success_streak = 0
        self._multiplier = 1.0

    def on_captcha(self) -> None:
        self._captcha_count += 1
        self._success_streak = 0
        # Каждая капча удваивает замедление (макс x8)
        self._multiplier = min(8.0, self._multiplier * 2.0)
        log.info("Троттлинг: замедление x%.1f (капч: %d)", self._multiplier, self._captcha_count)

    def on_success(self) -> None:
        self._success_streak += 1
        if self._success_streak >= 3 and self._multiplier > 1.0:
            self._multiplier = max(1.0, self._multiplier * 0.7)
            self._success_streak = 0
            log.debug("Троттлинг: ускорение до x%.1f", self._multiplier)

    def get_pause(self, base_ms: int = 2000) -> int:
        pause = int(base_ms * self._multiplier)
        noise = int(pause * 0.2)
        return pause + random.randint(-noise, noise)

    @property
    def multiplier(self) -> float:
        return self._multiplier

    @property
    def captcha_count(self) -> int:
        return self._captcha_count


_throttle = AdaptiveThrottle()


def get_throttle() -> AdaptiveThrottle:
    return _throttle
