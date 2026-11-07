# -*- coding: utf-8 -*-
"""Douyin channel: registration, URL routing, validation, item mapping and non-exhaustive comments."""

import pytest

from agent_reach.channels import get_channel
from agent_reach.channels.douyin import DouyinChannel
from agent_reach.runtime.dispatch import run_action

VIDEO = "https://www.douyin.com/video/7525538910311632128"


def test_channel_is_registered_as_experimental_with_three_actions():
    channel = get_channel("douyin")

    assert isinstance(channel, DouyinChannel)
    assert channel.name == "douyin"
    assert channel.level == "experimental"
    assert [action.name for action in channel.actions] == ["search", "read", "comments"]
    assert [action.backends for action in channel.actions] == [
        (("native", "_native_search"),),
        (("native", "_native_read"),),
        (("native", "_native_comments"),),
    ]
    assert [action.timeout for action in channel.actions] == [120, 120, 180]
    assert channel.actions[0].live_test == {"query": "小米汽车", "limit": 3}


@pytest.mark.parametrize(
    "url",
    [
        "https://www.douyin.com/video/7525538910311632128",
        "https://douyin.com/video/1",
        "https://v.douyin.com/abc123/",
        "https://www.iesdouyin.com/share/video/7525538910311632128",
        "https://www.douyin.com/user/MS4wLjABAAAA?modal_id=123",
    ],
)
def test_can_handle_douyin_urls(url):
    assert DouyinChannel().can_handle(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/video/1",
        "https://douyin.com.evil.test/video/1",
        "https://evil.test/?next=https://douyin.com/video/1",
        "https://douyin.com@evil.test/video/1",
        "ftp://douyin.com/video/1",
        "",
    ],
)
def test_can_handle_rejects_lookalikes(url):
    assert DouyinChannel().can_handle(url) is False


@pytest.mark.parametrize(
    "action,params",
    [
        ("search", {"query": "小米汽车", "limit": 0}),
        ("search", {"query": "小米汽车", "limit": 51}),
        ("comments", {"target": VIDEO, "limit": 51}),
        ("comments", {"target": VIDEO, "limit": 0}),
        ("read", {"target": "https://example.com/video/1"}),
        ("read", {"target": "https://douyin.com.evil.test/video/1"}),
        ("read", {"target": "-3"}),
        ("read", {"target": "https://www.douyin.com/user/MS4wLjABAAAA"}),
        ("read", {"target": "https://v.douyin.com/"}),
        ("comments", {"target": "not-a-video", "limit": 5}),
    ],
)
def test_invalid_input_is_rejected_before_the_native_backend(monkeypatch, action, params):
    from agent_reach.native import douyin as native

    monkeypatch.setattr(native, "search", lambda **kwargs: pytest.fail("unexpected subprocess"))
    monkeypatch.setattr(native, "read", lambda **kwargs: pytest.fail("unexpected subprocess"))
    monkeypatch.setattr(native, "comments", lambda **kwargs: pytest.fail("unexpected subprocess"))

    result = run_action(DouyinChannel(), action, params)

    assert result["error"]["code"] == "BAD_INPUT"


@pytest.mark.parametrize(
    "target,expected",
    [
        ("7525538910311632128", "7525538910311632128"),
        (VIDEO, VIDEO),
        ("https://v.douyin.com/abc123/", "https://v.douyin.com/abc123/"),
        ("https://v.douyin.com/abc123", "https://v.douyin.com/abc123/"),
        (
            "https://www.iesdouyin.com/share/video/7525538910311632128",
            VIDEO,
        ),
        (
            "https://www.douyin.com/user/MS4wLjABAAAA?modal_id=7525538910311632128",
            VIDEO,
        ),
    ],
)
def test_valid_targets_are_normalized_before_the_native_backend(monkeypatch, target, expected):
    from agent_reach.native import douyin as native

    seen = {}

    def read(*, target, timeout):
        seen.update(target=target, timeout=timeout)
        return [{"aweme_id": "123", "title": "标题", "desc": "简介"}]

    monkeypatch.setattr(native, "read", read)

    result = run_action(DouyinChannel(), "read", {"target": target}, timeout=120)

    assert result["ok"] is True
    assert result["backend"] == "native"
    assert result["items"][0]["title"] == "标题"
    assert seen["target"] == expected


def test_setup_problems_delegates_to_the_native_backend(monkeypatch):
    from agent_reach.native import douyin as native

    monkeypatch.setattr(native, "setup_problems", lambda: ["install MediaCrawler"])
    assert DouyinChannel().setup_problems() == ["install MediaCrawler"]


def test_video_item_maps_title_url_author_time_and_statistics():
    item = DouyinChannel()._video_item(
        {
            "aweme_id": "7525538910311632128",
            "title": "标题",
            "desc": "视频简介",
            "nickname": "不***师",
            "create_time": 1700000000,
            "liked_count": "133555",
            "aweme_url": "",
        }
    )

    assert item["title"] == "标题"
    assert item["url"] == "https://www.douyin.com/video/7525538910311632128"
    assert item["author"] == "不***师"
    assert item["published_at"] == "2023-11-14T22:13:20Z"
    assert item["text"] == "视频简介"
    assert item["raw"]["liked_count"] == "133555"


def test_search_returns_native_items_with_the_requested_limit(monkeypatch):
    from agent_reach.native import douyin as native

    monkeypatch.setattr(
        native,
        "search",
        lambda *, query, limit, timeout: [{"aweme_id": str(i), "title": f"t{i}"} for i in range(5)],
    )

    result = run_action(
        DouyinChannel(), "search", {"query": "小米汽车", "limit": 2}, timeout=120
    )

    assert result["ok"] is True
    assert result["backend"] == "native"
    assert len(result["items"]) == 2
    assert [item["title"] for item in result["items"]] == ["t0", "t1"]


def test_comments_are_marked_non_exhaustive(monkeypatch):
    from agent_reach.native import douyin as native

    monkeypatch.setattr(
        native,
        "comments",
        lambda *, target, limit, timeout: [
            {
                "comment_id": "1",
                "aweme_id": "7525538910311632128",
                "content": "第一条",
                "nickname": "R***n",
                "create_time": 1700000000,
                "like_count": 81,
                "parent_comment_id": "0",
            }
        ],
    )

    result = run_action(
        DouyinChannel(), "comments", {"target": VIDEO, "limit": 5}, timeout=180
    )

    assert result["ok"] is True
    assert result["backend"] == "native"
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["text"] == "第一条"
    assert item["author"] == "R***n"
    assert item["url"] == "https://www.douyin.com/video/7525538910311632128"
    assert item["raw"]["exhaustive"] is False
    assert item["raw"]["content_scope"] == "top_level_comments"


def test_comments_with_no_rows_are_an_empty_success(monkeypatch):
    from agent_reach.native import douyin as native

    monkeypatch.setattr(native, "comments", lambda *, target, limit, timeout: [])

    result = run_action(
        DouyinChannel(), "comments", {"target": VIDEO, "limit": 5}, timeout=180
    )

    assert result["ok"] is True
    assert result["items"] == []
