# -*- coding: utf-8 -*-
"""Douyin through a bounded MediaCrawler subprocess.

MediaCrawler stays outside this process: every call runs its own interpreter
with an isolated ``--save_data_path`` and reads the JSONL files afterwards.
The package is optional, so a missing vendor checkout is a non-final
``NEED_SETUP``; only a profile the platform rejected becomes ``NEED_LOGIN``.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from agent_reach.config import Config
from agent_reach.runtime.result import (
    NEED_LOGIN,
    NEED_SETUP,
    NOT_FOUND,
    PLATFORM_BLOCKED,
    TIMEOUT,
    UPSTREAM_BROKEN,
    ReachError,
)
from agent_reach.runtime.run import CommandFailed, run_cmd
from agent_reach.utils.text import scrub_url_credentials

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_ROOT = _REPO_ROOT / "vendor" / "MediaCrawler"
_LEGACY_ROOT = Path("~/Projects/MediaCrawler")

_PROFILE = "dy_user_data_dir"
_CDP_PROFILE = "cdp_dy_user_data_dir"
_COOKIE_NAME = "sessionid"
_CHROME_EPOCH_OFFSET = 11644473600  # 1601-01-01 → 1970-01-01, in seconds
#: Avoid Agent Reach's own 9222 browser; MediaCrawler launches its own profile here.
_CDP_DEBUG_PORT = 9223

_BUSY_MARKERS = (
    "processsingleton",
    "singletonlock",
    "singleton lock",
    "profile is already in use",
    "user data directory is already in use",
    "browser is already running",
)
# These are exact failure phrases from MediaCrawler's own logs. Broad words such
# as "验证码", "风控", "请登录" also occur in video titles and comments, so a
# successful run must not be failed by matching them.
_BLOCKED_MARKERS = (
    "验证码中间页",
    "账号也许被风控了",
    "请完成验证码",
    "slider verify failed",
    "安全验证失败",
    "操作过慢",
)
_LOGIN_MARKERS = (
    "login failed please confirm",
    "login qrcode not found",
    "登录失败请确认",
)
_SIGNAL_MARKERS = (
    "error",
    "warning",
    "failed",
    "traceback",
    "[douyinlogin",
    "[cdpbrowsermanager",
    "[browserlauncher",
)
_SKIP_SIGNAL_MARKERS = (
    "[store.douyin",
    "current keyword:",
    "aweme_list:",
)
_SEARCH_FAILURE_RE = re.compile(r"search douyin keyword.*\bfailed\b", re.IGNORECASE)


def _config_get(key: str, default=None):
    try:
        return Config(read_only=True).get(key, default)
    except Exception:  # noqa: BLE001 - a broken config must not hide the setup path
        return default


def _python_in(root: Path) -> Optional[Path]:
    for parts in ((".venv", "bin", "python"), (".venv", "Scripts", "python.exe")):
        candidate = root.joinpath(*parts)
        if candidate.is_file():
            return candidate
    return None


def _root_for_python(interpreter: Path) -> Optional[Path]:
    # Walk up from the interpreter itself: venv layouts vary and .venv/bin/python
    # is usually a symlink, so resolve() must not be used here.
    for ancestor in interpreter.parents:
        if (ancestor / "main.py").is_file():
            return ancestor
    return None


def _setup_hint() -> str:
    return (
        "Set `douyin_python` in ~/.agent-reach/config.yaml to MediaCrawler's Python "
        "interpreter (for example /path/to/MediaCrawler/.venv/bin/python). "
        "For a source checkout the default is vendor/MediaCrawler "
        f"(or ~/Projects/MediaCrawler; resolved default: {_DEFAULT_ROOT})."
    )


def _default_install() -> Optional[tuple[Path, Path]]:
    for root in (_DEFAULT_ROOT, Path(_LEGACY_ROOT).expanduser()):
        interpreter = _python_in(root)
        if interpreter is not None and (root / "main.py").is_file():
            return root, interpreter
    return None


def _install() -> tuple[Path, Path]:
    """Return (MediaCrawler root, its interpreter), or NEED_SETUP with a fixable hint."""
    configured = str(_config_get("douyin_python") or "").strip()
    if configured:
        interpreter = Path(configured).expanduser()
        if not interpreter.is_absolute():
            interpreter = _REPO_ROOT / interpreter
        if not interpreter.is_file():
            raise ReachError(
                NEED_SETUP, f"MediaCrawler interpreter was not found at {interpreter}",
                hint=_setup_hint(),
            )
        root = _root_for_python(interpreter)
        if root is None:
            raise ReachError(
                NEED_SETUP, f"No MediaCrawler main.py was found above {interpreter}",
                hint=_setup_hint(),
            )
        return root, interpreter

    found = _default_install()
    if found is None:
        raise ReachError(
            NEED_SETUP,
            "MediaCrawler is not installed for Douyin",
            hint=_setup_hint(),
        )
    return found


def _profile_has_sessionid(profile: Path) -> bool:
    """Whether a Chrome profile holds a live douyin ``sessionid`` cookie name.

    Cookie values stay encrypted by Chrome; only the row's presence and expiry
    are inspected. An unreadable/locked database counts as no evidence.
    """
    now = int((time.time() + _CHROME_EPOCH_OFFSET) * 1_000_000)
    for name in ("Cookies", "Network/Cookies"):
        database = profile / "Default" / name
        if not database.is_file():
            continue
        try:
            connection = sqlite3.connect(
                database.resolve().as_uri() + "?mode=ro", uri=True, timeout=2
            )
            try:
                row = connection.execute(
                    "SELECT 1 FROM cookies WHERE name = ? "
                    "AND (host_key = ? OR host_key LIKE ?) "
                    "AND (expires_utc = 0 OR expires_utc > ?) LIMIT 1",
                    (_COOKIE_NAME, "douyin.com", "%.douyin.com", now),
                ).fetchone()
            finally:
                connection.close()
        except (sqlite3.Error, OSError, ValueError):
            continue
        if row is not None:
            return True
    return False


def _login_command(root: Path, interpreter: Path) -> str:
    """Standard-mode command: a manual login writes dy_user_data_dir, not CDP's profile."""
    return "cd {} && {}".format(
        shlex.quote(str(root)),
        shlex.join(_launcher(interpreter, "standard") + [
            "--platform", "dy",
            "--type", "search",
            "--keywords", "抖音",
            "--headless", "no",
            "--get_comment", "no",
            "--get_media", "no",
            "--get_sub_comment", "no",
        ]),
    )


