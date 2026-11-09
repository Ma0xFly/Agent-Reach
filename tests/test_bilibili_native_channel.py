# -*- coding: utf-8 -*-
"""Bilibili channel wiring: native chains, public-API fallback, subtitle params.

bili-cli was removed from this fork: every action is answered by `native` or by
the plain-HTTP `bilibili-api` fallback. These tests pin that, so a future merge
from upstream cannot quietly reintroduce a third-party CLI dependency.
"""

import pytest

from agent_reach.channels import bilibili
from agent_reach.native import bilibili as native
from agent_reach.runtime.dispatch import run_action
from agent_reach.runtime.result import ReachError

BV = "BV1GJ411x7h7"
ALLOWED_BACKENDS = {"native", "bilibili-api"}


def _channel():
    return bilibili.BilibiliChannel()


def _action(name):
    return next(action for action in bilibili.BilibiliChannel.actions if action.name == name)


def test_backend_chains_match_the_plan():
    assert _action("search").backends == (("native", "_native_search"),)
    assert _action("read").backends == (
        ("bilibili-api", "_api_read"),
        ("native", "_native_read"),
    )
    assert _action("hot").backends == (("bilibili-api", "_api_hot"),)
    assert _action("subtitle").backends == (("native", "_native_subtitle"),)
    assert _action("summary").backends == (("native", "_native_summary"),)


def test_no_third_party_cli_backend_remains():
    for action in bilibili.BilibiliChannel.actions:
        for label, method in action.backends:
            assert label in ALLOWED_BACKENDS, f"{action.name} still uses {label}"
            assert callable(getattr(bilibili.BilibiliChannel, method, None))
    assert bilibili.BilibiliChannel.tools == ()
    assert bilibili.BilibiliChannel.session == ""
    assert bilibili.BilibiliChannel.login_argv == ()


def test_subtitle_declares_part_language_and_timestamps():
    params = {p.name: p for p in _action("subtitle").params}
    assert list(params) == ["target", "part", "language", "timestamps"]
    assert params["part"].type is int and params["part"].default == 1
    assert params["language"].default == "ai-zh"
    assert params["timestamps"].type is str and params["timestamps"].default == "true"
    assert "JSON booleans" in params["timestamps"].help


class _Redirect:
    def __init__(self, url):
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def geturl(self):
        return self._url


