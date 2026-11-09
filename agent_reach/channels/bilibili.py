# -*- coding: utf-8 -*-
"""Bilibili — native WBI backend with a public-API fallback.

`native` (agent_reach/native/bilibili.py) serves search/read/subtitles and the
AI `summary` action with no third-party CLI at all. `bilibili-api` is the
unauthenticated public API fallback for read/hot. bili-cli was removed: it
duplicated everything native does while adding an install step and a separate
credential file. yt-dlp was already removed because Bilibili's risk control
412-blocks it.

Login lives in config: set `bilibili_sessdata` (and `bilibili_csrf`) in
~/.agent-reach/config.yaml, or install `rookiepy` so native can reuse the
Chrome cookie jar. Reach never logs in on the user's behalf.
"""

import json
import re
import urllib.request
from urllib.parse import urlsplit

from agent_reach.runtime.result import (
    BAD_INPUT,
    NOT_FOUND,
    UPSTREAM_BROKEN,
    ReachError,
    make_item,
    unix_to_iso,
)
from agent_reach.runtime.run import http_request

from .base import Action, Channel, Param

_BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
_BV_HINT = "target must be a BV id or a bilibili.com / b23.tv video link"
_BILI_LOGIN_HINT = (
    "Bilibili subtitles need a login. Put SESSDATA in ~/.agent-reach/config.yaml "
    "as `bilibili_sessdata` (optional: `bilibili_csrf`), or install `rookiepy` so "
    "Reach can reuse the Chrome cookie jar. Never log in on the user's behalf."
)


