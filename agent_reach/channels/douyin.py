# -*- coding: utf-8 -*-
"""Douyin — MediaCrawler subprocess (experimental)."""

import re
from urllib.parse import parse_qs, urlsplit

from agent_reach.native import douyin as native
from agent_reach.runtime.result import BAD_INPUT, ReachError, make_item, unix_to_iso
from agent_reach.utils.url import host_matches

from .base import Action, Channel, Param

_MIN_LIMIT = 1
_MAX_LIMIT = 50
_VIDEO_PATH_RE = re.compile(r"/video/(\d+)")
_SHORT_PATH_RE = re.compile(r"/+([A-Za-z0-9_-]+)/*")


def _limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not _MIN_LIMIT <= value <= _MAX_LIMIT:
        raise ReachError(BAD_INPUT, "limit must be between 1 and 50")
    return value


def _target(value: object) -> str:
    """Normalize to a video id/URL MediaCrawler can parse; reject other Douyin pages."""
    text = str(value).strip()
    if text.isdigit():
        return text
    hint = "target must be a Douyin video link or aweme id"
    if not host_matches(text, "douyin.com", "iesdouyin.com"):
        raise ReachError(BAD_INPUT, hint)
    try:
        parsed = urlsplit(text)
    except ValueError:
        raise ReachError(BAD_INPUT, hint) from None
    host = (parsed.hostname or "").lower().rstrip(".")
    if host == "v.douyin.com" or host.endswith(".v.douyin.com"):
        match = _SHORT_PATH_RE.fullmatch(parsed.path)
        if not match:
            raise ReachError(BAD_INPUT, hint)
        return f"https://v.douyin.com/{match.group(1)}/"
    modal_id = parse_qs(parsed.query).get("modal_id", [""])[0].strip()
    if modal_id.isdigit():
        return f"https://www.douyin.com/video/{modal_id}"
    match = _VIDEO_PATH_RE.search(parsed.path)
    if match:
        return f"https://www.douyin.com/video/{match.group(1)}"
    raise ReachError(BAD_INPUT, hint)


class DouyinChannel(Channel):
    name = "douyin"
    description = "抖音视频、搜索和评论"

    level = "experimental"

    def setup_problems(self):
        return native.setup_problems()

    actions = (
        Action(
            "search",
            "Search Douyin videos",
            (
                Param("query", "what to search for"),
                Param("limit", "max results (1..50)", required=False, type=int, default=10),
            ),
            (("native", "_native_search"),),
            live_test={"query": "小米汽车", "limit": 3},
            timeout=120,
        ),
        Action(
            "read",
            "Read a video by link or aweme id",
            (Param("target", "video link or aweme id"),),
            (("native", "_native_read"),),
            timeout=120,
        ),
        Action(
            "comments",
            "Read top-level comments; not an exhaustive export",
            (
                Param("target", "video link or aweme id"),
                Param("limit", "max top-level comments (1..50)", required=False, type=int, default=20),
            ),
            (("native", "_native_comments"),),
            timeout=180,
        ),
    )

    def can_handle(self, url: str) -> bool:
        return host_matches(url, "douyin.com", "iesdouyin.com")

    def validate_params(self, action: str, params: dict) -> None:
        if action in {"read", "comments"}:
            params["target"] = _target(params["target"])
        if action in {"search", "comments"}:
            _limit(params["limit"])

    # ── unified entry backends ──

    def _native_search(self, *, query: str, limit: int, timeout: float) -> list:
        limit = _limit(limit)
        rows = native.search(query=str(query), limit=limit, timeout=timeout)
        return [self._video_item(row) for row in rows[:limit]]

    def _native_read(self, *, target: str, timeout: float) -> list:
        target = _target(target)
        rows = native.read(target=target, timeout=timeout)
        return [self._video_item(row) for row in rows[:1]]

    def _native_comments(self, *, target: str, limit: int, timeout: float) -> list:
        target = _target(target)
        limit = _limit(limit)
        rows = native.comments(target=target, limit=limit, timeout=timeout)
        return [self._comment_item(row, target) for row in rows[:limit]]

    # ── item mapping ──

    @staticmethod
    def _video_item(row: dict) -> dict:
        row = dict(row)
        aweme_id = str(row.get("aweme_id") or "").strip()
        url = row.get("aweme_url") or (
            f"https://www.douyin.com/video/{aweme_id}" if aweme_id else None
        )
        return make_item(
            title=row.get("title") or row.get("desc"),
            url=url,
            author=row.get("nickname"),
            published_at=unix_to_iso(row.get("create_time")),
            text=row.get("desc") or row.get("title"),
            raw=row,
        )

    @staticmethod
    def _comment_item(row: dict, target: str) -> dict:
        row = dict(row)
        aweme_id = str(row.get("aweme_id") or "").strip()
        raw = dict(row)
        raw["content_scope"] = "top_level_comments"
        raw["exhaustive"] = False
        return make_item(
            title=None,
            url=f"https://www.douyin.com/video/{aweme_id}" if aweme_id else str(target),
            author=row.get("nickname"),
            published_at=unix_to_iso(row.get("create_time")),
            text=row.get("content"),
            raw=raw,
        )