def test_bvid_follows_only_real_b23_links(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append((request.full_url, timeout))
        return _Redirect(f"https://www.bilibili.com/video/{BV}")

    monkeypatch.setattr(bilibili.urllib.request, "urlopen", fake_urlopen)

    assert bilibili._bvid("https://b23.tv/abc123", 5) == BV
    assert calls == [("https://b23.tv/abc123", 5)]


@pytest.mark.parametrize(
    "target",
    [
        "https://evil.test/b23.tv/abc",
        "evil.test/b23.tv/abc",
        "https://b23.tv@evil.test/b23.tv/abc",
        "https://b23.tv.evil.test/abc",
        "ftp://b23.tv/abc",
        "http://127.0.0.1/b23.tv/abc",
    ],
)
def test_bvid_rejects_b23_lookalikes_before_network(monkeypatch, target):
    monkeypatch.setattr(
        bilibili.urllib.request,
        "urlopen",
        lambda *a, **k: pytest.fail("must not make a request"),
    )

    with pytest.raises(ReachError) as exc:
        bilibili._bvid(target, 5)
    assert exc.value.code == "BAD_INPUT"


def test_summary_has_no_live_test():
    assert _action("summary").live_test is None


def test_search_native_only(monkeypatch):
    monkeypatch.setattr(native, "search", lambda query, limit, timeout: [{"title": query}])
    out = run_action(_channel(), "search", {"query": "Python", "limit": 2}, only_backend="native")
    assert out["ok"] is True
    assert out["backend"] == "native"
    assert out["items"] == [{"title": "Python"}]


def test_search_has_no_fallback_so_a_native_failure_is_reported(monkeypatch):
    def broken(**kwargs):
        raise ReachError("UPSTREAM_BROKEN", "wbi failed")

    monkeypatch.setattr(native, "search", broken)
    out = run_action(_channel(), "search", {"query": "Python", "limit": 3})
    assert out["ok"] is False
    assert out["error"]["code"] == "UPSTREAM_BROKEN"
    assert [a["backend"] for a in out["meta"]["attempts"]] == ["native"]


def test_read_404_stops_before_native(monkeypatch):
    def api_not_found(path, timeout):
        raise ReachError("NOT_FOUND", "video not found")

    monkeypatch.setattr(bilibili, "_bili_api", api_not_found)
    monkeypatch.setattr(native, "read", lambda target, timeout: pytest.fail("native must not run"))

    out = run_action(_channel(), "read", {"target": BV})
    assert out["ok"] is False
    assert out["error"]["code"] == "NOT_FOUND"
    assert [a["backend"] for a in out["meta"]["attempts"]] == ["bilibili-api"]


def test_read_falls_back_from_api_to_native(monkeypatch):
    def api_broken(path, timeout):
        raise ReachError("UPSTREAM_BROKEN", "api unavailable")

    monkeypatch.setattr(bilibili, "_bili_api", api_broken)
    monkeypatch.setattr(native, "read", lambda target, timeout: [{"title": "native"}])
    out = run_action(_channel(), "read", {"target": BV})
    assert out["ok"] is True
    assert out["backend"] == "native"
    assert [a["backend"] for a in out["meta"]["attempts"]] == ["bilibili-api", "native"]


def test_hot_uses_the_public_api_only(monkeypatch):
    monkeypatch.setattr(
        bilibili,
        "_bili_api",
        lambda path, timeout: {"list": [{"bvid": BV, "title": "hot"}]},
    )
    out = run_action(_channel(), "hot", {"limit": 2})
    assert out["ok"] is True
    assert out["backend"] == "bilibili-api"


def test_native_subtitle_receives_part_language_and_timestamps(monkeypatch):
    seen = {}

    def fake_subtitle(*, target, part, language, timestamps, timeout):
        seen.update(target=target, part=part, language=language, timestamps=timestamps)
        return [{"title": "字幕"}]

    monkeypatch.setattr(native, "subtitle", fake_subtitle)
    out = run_action(
        _channel(),
        "subtitle",
        {"target": BV, "part": 2, "language": "ai-en", "timestamps": "false"},
        only_backend="native",
    )
    assert out["ok"] is True and out["backend"] == "native"
    assert seen == {"target": BV, "part": 2, "language": "ai-en", "timestamps": "false"}


def test_subtitle_failure_is_reported_without_a_cli_fallback(monkeypatch):
    monkeypatch.setattr(native, "subtitle", lambda **kwargs: _raise("NEED_LOGIN"))
    out = run_action(
        _channel(), "subtitle", {"target": BV, "part": 1, "language": "ai-zh", "timestamps": "true"}
    )
    assert out["ok"] is False
    assert out["error"]["code"] == "NEED_LOGIN"
    assert [a["backend"] for a in out["meta"]["attempts"]] == ["native"]


def test_summary_runs_native_only(monkeypatch):
    monkeypatch.setattr(native, "summary", lambda **kwargs: [{"title": "总结"}])
    out = run_action(_channel(), "summary", {"target": BV})
    assert out["ok"] is True
    assert out["backend"] == "native"
    assert [a["backend"] for a in out["meta"]["attempts"]] == ["native"]


def test_summary_forwards_part_language_and_model(monkeypatch):
    seen = {}

    def fake_summary(*, target, part, language, model, timeout):
        seen.update(target=target, part=part, language=language, model=model)
        return [{"title": "总结"}]

    monkeypatch.setattr(native, "summary", fake_summary)
    out = run_action(
        _channel(),
        "summary",
        {"target": BV, "part": 3, "language": "zh-Hans", "model": "custom/model"},
        only_backend="native",
    )
    assert out["ok"] is True
    assert seen == {
        "target": BV,
        "part": 3,
        "language": "zh-Hans",
        "model": "custom/model",
    }


def test_setup_problems_does_not_block_on_optional_summary_key(monkeypatch):
    monkeypatch.delenv("SUMMARY_API_KEY", raising=False)
    assert _channel().setup_problems() == []


def _raise(code):
    raise ReachError(code, code.lower())