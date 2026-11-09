# -*- coding: utf-8 -*-
"""Item builders and action-aware error mapping shared by native backends.

`make_item` / `unix_to_iso` already live in `agent_reach.runtime.result`; this
module only adapts each platform's payload into that shape.
"""

from __future__ import annotations

import html
import re
from typing import Any, Dict, Optional

from agent_reach.runtime.result import (
    BAD_INPUT,
    NEED_LOGIN,
    NEED_SETUP,
    NOT_FOUND,
    PLATFORM_BLOCKED,
    RATE_LIMITED,
    UPSTREAM_BROKEN,
    ReachError,
    make_item,
    unix_to_iso,
)
from agent_reach.utils.text import scrub_url_credentials

_TAG_RE = re.compile(r"<[^>]+>")

_BILI_LOGIN_HINT = (
    "Bilibili needs a login for this request. Set `bili_cookie` in "
    "~/.agent-reach/config.yaml (or export BILI_COOKIE), or sign in to "
    "bilibili.com in Chrome; agent-reach can also fall back to bili-cli's own login."
)

BILI_NO_KEY_HINT = (
    "Set `summary_api_key` in ~/.agent-reach/config.yaml (or export "
    "SUMMARY_API_KEY); optional: `summary_base_url` and `summary_model`."
)


def _strip_html(value: Any) -> Optional[str]:
    """Search titles include <em> highlight tags; plain text is what agents need."""
    if value is None:
        return None
    text = html.unescape(_TAG_RE.sub("", str(value))).strip()
    return text or None


def video_item(info: Dict[str, Any]) -> dict:
    """One item for `read`, built from the native `get_video_info` payload."""
    bvid = info.get("bvid")
    raw = {
        key: info.get(key)
        for key in (
            "bvid",
            "aid",
            "cid",
            "duration",
            "pubdate",
            "views",
            "stat",
            "page_count",
            "pages",
            "subtitle_allow_submit",
        )
        if info.get(key) is not None
    }
    return make_item(
        title=info.get("title"),
        url=info.get("url") or (f"https://www.bilibili.com/video/{bvid}" if bvid else None),
        author=info.get("owner"),
        published_at=unix_to_iso(info.get("pubdate")),
        text=info.get("desc"),
        raw=raw,
    )


def search_item(row: Dict[str, Any]) -> dict:
    """One item for `search`, built from a WBI search result row."""
    bvid = str(row.get("bvid") or "")
    # Prefer the canonical BV link: `arcurl` often uses the av-number form,
    # which the channel's read target parser does not accept.
    url = (
        f"https://www.bilibili.com/video/{bvid}"
        if bvid
        else row.get("arcurl") or None
    )
    raw = {
        key: row.get(key)
        for key in ("bvid", "aid", "author", "pubdate", "play", "pic", "duration", "type")
        if row.get(key) is not None
    }
    return make_item(
        title=_strip_html(row.get("title")),
        url=url,
        author=_strip_html(row.get("author")),
        published_at=unix_to_iso(row.get("pubdate")),
        text=_strip_html(row.get("description")),
        raw=raw,
    )


def subtitle_item(result: Dict[str, Any], language: Optional[str] = None) -> dict:
    """One item for `subtitle`; `result` is the native `extract_subtitle` dict."""
    part = (result.get("parts") or [{}])[0]
    tracks = part.get("subtitles") or {}
    if language:
        texts = [
            track.get("text")
            for code, track in tracks.items()
            if code == language and track.get("text")
        ]
    else:
        texts = [track.get("text") for track in tracks.values() if track.get("text")]
    bvid = result.get("bvid")
    return make_item(
        title=result.get("title"),
        url=result.get("url") or (f"https://www.bilibili.com/video/{bvid}" if bvid else None),
        author=result.get("owner"),
        published_at=unix_to_iso(result.get("pubdate")),
        text="\n\n".join(texts) or None,
        raw={
            "bvid": bvid,
            "part": part.get("part"),
            "part_name": part.get("part_name"),
            "page_count": result.get("page_count"),
            "languages": sorted(tracks),
            "line_count": sum(int(track.get("line_count") or 0) for track in tracks.values()),
        },
    )


def summary_item(
    result: Dict[str, Any],
    text: str,
    model: str,
    *,
    input_characters: Optional[int] = None,
    truncated: Optional[bool] = None,
) -> dict:
    """One item for `summary`; `text` is the model's final answer."""
    part = (result.get("parts") or [{}])[0]
    bvid = result.get("bvid")
    return make_item(
        title=result.get("title"),
        url=result.get("url") or (f"https://www.bilibili.com/video/{bvid}" if bvid else None),
        author=result.get("owner"),
        published_at=unix_to_iso(result.get("pubdate")),
        text=text,
        raw={
            "bvid": bvid,
            "part": part.get("part"),
            "part_name": part.get("part_name"),
            "model": model,
            "characters": len(text),
            "input_characters": input_characters,
            "truncated": truncated,
        },
    )