def _login_hint(root: Path, interpreter: Path) -> str:
    return (
        "Log in yourself in MediaCrawler's standard-mode browser: "
        f"{_login_command(root, interpreter)}. "
        "Scan the QR code in the browser window; never share the cookie."
    )


def _browser_plan(root: Path, interpreter: Path) -> str:
    """Pick the existing profile: CDP wins when it has a login, else standard mode."""
    browser_data = Path(root) / "browser_data"
    if _profile_has_sessionid(browser_data / _CDP_PROFILE):
        return "cdp"
    if _profile_has_sessionid(browser_data / _PROFILE):
        return "standard"
    raise ReachError(
        NEED_LOGIN,
        "No logged-in Douyin browser profile was found in MediaCrawler",
        hint=_login_hint(root, interpreter),
    )


def _subprocess_env() -> dict[str, str]:
    """Child environment overlay: clear SOCKS proxies, inject only an HTTP(S) one.

    ``run_cmd`` merges this mapping over the parent environment, so a removed
    key would come back. Empty values are what actually clear a proxy.
    """
    env = {"ALL_PROXY": "", "all_proxy": ""}
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        if str(os.environ.get(key) or "").strip().lower().startswith("socks"):
            env[key] = ""
    proxy = str(_config_get("proxy") or "").strip()
    try:
        parsed = urlsplit(proxy)
        valid = parsed.scheme.lower() in {"http", "https"} and bool(parsed.hostname)
    except ValueError:
        valid = False
    if valid:
        for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            env[key] = proxy
        env["NO_PROXY"] = ""
        env["no_proxy"] = ""
    return env


def _launcher(interpreter: Path, mode: str) -> list[str]:
    """Run main.py with stderr folded into stdout so failure signals stay visible."""
    code = "import sys; sys.stderr = sys.stdout"
    if mode == "standard":
        # MediaCrawler's own files stay untouched; the flag is set in the child.
        code += "; import config; config.ENABLE_CDP_MODE = False"
    elif mode == "cdp":
        # Use MediaCrawler's own CDP profile instead of Agent Reach's 9222 browser.
        code += (
            "; import config; config.CDP_CONNECT_EXISTING = False"
            f"; config.CDP_DEBUG_PORT = {_CDP_DEBUG_PORT}"
        )
    code += "; import runpy; runpy.run_path('main.py', run_name='__main__')"
    return [str(interpreter), "-c", code]


