# -*- coding: utf-8 -*-
"""YouTube comments and channel actions."""

import json
from pathlib import Path

import pytest

from agent_reach.channels import youtube as yt
from agent_reach.channels.youtube import YouTubeChannel
from agent_reach.runtime.result import ReachError

VIDEO = "jNQXAC9IVRw"


def test_channel_url_normalizes_handles_ids_and_tabs():
    channel_id = "UC" + "a" * 22
    assert yt._channel_url("@YouTube") == "https://www.youtube.com/@YouTube/videos"
    assert yt._channel_url("@YouTube/videos") == "https://www.youtube.com/@YouTube/videos"
    assert yt._channel_url("@YouTube/featured") == "https://www.youtube.com/@YouTube/videos"
    assert yt._channel_url("@YouTube/live") == "https://www.youtube.com/@YouTube/live"
    assert yt._channel_url(channel_id) == f"https://www.youtube.com/channel/{channel_id}/videos"
    assert yt._channel_url("https://www.youtube.com/@YouTube") == "https://www.youtube.com/@YouTube/videos"
    assert yt._channel_url("https://www.youtube.com/@YouTube/shorts") == "https://www.youtube.com/@YouTube/shorts"
    assert (
        yt._channel_url(f"https://m.youtube.com/channel/{channel_id}/videos")
        == f"https://m.youtube.com/channel/{channel_id}/videos"
    )


@pytest.mark.parametrize(
    "target",
    [
        "https://youtu.be/abc",
        "https://www.youtube.com/playlist?list=PL123",
        "https://www.youtube.com/@YouTube/playlists",
        "https://www.youtube.com/channel/UCabc/videos",
        "https://example.com/@YouTube",
        "not-a-channel",
    ],
)
def test_channel_url_rejects_non_channel_pages(target):
    with pytest.raises(ReachError) as exc:
        yt._channel_url(target)
    assert exc.value.code == "BAD_INPUT"


