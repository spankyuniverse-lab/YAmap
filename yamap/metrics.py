"""Run metrics: время, скорость, статистика капч."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field


log = logging.getLogger("yandex_parser")


@dataclass
class RunMetrics:
    """Метрики одного запуска парсера."""

    start_time: float = field(default_factory=time.monotonic)
    queries_total: int = 0
    queries_done: int = 0
    queries_empty: int = 0
    orgs_collected: int = 0
    captcha_hits: int = 0
    interrupted: bool = False

    @property
    def elapsed_sec(self) -> float:
        return time.monotonic() - self.start_time

    @property
    def orgs_per_sec(self) -> float:
        e = self.elapsed_sec
        return self.orgs_collected / e if e > 0 else 0.0

    @property
    def captcha_rate(self) -> float:
        if self.queries_done == 0:
            return 0.0
        return self.captcha_hits / self.queries_done

    def report(self, label: str = "Run metrics") -> str:
        elapsed = self.elapsed_sec
        h, rem = divmod(int(elapsed), 3600)
        m, s = divmod(rem, 60)
        time_str = f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"

        lines = [
            "",
            "=" * 50,
            f"  {label}",
            "=" * 50,
            f"  Время:               {time_str}",
            f"  Запросов выполнено:  {self.queries_done}/{self.queries_total}",
        ]
        if self.queries_empty:
            lines.append(f"  Пустых запросов:     {self.queries_empty}")
        lines += [
            f"  Организаций:         {self.orgs_collected}",
            f"  Скорость:            {self.orgs_per_sec:.1f} орг/сек",
            f"  Капч встречено:      {self.captcha_hits}",
        ]
        if self.queries_done:
            lines.append(f"  Частота капч:        {self.captcha_rate:.1%} (на запрос)")
        if self.interrupted:
            lines.append(f"  Статус:              ПРЕРВАНО ПОЛЬЗОВАТЕЛЕМ")
        lines.append("=" * 50)
        return "\n".join(lines)