def _base_argv(save_path: Path) -> list[str]:
    return [
        "--platform", "dy",
        "--save_data_option", "jsonl",
        "--save_data_path", str(save_path),
        "--headless", "yes",
        "--get_media", "no",
        "--get_sub_comment", "no",
    ]


def _read_jsonl(save_path: Path, kind: str) -> list[dict]:
    """Read every ``<kind>_<date>.jsonl`` file MediaCrawler wrote into save_path."""
    rows: list[dict] = []
    base = Path(save_path) / "douyin" / "jsonl"
    for path in sorted(base.glob(f"{kind}_*.jsonl")):
        try:
            with path.open("r", encoding="utf-8") as stream:
                for line in stream:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict):
                        rows.append(row)
        except OSError:
            continue
    return rows


def _last_line(text: str) -> str:
    return next((line.strip() for line in reversed(text.splitlines()) if line.strip()), "")


def _matching_line(text: str, markers: tuple[str, ...]) -> str:
    for line in text.splitlines():
        lowered = line.lower()
        if any(marker in lowered for marker in markers):
            return line.strip()
    return ""


def _message(line: str, fallback: str) -> str:
    cleaned = scrub_url_credentials(line).strip()[:300]
    return cleaned or fallback


def _map_output(text: str) -> Optional[tuple[str, str]]:
    """Map MediaCrawler's log/exit signals to a unified code."""
    if not text.strip():
        return None
    lowered = text.lower()
    if any(marker in lowered for marker in _BUSY_MARKERS):
        return "BUSY", _message(
            _matching_line(text, _BUSY_MARKERS),
            "MediaCrawler's browser profile is in use by another process",
        )
    if any(marker in lowered for marker in _BLOCKED_MARKERS):
        return PLATFORM_BLOCKED, _message(
            _matching_line(text, _BLOCKED_MARKERS),
            "Douyin returned a verification/captcha challenge",
        )
    if any(marker in lowered for marker in _LOGIN_MARKERS):
        return NEED_LOGIN, _message(
            _matching_line(text, _LOGIN_MARKERS),
            "Douyin reported that the saved login is no longer valid",
        )
    return None


def _hint_for(code: str, root: Path, interpreter: Path) -> Optional[str]:
    if code == NEED_LOGIN:
        return _login_hint(root, interpreter)
    if code == "BUSY":
        return (
            "Close the process using MediaCrawler's browser profile "
            "(browser_data/dy_user_data_dir or browser_data/cdp_dy_user_data_dir: "
            "Chrome, MediaCrawler, or another agent), then retry. "
            "Do not delete or switch profiles."
        )
    if code == PLATFORM_BLOCKED:
        return (
            "Stop automation and complete Douyin's verification manually in the "
            "MediaCrawler browser profile. Do not retry in a loop or switch backends."
        )
    return None


def _timeout_hint(root: Path, interpreter: Path, action: str) -> str:
    hint = (
        "If this is the first run, complete Douyin login manually: "
        f"{_login_command(root, interpreter)}. "
    )
    if action == "search":
        return hint + "Otherwise retry with a smaller --limit."
    return hint + "Otherwise retry later."


def _signal_text(text: str) -> str:
    """Keep crawler log lines only; result content must never be mistaken for a failure."""
    lines = []
    for line in text.splitlines():
        lowered = line.lower()
        if any(marker in lowered for marker in _SKIP_SIGNAL_MARKERS):
            continue
        if any(marker in lowered for marker in _SIGNAL_MARKERS):
            lines.append(line)
    return "\n".join(lines)


def _fetch_failure(text: str) -> bool:
    lowered = text.lower()
    return (
        "datafetcherror" in lowered
        or "get aweme detail error" in lowered
        or "get comments failed" in lowered
        or _SEARCH_FAILURE_RE.search(text) is not None
    )


def _raise_fetch_failure(output: str) -> None:
    """MediaCrawler swallows DataFetchError and exits 0 without JSONL."""
    if not _fetch_failure(output):
        return
    line = next((line for line in output.splitlines() if _fetch_failure(line)), "")
    raise ReachError(
        UPSTREAM_BROKEN,
        _message(line, "MediaCrawler could not fetch the requested Douyin data"),
    )


