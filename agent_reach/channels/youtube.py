# -*- coding: utf-8 -*-
"""YouTube — info, search, subtitles, comments, channel lists (yt-dlp) and Whisper transcription."""

import html
import json
import os
import re
import shutil
import sys
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from agent_reach.runtime.result import (
    BAD_INPUT,
    NOT_FOUND,
    PLATFORM_BLOCKED,
    RATE_LIMITED,
    UPSTREAM_BROKEN,
    ReachError,
    make_item,
    unix_to_iso,
)
from agent_reach.runtime.run import CommandFailed, run_cmd
from agent_reach.utils.paths import private_temporary_directory

from .base import Action, Channel, Param

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_VIDEO_PATH_RE = re.compile(r"^/(?:shorts|embed|live)/([A-Za-z0-9_-]{11})/?$")
_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_CHANNEL_PATH_RE = re.compile(
    r"^/(?P<kind>@[^/]+|channel/[^/]+|c/[^/]+|user/[^/]+)"
    r"(?:/(?P<tab>videos|shorts|streams|live|featured))?/?$"
)
_VTT_TIME_RE = re.compile(r"^\d{2}:\d{2}(:\d{2})?\.\d{3} --> ")
_TAG_RE = re.compile(r"<[^>]+>")
_RAW_KEYS = ("id", "duration", "view_count", "like_count", "channel_id", "tags", "categories")
_COMMENT_RAW_KEYS = (
    "id",
    "parent",
    "like_count",
    "reply_count",
    "is_pinned",
    "author_is_uploader",
    "author_id",
    "timestamp",
)


def _ytdlp_argv(*args: str) -> list:
    """Run the yt-dlp installed with Agent Reach (one of its dependencies)."""
    runtime = []
    if shutil.which("deno") is None and shutil.which("node"):
        runtime = ["--js-runtimes", "node"]
    return [sys.executable, "-m", "yt_dlp", "--no-warnings", *runtime, *args]


def _video_url(target: str) -> str:
    """Normalize a single-video target; reject channels, playlists and other pages."""
    from agent_reach.utils.url import host_matches

    hint = "target must be a YouTube video link or 11-character video id"
    t = str(target).strip()
    if _VIDEO_ID_RE.fullmatch(t):
        return f"https://www.youtube.com/watch?v={t}"
    candidate = t if "://" in t else f"https://{t}"
    if not host_matches(candidate, "youtube.com", "youtu.be"):
        raise ReachError(BAD_INPUT, hint)
    parsed = urlsplit(candidate)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host == "youtu.be" or host.endswith(".youtu.be"):
        video_id = parsed.path.strip("/")
        if _VIDEO_ID_RE.fullmatch(video_id):
            return f"https://www.youtube.com/watch?v={video_id}"
        raise ReachError(BAD_INPUT, hint)
    if parsed.path.rstrip("/") == "/watch":
        video_id = parse_qs(parsed.query).get("v", [""])[0]
        if _VIDEO_ID_RE.fullmatch(video_id):
            return f"https://www.youtube.com/watch?v={video_id}"
        raise ReachError(BAD_INPUT, hint)
    match = _VIDEO_PATH_RE.fullmatch(parsed.path)
    if match:
        return f"https://www.youtube.com/watch?v={match.group(1)}"
    raise ReachError(BAD_INPUT, hint)


def _channel_url(target: str) -> str:
    """Normalize a channel page to a video tab; reject non-channel pages."""
    from agent_reach.utils.url import host_matches

    hint = "target must be a YouTube channel URL, @handle or UC channel id"
    t = str(target).strip()
    if t.startswith("@"):
        return _channel_url(f"https://www.youtube.com/{t}")
    if _CHANNEL_ID_RE.fullmatch(t):
        return f"https://www.youtube.com/channel/{t}/videos"
    candidate = t if "://" in t else f"https://{t}"
    if not host_matches(candidate, "youtube.com"):
        raise ReachError(BAD_INPUT, hint)
    parsed = urlsplit(candidate)
    path = parsed.path.rstrip("/") or "/"
    match = _CHANNEL_PATH_RE.fullmatch(path)
    if not match:
        raise ReachError(BAD_INPUT, hint)
    kind = match.group("kind")
    if kind.startswith("channel/"):
        channel_id = kind.split("/", 1)[1]
        if not _CHANNEL_ID_RE.fullmatch(channel_id):
            raise ReachError(BAD_INPUT, hint)
    tab = match.group("tab") or "videos"
    if tab == "featured":
        tab = "videos"
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{kind}/{tab}", "", ""))


