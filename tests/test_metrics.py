"""Тесты yamap.metrics — RunMetrics."""

from __future__ import annotations

import time

from yamap.metrics import RunMetrics


class TestRunMetrics:
    def test_default_zeros(self):
        m = RunMetrics()
        assert m.queries_total == 0
        assert m.queries_done == 0
        assert m.queries_empty == 0
        assert m.orgs_collected == 0
        assert m.captcha_hits == 0
        assert m.interrupted is False

    def test_elapsed_grows(self):
        m = RunMetrics()
        time.sleep(0.02)
        assert m.elapsed_sec > 0

    def test_orgs_per_sec_zero_when_no_data(self):
        m = RunMetrics()
        m.orgs_collected = 0
        assert m.orgs_per_sec == 0.0

    def test_orgs_per_sec_calculated(self):
        m = RunMetrics()
        time.sleep(0.05)
        m.orgs_collected = 100
        rate = m.orgs_per_sec
        assert rate > 0

    def test_captcha_rate_zero_no_queries(self):
        m = RunMetrics(queries_total=10)
        assert m.captcha_rate == 0.0

    def test_captcha_rate_calculated(self):
        m = RunMetrics(queries_total=10)
        m.queries_done = 5
        m.captcha_hits = 2
        assert m.captcha_rate == 0.4

    def test_report_contains_key_fields(self):
        m = RunMetrics(queries_total=10)
        m.queries_done = 7
        m.orgs_collected = 350
        m.captcha_hits = 1
        report = m.report("Test label")
        assert "Test label" in report
        assert "7/10" in report
        assert "350" in report

    def test_report_short_time_format_mm_ss(self):
        m = RunMetrics()
        report = m.report()
        # Короткое время — MM:SS, не HH:MM:SS
        time_lines = [l for l in report.splitlines() if "Время:" in l]
        assert time_lines
        assert time_lines[0].count(":") == 2  # "Время:" + "MM:SS" = 2 двоеточия

    def test_report_includes_interrupted_flag(self):
        m = RunMetrics()
        m.interrupted = True
        report = m.report()
        assert "ПРЕРВАНО" in report

    def test_report_omits_interrupted_when_false(self):
        m = RunMetrics()
        report = m.report()
        assert "ПРЕРВАНО" not in report

    def test_report_shows_empty_queries_only_when_present(self):
        m = RunMetrics(queries_total=10)
        m.queries_done = 5
        m.queries_empty = 0
        assert "Пустых" not in m.report()

        m.queries_empty = 2
        assert "Пустых" in m.report()