def map_bili_error(code: Any, message: Any, action: str = "read") -> ReachError:
    """Map a Bilibili API code / HTTP status to a unified error.

    `action` labels the failing operation in fallback messages. Credential
    distinctions (anonymous 401/403 vs a rejected login) live in the caller,
    which is the only place that knows whether a session was supplied.
    Returning (not raising) keeps the mapping trivially testable; callers
    raise the result.
    """
    detail = (_strip_html(message) or "")[:500]
    try:
        numeric = int(code)
    except (TypeError, ValueError):
        numeric = None

    if numeric in (-404, 404):
        return ReachError(NOT_FOUND, detail or "video not found")
    if numeric in (429, -509, -799):
        return ReachError(RATE_LIMITED, detail or "Bilibili rate limited the request")
    if numeric in (-412, -352, 412):
        # WBI risk-control rejection: let bili-cli take over instead of
        # treating this as an account-level block.
        return ReachError(
            UPSTREAM_BROKEN,
            detail or f"Bilibili rejected the request (code {code})",
        )
    if numeric == -101:
        # No usable local login: non-final so the bili-cli leg can run.
        return ReachError(
            NEED_SETUP,
            detail or "Bilibili needs a login for this request",
            hint=_BILI_LOGIN_HINT,
        )
    if numeric in (401, 403):
        # A credential was supplied and the platform refused it.
        return ReachError(
            NEED_LOGIN,
            detail or "Bilibili rejected the provided login",
            hint=_BILI_LOGIN_HINT,
        )
    if numeric in (-102, -403, -10403):
        return ReachError(PLATFORM_BLOCKED, detail or "Bilibili blocked this account")
    if numeric == -400:
        return ReachError(BAD_INPUT, detail or "Bilibili rejected the request as invalid")
    return ReachError(
        UPSTREAM_BROKEN, detail or f"Bilibili {action} failed (code {code})"
    )


XHS_RISK_CODES = frozenset({"300031", "300035", "2001", "1001", "2002", "4001", "-1"})
XHS_RISK_MESSAGES = (
    "风控",
    "频繁",
    "验证码",
    "限制",
    "暂时无法浏览",
    "操作过于频繁",
    "稍后再试",
    "too many",
    "captcha",
)
_NOT_FOUND_MESSAGES = ("已删除", "不存在", "设私", "私密", "not found", "not exist", "deleted", "private")
_NEED_LOGIN_MESSAGES = ("登录", "login", "log in", "sign in", "unauthorized", "401")
_MAX_MESSAGE = 500
_XHS_QUERY_SECRET_RE = re.compile(r"(?i)\b(xsec_(?:token|source))\s*=\s*[^\s&#\"']+")


def _clean_message(message: object) -> str:
    """Upstream text may echo the note URL, including its xsec_token."""
    text = _XHS_QUERY_SECRET_RE.sub(r"\1=***", str(message))
    return scrub_url_credentials(text).strip()[:_MAX_MESSAGE]


def _payload_fields(payload: dict) -> tuple[str, str]:
    raw_data = payload.get("data")
    data: dict = raw_data if isinstance(raw_data, dict) else {}
    code = payload.get("code")
    if code in (None, "") and data:
        code = data.get("code")
    message = payload.get("msg") or payload.get("message") or data.get("msg") or ""
    return str(code or "").strip(), _clean_message(message)


def _contains(message: str, words: tuple[str, ...]) -> bool:
    lowered = message.casefold()
    return any(word.casefold() in lowered for word in words)


def map_xhs_error(action: str, payload: Any) -> Optional[ReachError]:
    """Return the ReachError for an upstream payload, or None when it succeeded."""
    if not isinstance(payload, dict):
        return ReachError(UPSTREAM_BROKEN, "XiaoHongShu returned an unexpected payload")

    code, message = _payload_fields(payload)

    # detail/comments 300031 is "deleted or private", never risk control
    # (see 改造计划 §6); only search treats the same code as a block.
    if action in {"read", "comments"} and code == "300031":
        return ReachError(NOT_FOUND, message or "note not found or private")

    # A successful payload is trusted even if its message happens to contain a
    # risk/login word (for example "登录成功").
    if payload.get("success") is True:
        return None

    if _contains(message, XHS_RISK_MESSAGES) or code in XHS_RISK_CODES:
        return ReachError(
            PLATFORM_BLOCKED,
            message or f"XiaoHongShu risk control (code {code or 'unknown'})",
        )

    if action in {"read", "comments"} and _contains(message, _NOT_FOUND_MESSAGES):
        return ReachError(NOT_FOUND, message or "note not found")

    if _contains(message, _NEED_LOGIN_MESSAGES):
        return ReachError(NEED_LOGIN, message or "XiaoHongShu rejected the saved login")

    # Missing/false success with no mapped cause is a broken upstream response,
    # not a silent empty success.
    return ReachError(
        UPSTREAM_BROKEN,
        message or f"XiaoHongShu request failed (code {code or 'unknown'})",
    )


def _published_at(value: Any) -> Optional[str]:
    """XHS detail uses millisecond timestamps; ``unix_to_iso`` expects seconds."""
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    if seconds > 10_000_000_000:  # 13-digit milliseconds, not 10-digit seconds
        seconds //= 1000
    return unix_to_iso(seconds)


def note_item(row: Any, *, url: Optional[str] = None) -> dict:
    """One search result or note detail as a unified item."""
    row = dict(row or {}) if isinstance(row, dict) else {}
    return make_item(
        title=row.get("title") or row.get("display_title"),
        url=url or row.get("url"),
        author=row.get("nickname") or row.get("author"),
        published_at=_published_at(row.get("publish_time")),
        text=row.get("desc"),
        raw=row,
    )


def comment_item(row: Any, *, url: Optional[str] = None) -> dict:
    """One top-level comment; loaded replies stay under ``raw['sub']``."""
    row = dict(row or {}) if isinstance(row, dict) else {}
    raw = dict(row)
    raw.update(content_scope="loaded_comments", exhaustive=False)
    return make_item(
        url=url,
        author=row.get("nickname"),
        text=row.get("content"),
        raw=raw,
    )