_BOT_GATE = ("sign in to confirm", "not a bot")


def _cookies_browser() -> str:
    """Browser whose cookies yt-dlp may borrow when YouTube demands a login.

    Set ``yt_cookies_browser`` in ~/.agent-reach/config.yaml to override; the
    default is chrome, because anonymous egress IPs are bot-gated by YouTube
    and a logged-in cookie jar is the only reliable fix (verified live).
    """
    from agent_reach.config import Config

    value = str(Config().get("yt_cookies_browser") or "chrome").strip().lower()
    return "" if value in {"", "none", "off", "no", "false"} else value


def _run_ytdlp(args: list, timeout: float, cwd=None, *, retry: bool = True) -> str:
    try:
        return run_cmd(_ytdlp_argv(*args), timeout=timeout, cwd=cwd, label="yt-dlp")
    except CommandFailed as exc:
        err = exc.stderr.lower()
        if retry and any(s in err for s in _BOT_GATE) and "--cookies-from-browser" not in args:
            browser = _cookies_browser()
            if browser:
                try:
                    return _run_ytdlp([*args, "--cookies-from-browser", browser],
                                      timeout, cwd=cwd, retry=False)
                except CommandFailed as retry_exc:
                    exc = retry_exc
                    err = retry_exc.stderr.lower()
        if any(s in err for s in ("video unavailable", "private video", "has been removed", "does not exist")):
            raise ReachError(NOT_FOUND, exc.message) from None
        if any(s in err for s in _BOT_GATE):
            raise ReachError(
                PLATFORM_BLOCKED,
                exc.message,
                hint=("YouTube bot-gated this egress IP. Set `yt_cookies_browser` in "
                      "~/.agent-reach/config.yaml to a browser you are logged into "
                      "(needs secretstorage on Linux), or route through another proxy node."),
            ) from None
        if "429" in err or "too many requests" in err:
            raise ReachError(RATE_LIMITED, exc.message) from None
        raise


def _iso_date(yyyymmdd) -> str:
    s = str(yyyymmdd or "")
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else ""


def _flat_video_item(info: dict) -> dict:
    """One item from a yt-dlp flat playlist/search entry."""
    video_id = info.get("id")
    return make_item(
        title=info.get("title"),
        url=info.get("url") or (f"https://www.youtube.com/watch?v={video_id}" if video_id else None),
        author=info.get("channel") or info.get("uploader"),
        text=info.get("description"),
        raw={key: info.get(key) for key in _RAW_KEYS},
    )


