# -*- coding: utf-8 -*-
"""The setup report and capability summary stay truthful about the new actions."""

from agent_reach.channels.bilibili import BilibiliChannel
from agent_reach.channels.douyin import DouyinChannel
from agent_reach.channels.youtube import YouTubeChannel
from agent_reach.runtime.onboarding import capability_summary, installation_report


def test_summary_lists_new_actions_and_their_limits():
    summary = capability_summary(
        [YouTubeChannel(), BilibiliChannel(), DouyinChannel()],
        [],
    )
    by_name = {entry["channel"]: entry for entry in summary["channels"]}

    youtube = by_name["youtube"]
    assert {action["action"] for action in youtube["actions"]} >= {"comments", "channel"}
    assert any("not an" in line or "不是完整导出" in line for line in youtube["limits"])

    bilibili = by_name["bilibili"]
    assert "summary" in {action["action"] for action in bilibili["actions"]}
    assert any("summary_api_key" in line for line in bilibili["limits"])

    douyin = by_name["douyin"]
    assert {action["action"] for action in douyin["actions"]} == {"search", "read", "comments"}
    assert any("MediaCrawler" in line for line in douyin["limits"])


def test_installation_report_uses_the_douyin_display_name():
    summary = capability_summary([YouTubeChannel(), DouyinChannel()], [])
    result = {
        "version": "test",
        "capability_summary": summary,
        "default_channels": [
            {"channel": "youtube", "ok": True},
            {"channel": "douyin", "ok": True},
        ],
    }

    report = installation_report(result)
    assert "抖音" in report
    assert "| 抖音 |" in report