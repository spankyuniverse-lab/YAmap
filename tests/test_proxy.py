"""Тесты yamap.proxy — ProxyRotator."""

from __future__ import annotations

from pathlib import Path

from yamap.proxy import ProxyRotator


class TestProxyRotatorBasic:
    def test_empty_rotator(self):
        r = ProxyRotator([])
        assert not r.has_proxies
        assert r.next() is None
        assert r.current() is None

    def test_round_robin(self):
        r = ProxyRotator(["a", "b", "c"])
        assert r.next() == "a"
        assert r.next() == "b"
        assert r.next() == "c"
        assert r.next() == "a"  # цикл

    def test_current_returns_last(self):
        r = ProxyRotator(["a", "b"])
        r.next()
        assert r.current() == "a"
        r.next()
        assert r.current() == "b"


class TestProxyRotatorFromFile:
    def test_load_from_file(self, tmp_path):
        p = tmp_path / "proxies.txt"
        p.write_text("http://a:1\nhttp://b:2\n", encoding="utf-8")
        r = ProxyRotator.from_file(str(p))
        assert r.has_proxies
        assert r.next() == "http://a:1"

    def test_skips_comments_and_blanks(self, tmp_path):
        p = tmp_path / "proxies.txt"
        p.write_text("# header\nhttp://a:1\n\n# comment\nhttp://b:2\n", encoding="utf-8")
        r = ProxyRotator.from_file(str(p))
        assert r.next() == "http://a:1"
        assert r.next() == "http://b:2"
        assert r.next() == "http://a:1"  # round-robin past 2

    def test_missing_file_returns_empty(self, tmp_path):
        r = ProxyRotator.from_file(str(tmp_path / "does_not_exist.txt"))
        assert not r.has_proxies


class TestProxyRotatorFailure:
    def test_three_failures_remove_proxy(self):
        r = ProxyRotator(["a", "b", "c"])
        r.mark_failed("a")
        r.mark_failed("a")
        r.mark_failed("a")
        # 'a' удалён из списка
        assert "a" not in r._proxies
        assert "b" in r._proxies

    def test_two_failures_keep_proxy(self):
        r = ProxyRotator(["a", "b"])
        r.mark_failed("a")
        r.mark_failed("a")
        assert "a" in r._proxies


class TestProxyRotatorPlaywrightArg:
    def test_simple_url(self):
        r = ProxyRotator()
        arg = r.to_playwright_arg("http://host:8080")
        assert arg["server"] == "http://host:8080"
        assert "username" not in arg

    def test_with_auth(self):
        r = ProxyRotator()
        arg = r.to_playwright_arg("http://user:pass@host:8080")
        assert arg["username"] == "user"
        assert arg["password"] == "pass"
        # server не содержит креденшелов
        assert "user" not in arg["server"]
        assert "pass" not in arg["server"]
        assert arg["server"] == "http://host:8080"

    def test_socks5(self):
        r = ProxyRotator()
        arg = r.to_playwright_arg("socks5://host:1080")
        assert arg["server"] == "socks5://host:1080"