def _bvid(target: str, timeout: float) -> str:
    """BV id from a BV id, a video link, or a b23.tv short link."""
    from agent_reach.utils.url import host_matches

    t = str(target).strip()
    m = _BV_RE.search(t)
    if m:
        return m.group(0)
    if "b23.tv/" in t:
        url = t if "://" in t else f"https://{t}"
        try:
            parsed = urlsplit(url)
            host = (parsed.hostname or "").lower().rstrip(".")
            _ = parsed.port  # malformed/out-of-range ports raise here
        except ValueError:
            raise ReachError(BAD_INPUT, _BV_HINT) from None
        # Follow a short link only when the caller named b23.tv itself; a
        # lookalike host or userinfo disguise must never trigger a HEAD.
        if (
            host not in {"b23.tv", "www.b23.tv"}
            or not host_matches(url, "b23.tv")
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ReachError(BAD_INPUT, _BV_HINT)
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            m = _BV_RE.search(resp.geturl())
        if m:
            return m.group(0)
    raise ReachError(BAD_INPUT, _BV_HINT)


def _api_video_item(v: dict, text=None) -> dict:
    return make_item(
        title=v.get("title"),
        url=f"https://www.bilibili.com/video/{v.get('bvid')}" if v.get("bvid") else None,
        author=(v.get("owner") or {}).get("name"),
        published_at=unix_to_iso(v.get("pubdate")),
        text=v.get("desc") if text is None else text,
        raw={k: v.get(k) for k in ("bvid", "aid", "duration", "stat", "tname")},
    )


def _bili_api(path: str, timeout: float) -> dict:
    body = http_request(
        f"https://api.bilibili.com{path}",
        timeout=timeout,
        headers={"User-Agent": _UA, "Referer": "https://www.bilibili.com/"},
    )
    try:
        data = json.loads(body)
    except ValueError:
        raise ReachError(UPSTREAM_BROKEN, "Bilibili returned a verification page") from None
    if data.get("code") == -404:
        raise ReachError(NOT_FOUND, "video not found")
    if data.get("code") != 0:
        raise ReachError(UPSTREAM_BROKEN, f"Bilibili API code {data.get('code')}: {data.get('message')}")
    return data.get("data") or {}


_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


class BilibiliChannel(Channel):
    name = "bilibili"
    description = "B站视频、字幕、搜索和 AI 总结"

    level = "default"
    #: No third-party CLI is installed or required: native covers every action
    #: and bilibili-api is a plain HTTP fallback for read/hot.
    tools = ()
    actions = (
        Action(
            "search",
            "Search videos",
            (
                Param("query", "what to search for"),
                Param("limit", "max results", required=False, type=int, default=5),
            ),
            (("native", "_native_search"),),
            live_test={"query": "Python", "limit": 2},
        ),
        Action(
            "read",
            "Video title, uploader, description and stats",
            (Param("target", "BV id or video link"),),
            (
                ("bilibili-api", "_api_read"),
                ("native", "_native_read"),
            ),
            live_test={"target": "BV1GJ411x7h7"},
        ),
        Action(
            "hot",
            "Trending videos",
            (Param("limit", "max items", required=False, type=int, default=10),),
            (("bilibili-api", "_api_hot"),),
            live_test={"limit": 2},
        ),
        Action(
            "subtitle",
            "Subtitle text; pick a part and language, optionally without timestamps",
            (
                Param("target", "BV id or video link"),
                Param(
                    "part",
                    "1-based part index for multi-P videos (out-of-range uses the last part)",
                    required=False,
                    type=int,
                    default=1,
                ),
                Param(
                    "language",
                    "subtitle language, e.g. ai-zh, ai-en, zh-Hans",
                    required=False,
                    type=str,
                    default="ai-zh",
                ),
                Param(
                    "timestamps",
                    "prepend [HH:MM:SS] timestamps: true or false (string; JSON booleans are rejected)",
                    required=False,
                    type=str,
                    default="true",
                ),
            ),
            (("native", "_native_subtitle"),),
            # This canonical test video has human zh-Hans tracks but no ai-zh one;
            # without a language override the native leg correctly returns NOT_FOUND.
            live_test={"target": "BV1GJ411x7h7", "language": "zh-Hans"},
        ),
        Action(
            "summary",
            "AI summary of a video part's subtitles (needs summary_api_key)",
            (
                Param("target", "BV id or video link"),
                Param(
                    "part",
                    "1-based part index for multi-P videos (out-of-range uses the last part)",
                    required=False,
                    type=int,
                    default=1,
                ),
                Param(
                    "language",
                    "subtitle language to summarize, e.g. ai-zh, ai-en",
                    required=False,
                    type=str,
                    default="ai-zh",
                ),
                Param(
                    "model",
                    "model override; defaults to summary_model",
                    required=False,
                    type=str,
                    default="",
                ),
            ),
            (("native", "_native_summary"),),
            live_test=None,
            timeout=120,
        ),
    )

    def can_handle(self, url: str) -> bool:
        from agent_reach.utils.url import host_matches

        return host_matches(url, "bilibili.com", "b23.tv")

    # ── unified entry backends ──

    def _native_search(self, *, query: str, limit: int, timeout: float) -> list:
        from agent_reach.native import bilibili as native

        return native.search(query=query, limit=limit, timeout=timeout)

    def _native_read(self, *, target: str, timeout: float) -> list:
        from agent_reach.native import bilibili as native

        return native.read(target=target, timeout=timeout)

    def _native_subtitle(
        self,
        *,
        target: str,
        part: int = 1,
        language: str = "ai-zh",
        timestamps: str = "true",
        timeout: float,
    ) -> list:
        from agent_reach.native import bilibili as native

        return native.subtitle(
            target=target,
            part=part,
            language=language,
            timestamps=timestamps,
            timeout=timeout,
        )

    def _native_summary(
        self,
        *,
        target: str,
        part: int = 1,
        language: str = "ai-zh",
        model: str = "",
        timeout: float,
    ) -> list:
        from agent_reach.native import bilibili as native

        return native.summary(
            target=target,
            part=part,
            language=language,
            model=model or None,
            timeout=timeout,
        )

    def _api_read(self, *, target: str, timeout: float) -> list:
        return [_api_video_item(_bili_api(f"/x/web-interface/view?bvid={_bvid(target, timeout)}", timeout))]

    def _api_hot(self, *, limit: int, timeout: float) -> list:
        data = _bili_api(f"/x/web-interface/popular?ps={limit}&pn=1", timeout)
        return [_api_video_item(v) for v in (data.get("list") or [])[:limit]]