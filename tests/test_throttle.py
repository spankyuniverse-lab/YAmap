"""Тесты yamap.throttle — AdaptiveThrottle."""

from __future__ import annotations

from yamap.throttle import AdaptiveThrottle


class TestAdaptiveThrottle:
    def test_initial_multiplier_is_one(self):
        t = AdaptiveThrottle()
        assert t.multiplier == 1.0
        assert t.captcha_count == 0

    def test_captcha_doubles_multiplier(self):
        t = AdaptiveThrottle()
        t.on_captcha()
        assert t.multiplier == 2.0
        t.on_captcha()
        assert t.multiplier == 4.0
        t.on_captcha()
        assert t.multiplier == 8.0

    def test_multiplier_caps_at_8(self):
        t = AdaptiveThrottle()
        for _ in range(10):
            t.on_captcha()
        assert t.multiplier == 8.0

    def test_three_successes_reduce_multiplier(self):
        t = AdaptiveThrottle()
        t.on_captcha()  # x2
        before = t.multiplier
        t.on_success()
        t.on_success()
        t.on_success()
        # После 3 успехов — снижение
        assert t.multiplier < before

    def test_two_successes_dont_reduce(self):
        t = AdaptiveThrottle()
        t.on_captcha()
        t.on_success()
        t.on_success()
        assert t.multiplier == 2.0  # без изменений

    def test_success_when_at_baseline_no_change(self):
        t = AdaptiveThrottle()
        t.on_success()
        t.on_success()
        t.on_success()
        assert t.multiplier == 1.0  # не уходит ниже базового

    def test_captcha_resets_success_streak(self):
        t = AdaptiveThrottle()
        t.on_captcha()  # x2
        t.on_success()
        t.on_success()
        # Две успешные подряд — далее капча, ресет стрика
        t.on_captcha()  # x4
        # Теперь ещё два успеха — недостаточно, чтобы снизить
        t.on_success()
        t.on_success()
        assert t.multiplier == 4.0

    def test_pause_returns_positive_int(self):
        t = AdaptiveThrottle()
        pause = t.get_pause(base_ms=2000)
        assert isinstance(pause, int)
        assert pause > 0

    def test_pause_within_jitter_range(self):
        t = AdaptiveThrottle()
        for _ in range(50):
            pause = t.get_pause(base_ms=1000)
            # Базовое 1000, шум ±20% → 800..1200
            assert 700 <= pause <= 1300

    def test_pause_scales_with_multiplier(self):
        t = AdaptiveThrottle()
        t.on_captcha()  # x2
        t.on_captcha()  # x4
        # Среднее значение должно быть около 4000
        avg = sum(t.get_pause(1000) for _ in range(100)) / 100
        assert 3000 < avg < 5000

    def test_captcha_count_tracks(self):
        t = AdaptiveThrottle()
        t.on_captcha()
        t.on_captcha()
        t.on_captcha()
        assert t.captcha_count == 3