def _run(
    argv: list[str], *, timeout: float, root: Path, env: dict, python: Path,
    action: str = "",
) -> str:
    """Run MediaCrawler and translate its exit/output into unified codes."""
    try:
        output = run_cmd(
            argv, timeout=timeout, cwd=str(root), env=env, label="MediaCrawler"
        )
    except CommandFailed as exc:
        text = f"{exc.stdout}\n{exc.stderr}"
        mapped = _map_output(text)
        if mapped:
            code, message = mapped
            raise ReachError(code, message, hint=_hint_for(code, root, python)) from None
        raise ReachError(
            UPSTREAM_BROKEN,
            f"MediaCrawler failed: {_message(_last_line(text), 'no error output')}",
        ) from None
    except ReachError as exc:
        if exc.code == TIMEOUT:
            raise ReachError(
                TIMEOUT, exc.message, hint=_timeout_hint(root, python, action)
            ) from None
        if exc.code == NEED_SETUP:
            raise ReachError(NEED_SETUP, exc.message, hint=_setup_hint()) from None
        raise
    except OSError as exc:
        raise ReachError(
            NEED_SETUP, f"MediaCrawler could not start: {exc}", hint=_setup_hint()
        ) from None

    # Exit 0 may still contain result content; only crawler signal lines count.
    mapped = _map_output(_signal_text(output))
    if mapped:
        code, message = mapped
        raise ReachError(code, message, hint=_hint_for(code, root, python))
    return output


def _prepare():
    root, interpreter = _install()
    mode = _browser_plan(root, interpreter)
    return root, interpreter, mode


def search(*, query: str, limit: int, timeout: float) -> list[dict]:
    root, interpreter, mode = _prepare()
    with tempfile.TemporaryDirectory(prefix="agent-reach-douyin-") as temporary:
        save_path = Path(temporary)
        # MediaCrawler fetches whole 10-item pages; round up so limit 11..19 gets two.
        pages = max(10, ((int(limit) + 9) // 10) * 10)
        argv = _launcher(interpreter, mode) + _base_argv(save_path) + [
            "--type", "search",
            "--keywords", str(query),
            "--crawler_max_notes_count", str(pages),
            "--get_comment", "no",
        ]
        output = _run(
            argv,
            timeout=timeout,
            root=root,
            env=_subprocess_env(),
            python=interpreter,
            action="search",
        )
        rows = _read_jsonl(save_path, "search_contents")[: int(limit)]
    if not rows:
        _raise_fetch_failure(output)
    return rows


def read(*, target: str, timeout: float) -> list[dict]:
    root, interpreter, mode = _prepare()
    with tempfile.TemporaryDirectory(prefix="agent-reach-douyin-") as temporary:
        save_path = Path(temporary)
        argv = _launcher(interpreter, mode) + _base_argv(save_path) + [
            "--type", "detail",
            "--specified_id", str(target),
            "--get_comment", "no",
        ]
        output = _run(
            argv,
            timeout=timeout,
            root=root,
            env=_subprocess_env(),
            python=interpreter,
            action="read",
        )
        rows = [
            row
            for row in _read_jsonl(save_path, "detail_contents")
            if str(row.get("aweme_id") or "").strip()
        ][:1]
    if not rows:
        _raise_fetch_failure(output)
        raise ReachError(NOT_FOUND, "Douyin video was not found or has no content")
    return rows


def comments(*, target: str, limit: int, timeout: float) -> list[dict]:
    root, interpreter, mode = _prepare()
    with tempfile.TemporaryDirectory(prefix="agent-reach-douyin-") as temporary:
        save_path = Path(temporary)
        argv = _launcher(interpreter, mode) + _base_argv(save_path) + [
            "--type", "detail",
            "--specified_id", str(target),
            "--get_comment", "yes",
            "--max_comments_count_singlenotes", str(int(limit)),
        ]
        output = _run(
            argv,
            timeout=timeout,
            root=root,
            env=_subprocess_env(),
            python=interpreter,
            action="comments",
        )
        rows = [
            row
            for row in _read_jsonl(save_path, "detail_comments")
            if str(row.get("parent_comment_id", "0")).strip() in {"", "0", "None", "none"}
        ][: int(limit)]
        if not rows:
            contents = [
                row
                for row in _read_jsonl(save_path, "detail_contents")
                if str(row.get("aweme_id") or "").strip()
            ]
            if not contents:
                _raise_fetch_failure(output)
                raise ReachError(
                    NOT_FOUND, "Douyin video was not found or has no content"
                )
    return rows


def setup_problems() -> list[str]:
    """Local-only doctor diagnostics: vendor/interpreter and saved login."""
    try:
        root, interpreter = _install()
    except ReachError as exc:
        return [f"{exc.message}: {exc.hint}" if exc.hint else exc.message]
    try:
        _browser_plan(root, interpreter)
    except ReachError as exc:
        return [f"{exc.message}: {exc.hint}" if exc.hint else exc.message]
    return []