def test_comments_read_info_json_and_mark_exhaustive(monkeypatch):
    seen = {}

    def fake_run(args, timeout, cwd=None):
        seen.update(args=args, timeout=timeout, cwd=cwd)
        Path(cwd, f"{VIDEO}.info.json").write_text(
            json.dumps(
                {
                    "id": VIDEO,
                    "comments": [
                        {
                            "id": "c1",
                            "text": "第一条",
                            "author": "A",
                            "timestamp": 1700000000,
                            "like_count": 3,
                            "parent": "root",
                        },
                        {"id": "c2", "text": "第二条", "author": "B"},
                        {"id": "c3", "text": "第三条", "author": "C"},
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return ""

    monkeypatch.setattr(yt, "_run_ytdlp", fake_run)

    items = YouTubeChannel()._ytdlp_comments(target=VIDEO, limit=2, timeout=120)

    args = seen["args"]
    assert "--skip-download" in args
    assert "--no-playlist" in args
    assert "--write-comments" in args
    assert "--write-info-json" in args
    assert args[args.index("--extractor-args") + 1] == "youtube:max_comments=2"
    assert args[args.index("-o") + 1] == "%(id)s.%(ext)s"
    assert seen["timeout"] == 120
    assert len(items) == 2
    assert items[0]["url"] == f"https://www.youtube.com/watch?v={VIDEO}&lc=c1"
    assert items[0]["author"] == "A"
    assert items[0]["text"] == "第一条"
    assert items[0]["published_at"] == "2023-11-14T22:13:20Z"
    assert items[0]["raw"]["exhaustive"] is False
    assert items[0]["raw"]["content_scope"] == "comments_and_replies"
    assert items[0]["raw"]["like_count"] == 3
    assert items[1]["raw"]["exhaustive"] is False


def test_comments_without_comments_are_empty(monkeypatch):
    monkeypatch.setattr(
        yt,
        "_run_ytdlp",
        lambda args, timeout, cwd=None: (
            Path(cwd, f"{VIDEO}.info.json").write_text(
                json.dumps({"id": VIDEO, "comments": []}), encoding="utf-8"
            )
            or ""
        ),
    )

    assert YouTubeChannel()._ytdlp_comments(target=VIDEO, limit=5, timeout=120) == []


@pytest.mark.parametrize("info", [{"id": VIDEO}, {"id": VIDEO, "comments": None}])
def test_comments_missing_or_none_is_not_found(monkeypatch, info):
    monkeypatch.setattr(
        yt,
        "_run_ytdlp",
        lambda args, timeout, cwd=None: (
            Path(cwd, f"{VIDEO}.info.json").write_text(json.dumps(info), encoding="utf-8") or ""
        ),
    )

    with pytest.raises(ReachError) as exc:
        YouTubeChannel()._ytdlp_comments(target=VIDEO, limit=5, timeout=120)
    assert exc.value.code == "NOT_FOUND"


def test_comments_failure_with_partial_info_is_returned(monkeypatch):
    def fake_run(args, timeout, cwd=None):
        Path(cwd, f"{VIDEO}.info.json").write_text(
            json.dumps({"id": VIDEO, "comments": [{"id": "c1", "text": "partial"}]}),
            encoding="utf-8",
        )
        raise ReachError("UPSTREAM_BROKEN", "incomplete reply thread")

    monkeypatch.setattr(yt, "_run_ytdlp", fake_run)

    items = YouTubeChannel()._ytdlp_comments(target=VIDEO, limit=5, timeout=120)
    assert len(items) == 1
    assert items[0]["raw"]["partial"] is True


def test_comment_link_drops_fragment_and_uses_lc_query():
    assert (
        yt._comment_link(f"https://www.youtube.com/watch?v={VIDEO}#t=1", "c1")
        == f"https://www.youtube.com/watch?v={VIDEO}&lc=c1"
    )
    assert (
        yt._comment_link(f"https://youtu.be/{VIDEO}?t=1", "c1")
        == f"https://www.youtube.com/watch?v={VIDEO}&lc=c1"
    )


def test_comments_missing_info_json_is_upstream_broken(monkeypatch):
    monkeypatch.setattr(yt, "_run_ytdlp", lambda args, timeout, cwd=None: "")

    with pytest.raises(ReachError) as exc:
        YouTubeChannel()._ytdlp_comments(target=VIDEO, limit=5, timeout=120)
    assert exc.value.code == "UPSTREAM_BROKEN"


def test_comments_unreadable_info_json_is_upstream_broken(monkeypatch):
    def fake_run(args, timeout, cwd=None):
        Path(cwd, f"{VIDEO}.info.json").write_text("not json", encoding="utf-8")
        return ""

    monkeypatch.setattr(yt, "_run_ytdlp", fake_run)

    with pytest.raises(ReachError) as exc:
        YouTubeChannel()._ytdlp_comments(target=VIDEO, limit=5, timeout=120)
    assert exc.value.code == "UPSTREAM_BROKEN"


def test_channel_reads_flat_playlist_and_limits(monkeypatch):
    seen = {}

    def fake_run(args, timeout, cwd=None):
        seen.update(args=args, timeout=timeout)
        return json.dumps(
            {
                "entries": [
                    {"id": "v1", "title": "T1", "url": "https://www.youtube.com/watch?v=v1", "channel": "C"},
                    {"id": "v2", "title": "T2"},
                    {"id": "v3", "title": "T3"},
                ]
            }
        )

    monkeypatch.setattr(yt, "_run_ytdlp", fake_run)

    items = YouTubeChannel()._ytdlp_channel(target="@YouTube", limit=2, timeout=120)

    args = seen["args"]
    assert "--flat-playlist" in args
    assert "--dump-single-json" in args
    assert args[args.index("--playlist-items") + 1] == "1:2"
    assert args[-1] == "https://www.youtube.com/@YouTube/videos"
    assert len(items) == 2
    assert items[0]["title"] == "T1"
    assert items[0]["url"] == "https://www.youtube.com/watch?v=v1"
    assert items[1]["url"] == "https://www.youtube.com/watch?v=v2"
    assert "exhaustive" not in items[0]["raw"]
    assert seen["timeout"] == 120


def test_channel_without_entries_is_empty(monkeypatch):
    monkeypatch.setattr(yt, "_run_ytdlp", lambda args, timeout, cwd=None: json.dumps({"entries": []}))

    assert YouTubeChannel()._ytdlp_channel(target="@YouTube", limit=5, timeout=120) == []


@pytest.mark.parametrize("entries", [None, {"bad": "shape"}])
def test_channel_invalid_entries_shape(monkeypatch, entries):
    monkeypatch.setattr(
        yt, "_run_ytdlp", lambda args, timeout, cwd=None: json.dumps({"entries": entries})
    )

    if entries is None:
        assert YouTubeChannel()._ytdlp_channel(target="@YouTube", limit=5, timeout=120) == []
    else:
        with pytest.raises(ReachError) as exc:
            YouTubeChannel()._ytdlp_channel(target="@YouTube", limit=5, timeout=120)
        assert exc.value.code == "UPSTREAM_BROKEN"


def test_channel_unreadable_output_is_upstream_broken(monkeypatch):
    monkeypatch.setattr(yt, "_run_ytdlp", lambda args, timeout, cwd=None: "not json")

    with pytest.raises(ReachError) as exc:
        YouTubeChannel()._ytdlp_channel(target="@YouTube", limit=5, timeout=120)
    assert exc.value.code == "UPSTREAM_BROKEN"


def test_actions_are_declared_with_expected_backends_and_timeouts():
    channel = YouTubeChannel()
    actions = {action.name: action for action in channel.actions}

    assert [action.name for action in channel.actions] == [
        "read",
        "search",
        "subtitle",
        "comments",
        "channel",
        "transcribe",
    ]
    assert actions["comments"].backends == (("yt-dlp", "_ytdlp_comments"),)
    assert actions["channel"].backends == (("yt-dlp", "_ytdlp_channel"),)
    assert actions["comments"].timeout == 120
    assert actions["channel"].timeout == 120
    assert actions["comments"].live_test == {
        "target": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
        "limit": 2,
    }
    assert actions["channel"].live_test == {
        "target": "https://www.youtube.com/@YouTube/videos",
        "limit": 2,
    }


def test_validate_params_rejects_invalid_limits_and_targets():
    channel = YouTubeChannel()

    channel.validate_params("comments", {"target": f"https://www.youtube.com/watch?v={VIDEO}", "limit": 1})
    channel.validate_params("channel", {"target": "@YouTube", "limit": 1})

    for action, params in [
        ("comments", {"target": "https://example.com/video", "limit": 1}),
        ("comments", {"target": "https://www.youtube.com/@YouTube", "limit": 1}),
        ("comments", {"target": "https://www.youtube.com/playlist?list=PL123", "limit": 1}),
        ("comments", {"target": f"https://www.youtube.com/watch?v={VIDEO}", "limit": 101}),
        ("channel", {"target": "https://www.youtube.com/playlist?list=PL123", "limit": 1}),
        ("channel", {"target": "@YouTube", "limit": 51}),
    ]:
        with pytest.raises(ReachError) as exc:
            channel.validate_params(action, params)
        assert exc.value.code == "BAD_INPUT"


def test_check_uses_each_action_timeout(monkeypatch):
    from agent_reach.channels.base import Action, Channel, Param
    from agent_reach.runtime import dispatch, manage

    class FakeChannel(Channel):
        name = "fake"
        description = "fake"
        level = "default"
        actions = (
            Action(
                "search",
                "search",
                (Param("query", "q"),),
                (("fake", "_run"),),
                live_test={"query": "x"},
                timeout=120,
            ),
        )

        def can_handle(self, url: str) -> bool:
            return False

    captured = []

    def fake_run_action(channel, action, params, *, timeout, only_backend=None):
        captured.append(timeout)
        return {
            "ok": True,
            "items": [{"title": "x"}],
            "backend": "fake",
            "meta": {"attempts": [], "elapsed_ms": 0},
            "error": None,
        }

    monkeypatch.setattr(manage, "_channels", lambda: [FakeChannel()])
    monkeypatch.setattr(dispatch, "run_action", fake_run_action)

    manage.run_checks(["fake"])
    manage.run_checks(["fake"], timeout=30)

    assert captured == [120, 30]