def _comment_link(video_url: str, comment_id) -> str:
    """Canonical watch URL plus ``lc``; never put ``lc`` in a fragment."""
    parsed = urlsplit(video_url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if host == "youtu.be" or host.endswith(".youtu.be"):
        video_id = parsed.path.strip("/")
    else:
        video_id = parse_qs(parsed.query).get("v", [""])[0]
    if not _VIDEO_ID_RE.fullmatch(video_id):
        return video_url
    query = {"v": video_id}
    if comment_id:
        query["lc"] = str(comment_id)
    return urlunsplit(("https", "www.youtube.com", "/watch", urlencode(query), ""))


def vtt_to_text(vtt: str) -> str:
    """Plain text from WebVTT: drop headers, timings, tags; collapse auto-caption repeats."""
    out: list[str] = []
    for line in vtt.splitlines():
        s = line.strip()
        if (
            not s
            or s == "WEBVTT"
            or s.startswith(("Kind:", "Language:", "NOTE", "STYLE"))
            or _VTT_TIME_RE.match(s)
            or s.isdigit()
        ):
            continue
        s = html.unescape(_TAG_RE.sub("", s)).strip()
        if s and (not out or out[-1] != s):
            out.append(s)
    return "\n".join(out)


class YouTubeChannel(Channel):
    name = "youtube"
    description = "YouTube 视频、字幕、评论和频道列表"

    level = "default"
    #: transcribe re-encodes and chunks audio
    prereqs = ("ffmpeg",)
    actions = (
        Action(
            "read",
            "Video title, channel, date and description",
            (Param("target", "video link or id"),),
            (("yt-dlp", "_ytdlp_read"),),
            live_test={"target": "https://www.youtube.com/watch?v=jNQXAC9IVRw"},
        ),
        Action(
            "search",
            "Search videos",
            (
                Param("query", "what to search for"),
                Param("limit", "max results", required=False, type=int, default=5),
            ),
            (("yt-dlp", "_ytdlp_search"),),
            live_test={"query": "python tutorial", "limit": 2},
        ),
        Action(
            "subtitle",
            "Full subtitle text of a video (manual or auto captions)",
            (
                Param("target", "video link or id"),
                Param("lang", "preferred languages, comma separated", required=False, default="zh-Hans,zh,en,en-orig"),
            ),
            (("yt-dlp", "_ytdlp_subtitle"),),
            live_test={"target": "https://www.youtube.com/watch?v=jNQXAC9IVRw"},
        ),
        Action(
            "comments",
            "Read comments and loaded replies from a video; not an exhaustive export",
            (
                Param("target", "video link or id"),
                Param("limit", "max comments (1..100)", required=False, type=int, default=20),
            ),
            (("yt-dlp", "_ytdlp_comments"),),
            live_test={"target": "https://www.youtube.com/watch?v=jNQXAC9IVRw", "limit": 2},
            timeout=120,
        ),
        Action(
            "channel",
            "List recent videos from a channel (up to limit)",
            (
                Param("target", "channel URL (videos/shorts/streams/live), @handle or UC channel id"),
                Param("limit", "max videos (1..50)", required=False, type=int, default=10),
            ),
            (("yt-dlp", "_ytdlp_channel"),),
            live_test={"target": "https://www.youtube.com/@YouTube/videos", "limit": 2},
            timeout=120,
        ),
        Action(
            "transcribe",
            "Speech-to-text of a video's audio (uses Groq/OpenAI Whisper quota)",
            (Param("target", "video link or id"),),
            (("whisper", "_whisper_transcribe"),),
            timeout=1800,
        ),
    )

    def can_handle(self, url: str) -> bool:
        from agent_reach.utils.url import host_matches

        return host_matches(url, "youtube.com", "youtu.be")

    def validate_params(self, action: str, params: dict) -> None:
        if action == "comments":
            _video_url(params["target"])
            if not 1 <= params["limit"] <= 100:
                raise ReachError(BAD_INPUT, "limit must be between 1 and 100 comments")
        elif action == "channel":
            _channel_url(params["target"])
            if not 1 <= params["limit"] <= 50:
                raise ReachError(BAD_INPUT, "limit must be between 1 and 50 videos")

    # ── unified entry backends ──

    def _ytdlp_read(self, *, target: str, timeout: float) -> list:
        out = _run_ytdlp(["--dump-json", "--skip-download", _video_url(target)], timeout)
        info = json.loads(out.splitlines()[-1])
        return [
            make_item(
                title=info.get("title"),
                url=info.get("webpage_url"),
                author=info.get("uploader") or info.get("channel"),
                published_at=_iso_date(info.get("upload_date")),
                text=info.get("description"),
                raw={k: info.get(k) for k in _RAW_KEYS},
            )
        ]

    def _ytdlp_search(self, *, query: str, limit: int, timeout: float) -> list:
        out = _run_ytdlp(["--flat-playlist", "--dump-json", f"ytsearch{limit}:{query}"], timeout)
        items = []
        for line in out.splitlines():
            if not line.strip():
                continue
            info = json.loads(line)
            if isinstance(info, dict):
                items.append(_flat_video_item(info))
        return items

    def _ytdlp_subtitle(self, *, target: str, lang: str, timeout: float) -> list:
        url = _video_url(target)
        wanted = [x.strip() for x in lang.split(",") if x.strip()]
        with private_temporary_directory(prefix="agent-reach-subs-") as tmp:
            out = _run_ytdlp(
                [
                    "--skip-download", "--no-simulate",
                    "--write-subs", "--write-auto-subs",
                    "--sub-langs", ",".join(wanted), "--sub-format", "vtt",
                    "--print", "%(title)s",
                    "-o", "%(id)s.%(ext)s",
                    url,
                ],
                timeout,
                cwd=tmp,
            )
            files = [f for f in os.listdir(tmp) if f.endswith(".vtt")]
            if not files:
                raise ReachError(NOT_FOUND, f"no subtitles in {', '.join(wanted)} for this video")

            def rank(name: str) -> int:
                code = name.rsplit(".", 2)[-2] if name.count(".") >= 2 else ""
                return wanted.index(code) if code in wanted else len(wanted)

            best = min(files, key=rank)
            with open(os.path.join(tmp, best), encoding="utf-8", errors="replace") as fh:
                text = vtt_to_text(fh.read())
        title = next((ln for ln in out.splitlines() if ln.strip()), None)
        code = best.rsplit(".", 2)[-2] if best.count(".") >= 2 else None
        return [make_item(title=title, url=url, text=text, raw={"lang": code})]

    def _ytdlp_comments(self, *, target: str, limit: int, timeout: float) -> list:
        url = _video_url(target)
        failure = None
        with private_temporary_directory(prefix="agent-reach-comments-") as tmp:
            try:
                _run_ytdlp(
                    [
                        "--skip-download",
                        "--no-playlist",
                        "--write-comments",
                        "--write-info-json",
                        "--extractor-args", f"youtube:max_comments={limit}",
                        "-o", "%(id)s.%(ext)s",
                        url,
                    ],
                    timeout,
                    cwd=tmp,
                )
            except ReachError as exc:
                # An incomplete reply thread still leaves a usable partial info file.
                failure = exc
            video_id = parse_qs(urlsplit(url).query).get("v", [""])[0]
            expected = f"{video_id}.info.json"
            files = sorted(name for name in os.listdir(tmp) if name.endswith(".info.json"))
            if expected in files:
                files = [expected] + [name for name in files if name != expected]
            if not files:
                if failure is not None:
                    raise failure
                raise ReachError(UPSTREAM_BROKEN, "yt-dlp wrote no info JSON for this video")
            try:
                with open(os.path.join(tmp, files[0]), encoding="utf-8") as stream:
                    info = json.load(stream)
            except (OSError, ValueError) as exc:
                if failure is not None:
                    raise failure
                raise ReachError(UPSTREAM_BROKEN, f"yt-dlp info JSON is unreadable: {exc}") from None

        comments = info.get("comments") if isinstance(info, dict) else None
        if comments is None:
            if failure is not None:
                raise failure
            raise ReachError(
                NOT_FOUND,
                "YouTube comments are disabled or unavailable for this video",
            )
        if not isinstance(comments, list):
            raise ReachError(UPSTREAM_BROKEN, "yt-dlp returned an invalid comments field")
        items = []
        for comment in comments[:limit]:
            if not isinstance(comment, dict):
                continue
            raw = {key: comment.get(key) for key in _COMMENT_RAW_KEYS if comment.get(key) is not None}
            raw["content_scope"] = "comments_and_replies"
            raw["exhaustive"] = False
            if failure is not None:
                raw["partial"] = True
            items.append(
                make_item(
                    url=_comment_link(url, comment.get("id")),
                    author=comment.get("author"),
                    published_at=unix_to_iso(comment.get("timestamp")),
                    text=comment.get("text"),
                    raw=raw,
                )
            )
        if failure is not None and not items:
            raise failure
        return items

    def _ytdlp_channel(self, *, target: str, limit: int, timeout: float) -> list:
        url = _channel_url(target)
        out = _run_ytdlp(
            ["--flat-playlist", "--dump-single-json", "--playlist-items", f"1:{limit}", url],
            timeout,
        )
        try:
            payload = json.loads(out)
        except ValueError:
            raise ReachError(UPSTREAM_BROKEN, "yt-dlp channel output is not JSON") from None
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if entries is None:
            return []
        if not isinstance(entries, list):
            raise ReachError(UPSTREAM_BROKEN, "yt-dlp returned an invalid channel entries field")
        return [_flat_video_item(info) for info in entries[:limit] if isinstance(info, dict)]

    def _whisper_transcribe(self, *, target: str, timeout: float) -> list:
        from agent_reach.transcribe import NoProviderConfigured, TranscribeError
        from agent_reach.transcribe import transcribe as _transcribe

        url = _video_url(target)
        try:
            text = _transcribe(url)
        except NoProviderConfigured:
            raise ReachError(
                "NEED_SETUP", "no Whisper API key configured",
                hint="Ask the user for a free key from https://console.groq.com, then run `agent-reach configure groq-key`.",
            ) from None
        except TranscribeError as exc:
            raise ReachError("UPSTREAM_BROKEN", str(exc)[:300]) from None
        return [make_item(url=url, text=text)]
