# -*- coding: utf-8 -*-
"""Native Douyin backend: MediaCrawler subprocess argv, jsonl reads and failure mapping."""

import contextlib
import json
import sqlite3
import time
from pathlib import Path

import pytest

from agent_reach.runtime.result import PLATFORM_BLOCKED, ReachError
from agent_reach.runtime.run import CommandFailed


@pytest.fixture(autouse=True)
def _no_mediacrawler_lock(monkeypatch):
    """Unit tests must not create lock files under the real state directory."""
    from agent_reach.runtime import browser_binding

    monkeypatch.setattr(
        browser_binding, "execution_lock", lambda profile: contextlib.nullcontext()
    )


def _native():
    from agent_reach.native import douyin

    return douyin


def _chrome_time(offset_seconds: float = 0.0) -> int:
    """Chrome stores timestamps as microseconds since 1601-01-01 UTC."""
    return int((time.time() + 11644473600 + offset_seconds) * 1_000_000)


def _profile(
    root: Path,
    *,
    host: str = ".douyin.com",
    expires: int | None = None,
    network: bool = False,
) -> Path:
    profile = root / "browser_data" / "dy_user_data_dir"
    db = profile / "Default" / ("Network/Cookies" if network else "Cookies")
    db.parent.mkdir(parents=True)
    con = sqlite3.connect(db)
    try:
        con.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, expires_utc INTEGER)")
        con.execute(
            "INSERT INTO cookies VALUES (?, ?, ?)",
            (host, "sessionid", _chrome_time(3600) if expires is None else expires),
        )
        con.commit()
    finally:
        con.close()
    return profile


def _fake_tree(root: Path, *, python: bool = True, main: bool = True, windows: bool = False) -> Path:
    root.mkdir(parents=True)
    if main:
        (root / "main.py").write_text("", encoding="utf-8")
    interpreter = (
        root / ".venv" / "Scripts" / "python.exe"
        if windows
        else root / ".venv" / "bin" / "python"
    )
    if python:
        interpreter.parent.mkdir(parents=True)
        interpreter.write_text("", encoding="utf-8")
    return interpreter


def _fake_install(
    monkeypatch, tmp_path, *, mode: str = "standard", writer=None, output: str = ""
):
    native = _native()
    root = tmp_path / "MediaCrawler"
    interpreter = _fake_tree(root)
    monkeypatch.setattr(native, "_install", lambda: (root, interpreter))
    monkeypatch.setattr(native, "_browser_plan", lambda root, python: mode)
    monkeypatch.setattr(native, "_subprocess_env", lambda: {"ONLY": "child"})
    calls = {}

    def fake_run(argv, *, timeout, root, env, python, action=""):
        calls.update(
            argv=argv, timeout=timeout, root=root, env=env, python=python, action=action
        )
        save_path = Path(argv[argv.index("--save_data_path") + 1])
        calls["save_path"] = save_path
        if writer is not None:
            writer(save_path)
        return output

    monkeypatch.setattr(native, "_run", fake_run)
    return native, calls


def _write_jsonl(save_path: Path, kind: str, rows: list) -> None:
    out = save_path / "douyin" / "jsonl"
    out.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    (out / f"{kind}_2026-11-07.jsonl").write_text(body, encoding="utf-8")


# ── install ──


def test_install_finds_vendored_mediacrawler(monkeypatch, tmp_path):
    native = _native()
    root = tmp_path / "MediaCrawler"
    interpreter = _fake_tree(root)
    monkeypatch.setattr(native, "_DEFAULT_ROOT", root)
    monkeypatch.setattr(native, "_config_get", lambda key, default=None: default)

    assert native._install() == (root, interpreter)


def test_install_finds_windows_venv_layout(monkeypatch, tmp_path):
    native = _native()
    root = tmp_path / "MediaCrawler"
    interpreter = _fake_tree(root, windows=True)
    monkeypatch.setattr(native, "_DEFAULT_ROOT", root)
    monkeypatch.setattr(native, "_config_get", lambda key, default=None: default)

    assert native._install() == (root, interpreter)


def test_install_falls_back_to_legacy_project(monkeypatch, tmp_path):
    native = _native()
    monkeypatch.setattr(native, "_DEFAULT_ROOT", tmp_path / "missing")
    monkeypatch.setattr(native, "_config_get", lambda key, default=None: default)
    legacy = Path.home() / "Projects" / "MediaCrawler"
    interpreter = _fake_tree(legacy)

    assert native._install() == (legacy, interpreter)


def test_configured_interpreter_and_its_root_are_used(monkeypatch, tmp_path):
    native = _native()
    root = tmp_path / "Elsewhere" / "MediaCrawler"
    interpreter = _fake_tree(root)
    monkeypatch.setattr(native, "_DEFAULT_ROOT", tmp_path / "missing")
    monkeypatch.setattr(
        native,
        "_config_get",
        lambda key, default=None: str(interpreter) if key == "douyin_python" else default,
    )

    assert native._install() == (root, interpreter)


def test_configured_interpreter_outside_venv_walks_up_to_main(monkeypatch, tmp_path):
    native = _native()
    root = tmp_path / "MediaCrawler"
    root.mkdir()
    (root / "main.py").write_text("", encoding="utf-8")
    interpreter = root / "python"
    interpreter.write_text("", encoding="utf-8")
    monkeypatch.setattr(native, "_DEFAULT_ROOT", tmp_path / "missing")
    monkeypatch.setattr(
        native,
        "_config_get",
        lambda key, default=None: str(interpreter) if key == "douyin_python" else default,
    )

    assert native._install() == (root, interpreter)


def test_configured_interpreter_without_main_above_is_need_setup(monkeypatch, tmp_path):
    native = _native()
    bare = tmp_path / "bare"
    bare.mkdir()
    interpreter = bare / "python"
    interpreter.write_text("", encoding="utf-8")
    monkeypatch.setattr(native, "_DEFAULT_ROOT", tmp_path / "missing")
    monkeypatch.setattr(
        native,
        "_config_get",
        lambda key, default=None: str(interpreter) if key == "douyin_python" else default,
    )

    with pytest.raises(ReachError) as exc:
        native._install()
    assert exc.value.code == "NEED_SETUP"
    assert "No MediaCrawler main.py" in exc.value.message


def test_configured_symlinked_interpreter_still_infers_its_media_root(monkeypatch, tmp_path):
    native = _native()
    root = tmp_path / "Elsewhere" / "MediaCrawler"
    interpreter = _fake_tree(root)
    shared = tmp_path / "uv-managed-python"
    shared.write_text("", encoding="utf-8")
    interpreter.unlink()
    try:
        interpreter.symlink_to(shared)
    except OSError:  # pragma: no cover - Windows without symlink privilege
        pytest.skip("symlinks are not available")
    monkeypatch.setattr(native, "_DEFAULT_ROOT", tmp_path / "missing")
    monkeypatch.setattr(
        native,
        "_config_get",
        lambda key, default=None: str(interpreter) if key == "douyin_python" else default,
    )

    assert native._install() == (root, interpreter)


def test_missing_vendor_reports_need_setup_with_actionable_hint(monkeypatch, tmp_path):
    native = _native()
    monkeypatch.setattr(native, "_DEFAULT_ROOT", tmp_path / "missing")
    monkeypatch.setattr(native, "_config_get", lambda key, default=None: default)

    with pytest.raises(ReachError) as exc:
        native._install()
    assert exc.value.code == "NEED_SETUP"
    assert exc.value.hint.index("douyin_python") < exc.value.hint.index("vendor")
    assert "vendor" in exc.value.hint


def test_missing_configured_interpreter_reports_need_setup(monkeypatch, tmp_path):
    native = _native()
    missing = tmp_path / "Elsewhere" / ".venv" / "bin" / "python"
    monkeypatch.setattr(native, "_DEFAULT_ROOT", tmp_path / "missing")
    monkeypatch.setattr(
        native,
        "_config_get",
        lambda key, default=None: str(missing) if key == "douyin_python" else default,
    )

    with pytest.raises(ReachError) as exc:
        native._install()
    assert exc.value.code == "NEED_SETUP"
    assert str(missing) in exc.value.message


# ── login profiles ──


def test_profile_sessionid_is_detected(tmp_path):
    native = _native()
    assert native._profile_has_sessionid(_profile(tmp_path)) is True


def test_profile_sessionid_is_detected_in_network_cookies(tmp_path):
    native = _native()
    assert native._profile_has_sessionid(_profile(tmp_path, network=True)) is True


def test_expired_foreign_or_lookalike_sessionid_is_not_a_login(tmp_path):
    native = _native()
    assert native._profile_has_sessionid(_profile(tmp_path / "expired", expires=1)) is False
    assert native._profile_has_sessionid(_profile(tmp_path / "foreign", host=".example.com")) is False
    assert native._profile_has_sessionid(_profile(tmp_path / "lookalike", host="notdouyin.com")) is False


def test_browser_plan_prefers_cdp_only_when_it_has_login(monkeypatch, tmp_path):
    native = _native()
    seen = set()
    monkeypatch.setattr(native, "_profile_has_sessionid", lambda profile: profile.name in seen)
    root = tmp_path / "MediaCrawler"
    interpreter = tmp_path / "python"

    seen = {"cdp_dy_user_data_dir"}
    assert native._browser_plan(root, interpreter) == "cdp"

    seen = {"dy_user_data_dir"}
    assert native._browser_plan(root, interpreter) == "standard"

    seen = set()
    with pytest.raises(ReachError) as exc:
        native._browser_plan(root, interpreter)
    assert exc.value.code == "NEED_LOGIN"
    assert "headless no" in exc.value.hint


def test_no_profile_fails_before_any_subprocess(monkeypatch, tmp_path):
    native = _native()
    root = tmp_path / "MediaCrawler"
    interpreter = _fake_tree(root)
    monkeypatch.setattr(native, "_install", lambda: (root, interpreter))
    monkeypatch.setattr(
        native,
        "_browser_plan",
        lambda root, python: (_ for _ in ()).throw(
            ReachError("NEED_LOGIN", "not logged in", hint="headless no")
        ),
    )
    monkeypatch.setattr(
        native, "run_cmd", lambda *args, **kwargs: pytest.fail("subprocess must not start")
    )

    with pytest.raises(ReachError) as exc:
        native.search(query="小米汽车", limit=3, timeout=120)
    assert exc.value.code == "NEED_LOGIN"


# ── child environment ──


def test_socks_proxy_variables_are_removed(monkeypatch):
    native = _native()
    monkeypatch.setenv("ALL_PROXY", "socks5://127.0.0.1:1080")
    monkeypatch.setenv("all_proxy", "socks5://127.0.0.1:1080")
    monkeypatch.setattr(native, "_config_get", lambda key, default=None: default)

    env = native._subprocess_env()
    socks = {key: value for key, value in env.items() if key.lower() == "all_proxy"}
    assert socks == {"ALL_PROXY": "", "all_proxy": ""}


def test_inherited_socks_http_proxy_is_cleared(monkeypatch):
    native = _native()
    monkeypatch.setenv("HTTP_PROXY", "socks5://127.0.0.1:1080")
    monkeypatch.setenv("http_proxy", "socks5://127.0.0.1:1080")
    monkeypatch.setattr(native, "_config_get", lambda key, default=None: default)

    env = native._subprocess_env()
    assert env["HTTP_PROXY"] == ""
    assert env["http_proxy"] == ""


def test_http_proxy_config_is_injected_and_no_proxy_is_cleared(monkeypatch):
    native = _native()
    proxy = "http://127.0.0.1:7890"
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    monkeypatch.setattr(
        native,
        "_config_get",
        lambda key, default=None: proxy if key == "proxy" else default,
    )

    env = native._subprocess_env()
    assert env["HTTP_PROXY"] == proxy
    assert env["HTTPS_PROXY"] == proxy
    assert env["http_proxy"] == proxy
    assert env["https_proxy"] == proxy
    assert env["NO_PROXY"] == ""
    assert env["no_proxy"] == ""


def test_non_http_proxy_config_is_not_injected(monkeypatch):
    native = _native()
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        native,
        "_config_get",
        lambda key, default=None: "socks5://127.0.0.1:1080" if key == "proxy" else default,
    )

    env = native._subprocess_env()
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        assert key not in env


def test_malformed_proxy_config_is_ignored(monkeypatch):
    native = _native()
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(
        native,
        "_config_get",
        lambda key, default=None: "http://[bad" if key == "proxy" else default,
    )

    env = native._subprocess_env()
    assert env == {"ALL_PROXY": "", "all_proxy": ""}


# ── launcher / jsonl ──


def test_standard_launcher_disables_cdp_inside_the_child():
    native = _native()
    argv = native._launcher(Path("/usr/bin/python3"), "standard")

    assert argv[:2] == ["/usr/bin/python3", "-c"]
    assert "ENABLE_CDP_MODE = False" in argv[2]
    assert "run_name='__main__'" in argv[2]


def test_cdp_launcher_uses_its_own_profile_and_port():
    native = _native()
    argv = native._launcher(Path("/usr/bin/python3"), "cdp")

    assert "ENABLE_CDP_MODE" not in argv[2]
    assert "CDP_CONNECT_EXISTING = False" in argv[2]
    assert "CDP_DEBUG_PORT = 9223" in argv[2]
    assert "run_name='__main__'" in argv[2]


def test_launcher_folds_stderr_before_running_main():
    native = _native()
    code = native._launcher(Path("/usr/bin/python3"), "standard")[2]

    assert code.startswith("import sys; sys.stderr = sys.stdout")
    assert code.index("sys.stderr = sys.stdout") < code.index("runpy.run_path")


def test_login_hint_uses_standard_mode_and_no_comment_fetch():
    native = _native()
    hint = native._login_hint(Path("/mc"), Path("/mc/.venv/bin/python"))

    assert "ENABLE_CDP_MODE = False" in hint
    assert "CDP_CONNECT_EXISTING" not in hint
    assert "--get_comment no" in hint
    assert "headless no" in hint


def test_read_jsonl_skips_bad_lines_and_reads_every_date(tmp_path):
    native = _native()
    out = tmp_path / "douyin" / "jsonl"
    out.mkdir(parents=True)
    (out / "search_contents_2026-11-07.jsonl").write_text(
        '{"a": 1}\nnot json\n\n{"a": 2}\n', encoding="utf-8"
    )
    (out / "search_contents_2026-11-08.jsonl").write_text('{"a": 3}\n', encoding="utf-8")

    assert native._read_jsonl(tmp_path, "search_contents") == [{"a": 1}, {"a": 2}, {"a": 3}]


# ── action argv / output ──


def test_search_uses_private_temp_jsonl_and_truncates(monkeypatch, tmp_path):
    rows = [{"aweme_id": str(i)} for i in range(12)]
    native, calls = _fake_install(
        monkeypatch, tmp_path, writer=lambda save: _write_jsonl(save, "search_contents", rows)
    )

    result = native.search(query="小米汽车", limit=3, timeout=120)

    argv = calls["argv"]
    assert argv[argv.index("--platform") + 1] == "dy"
    assert argv[argv.index("--type") + 1] == "search"
    assert argv[argv.index("--keywords") + 1] == "小米汽车"
    assert argv[argv.index("--save_data_option") + 1] == "jsonl"
    assert argv[argv.index("--crawler_max_notes_count") + 1] == "10"
    assert argv[argv.index("--headless") + 1] == "yes"
    assert argv[argv.index("--get_media") + 1] == "no"
    assert argv[argv.index("--get_sub_comment") + 1] == "no"
    assert argv[argv.index("--get_comment") + 1] == "no"
    assert calls["env"] == {"ONLY": "child"}
    assert calls["action"] == "search"
    assert [row["aweme_id"] for row in result] == ["0", "1", "2"]
    assert not calls["save_path"].exists(), "the temporary save path must be removed"


def test_search_limit_15_requests_two_pages(monkeypatch, tmp_path):
    rows = [{"aweme_id": str(i)} for i in range(20)]
    native, calls = _fake_install(
        monkeypatch, tmp_path, writer=lambda save: _write_jsonl(save, "search_contents", rows)
    )

    result = native.search(query="小米汽车", limit=15, timeout=120)

    assert calls["argv"][calls["argv"].index("--crawler_max_notes_count") + 1] == "20"
    assert len(result) == 15


def test_search_in_cdp_mode_does_not_use_agent_reach_browser(monkeypatch, tmp_path):
    native, calls = _fake_install(
        monkeypatch,
        tmp_path,
        mode="cdp",
        writer=lambda save: _write_jsonl(save, "search_contents", [{"aweme_id": "1"}]),
    )

    native.search(query="小米汽车", limit=3, timeout=120)

    code = calls["argv"][2]
    assert "ENABLE_CDP_MODE" not in code
    assert "CDP_CONNECT_EXISTING = False" in code
    assert "CDP_DEBUG_PORT = 9223" in code


def test_search_without_a_content_file_is_an_empty_success(monkeypatch, tmp_path):
    native, _ = _fake_install(monkeypatch, tmp_path)

    assert native.search(query="没有结果", limit=3, timeout=120) == []


def test_search_fetch_failure_without_output_is_upstream_broken(monkeypatch, tmp_path):
    native, _ = _fake_install(monkeypatch, tmp_path, output="DataFetchError: boom")

    with pytest.raises(ReachError) as exc:
        native.search(query="小米汽车", limit=3, timeout=120)
    assert exc.value.code == "UPSTREAM_BROKEN"


def test_read_uses_specified_id_and_returns_one_row(monkeypatch, tmp_path):
    rows = [{"aweme_id": "123", "title": "first"}, {"aweme_id": "456", "title": "second"}]
    native, calls = _fake_install(
        monkeypatch, tmp_path, writer=lambda save: _write_jsonl(save, "detail_contents", rows)
    )

    result = native.read(target="https://v.douyin.com/abc/", timeout=120)

    argv = calls["argv"]
    assert argv[argv.index("--type") + 1] == "detail"
    assert argv[argv.index("--specified_id") + 1] == "https://v.douyin.com/abc/"
    assert argv[argv.index("--get_comment") + 1] == "no"
    assert calls["action"] == "read"
    assert result == [rows[0]]


def test_read_skips_rows_without_an_aweme_id(monkeypatch, tmp_path):
    native, _ = _fake_install(
        monkeypatch,
        tmp_path,
        writer=lambda save: _write_jsonl(save, "detail_contents", [{"aweme_id": "", "title": "x"}]),
    )

    with pytest.raises(ReachError) as exc:
        native.read(target="123", timeout=120)
    assert exc.value.code == "NOT_FOUND"


def test_read_without_a_content_file_is_not_found(monkeypatch, tmp_path):
    native, _ = _fake_install(monkeypatch, tmp_path)

    with pytest.raises(ReachError) as exc:
        native.read(target="123", timeout=120)
    assert exc.value.code == "NOT_FOUND"


def test_read_with_an_empty_content_file_is_not_found(monkeypatch, tmp_path):
    native, _ = _fake_install(
        monkeypatch, tmp_path, writer=lambda save: _write_jsonl(save, "detail_contents", [])
    )

    with pytest.raises(ReachError) as exc:
        native.read(target="123", timeout=120)
    assert exc.value.code == "NOT_FOUND"


def test_read_fetch_failure_without_output_is_upstream_broken(monkeypatch, tmp_path):
    native, _ = _fake_install(
        monkeypatch, tmp_path, output="[get_aweme_detail] Get aweme detail error: boom"
    )

    with pytest.raises(ReachError) as exc:
        native.read(target="123", timeout=120)
    assert exc.value.code == "UPSTREAM_BROKEN"


def test_comments_keep_top_level_rows_and_honour_limit(monkeypatch, tmp_path):
    rows = [
        {"comment_id": "1", "parent_comment_id": "0"},
        {"comment_id": "2", "parent_comment_id": "99"},
        {"comment_id": "3"},
    ]

    def writer(save):
        _write_jsonl(save, "detail_contents", [{"aweme_id": "123"}])
        _write_jsonl(save, "detail_comments", rows)

    native, calls = _fake_install(monkeypatch, tmp_path, writer=writer)

    result = native.comments(target="123", limit=5, timeout=180)

    argv = calls["argv"]
    assert argv[argv.index("--type") + 1] == "detail"
    assert argv[argv.index("--specified_id") + 1] == "123"
    assert argv[argv.index("--get_comment") + 1] == "yes"
    assert argv[argv.index("--max_comments_count_singlenotes") + 1] == "5"
    assert calls["action"] == "comments"
    assert [row["comment_id"] for row in result] == ["1", "3"]
    assert native.comments(target="123", limit=1, timeout=180) == [rows[0]]


def test_existing_video_with_no_comments_is_an_empty_success(monkeypatch, tmp_path):
    native, _ = _fake_install(
        monkeypatch,
        tmp_path,
        writer=lambda save: _write_jsonl(save, "detail_contents", [{"aweme_id": "123"}]),
    )

    assert native.comments(target="123", limit=5, timeout=180) == []


def test_comments_for_a_nonexistent_video_are_not_found(monkeypatch, tmp_path):
    native, _ = _fake_install(monkeypatch, tmp_path)

    with pytest.raises(ReachError) as exc:
        native.comments(target="123", limit=5, timeout=180)
    assert exc.value.code == "NOT_FOUND"


def test_comments_fetch_failure_without_output_is_upstream_broken(monkeypatch, tmp_path):
    native, _ = _fake_install(monkeypatch, tmp_path, output="get comments failed")

    with pytest.raises(ReachError) as exc:
        native.comments(target="123", limit=5, timeout=180)
    assert exc.value.code == "UPSTREAM_BROKEN"


# ── failure mapping ──


@pytest.mark.parametrize(
    "output,expected",
    [
        ("[DouYinLogin.login_by_qrcode] login failed please confirm", "NEED_LOGIN"),
        ("[DouYinLogin.login_by_qrcode] login qrcode not found please confirm", "NEED_LOGIN"),
        ("ERROR [DouYinLogin.begin] 验证码中间页", "PLATFORM_BLOCKED"),
        ("ERROR [DouYinCrawler.search] search douyin keyword: x failed，账号也许被风控了。",
         "PLATFORM_BLOCKED"),
        ("ERROR [DouYinLogin] slider verify failed ...", "PLATFORM_BLOCKED"),
        ("ERROR:process_singleton_posix.cc(1)] Failed to create ProcessSingleton: "
         "profile is already in use", "BUSY"),
    ],
)
def test_zero_exit_output_with_a_failure_signal_is_mapped(monkeypatch, tmp_path, output, expected):
    native = _native()
    monkeypatch.setattr(native, "run_cmd", lambda *args, **kwargs: output)

    with pytest.raises(ReachError) as exc:
        native._run(["python", "-c", "x"], timeout=5, root=tmp_path, env={}, python=tmp_path / "py")
    assert exc.value.code == expected


def test_run_ignores_exact_markers_in_result_content(monkeypatch, tmp_path):
    native = _native()
    output = (
        "[store.douyin.update_douyin_aweme] douyin aweme id:1, "
        "title: 账号也许被风控了 验证码中间页 login failed please confirm\n"
        "[DouYinCrawler.search] Current keyword: 账号也许被风控了\n"
        "[DouYinCrawler.search] keyword:x, aweme_list:['1', '2']\n"
    )
    monkeypatch.setattr(native, "run_cmd", lambda *args, **kwargs: output)

    assert native._run(
        ["python", "-c", "x"], timeout=5, root=tmp_path, env={}, python=tmp_path / "py"
    ) == output


def test_nonzero_exit_without_a_signal_is_upstream_broken(monkeypatch, tmp_path):
    native = _native()

    def fail(*args, **kwargs):
        raise CommandFailed("UPSTREAM_BROKEN", "exit 1", stderr="Traceback: boom", stdout="")

    monkeypatch.setattr(native, "run_cmd", fail)

    with pytest.raises(ReachError) as exc:
        native._run(["python", "-c", "x"], timeout=5, root=tmp_path, env={}, python=tmp_path / "py")
    assert exc.value.code == "UPSTREAM_BROKEN"


def test_nonzero_exit_with_a_captcha_signal_is_blocked(monkeypatch, tmp_path):
    native = _native()

    def fail(*args, **kwargs):
        raise CommandFailed("UPSTREAM_BROKEN", "exit 1", stderr="请完成验证码", stdout="")

    monkeypatch.setattr(native, "run_cmd", fail)

    with pytest.raises(ReachError) as exc:
        native._run(["python", "-c", "x"], timeout=5, root=tmp_path, env={}, python=tmp_path / "py")
    assert exc.value.code == PLATFORM_BLOCKED


def test_run_reports_busy_when_another_mediacrawler_holds_the_lock(monkeypatch, tmp_path):
    from agent_reach.runtime import browser_binding

    native = _native()

    @contextlib.contextmanager
    def busy(profile):
        raise ReachError("BUSY", "Another Reach command is using this Chrome profile")
        yield

    monkeypatch.setattr(browser_binding, "execution_lock", busy)

    with pytest.raises(ReachError) as exc:
        native._run(["python", "-c", "x"], timeout=5, root=tmp_path, env={}, python=tmp_path / "py")
    assert exc.value.code == "BUSY"
    assert "Douyin MediaCrawler" in exc.value.message


def test_run_passes_cwd_env_and_timeout(monkeypatch, tmp_path):
    native = _native()
    seen = {}

    def fake(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        return ""

    monkeypatch.setattr(native, "run_cmd", fake)

    native._run(
        ["python", "-c", "x"],
        timeout=7,
        root=tmp_path,
        env={"A": "1"},
        python=tmp_path / "py",
        action="search",
    )

    assert seen == {
        "argv": ["python", "-c", "x"],
        "timeout": 7,
        "cwd": str(tmp_path),
        "env": {"A": "1"},
        "label": "MediaCrawler",
    }


def test_timeout_adds_a_first_login_hint_for_search(monkeypatch, tmp_path):
    native = _native()

    def fail(*args, **kwargs):
        raise ReachError("TIMEOUT", "MediaCrawler did not finish")

    monkeypatch.setattr(native, "run_cmd", fail)

    with pytest.raises(ReachError) as exc:
        native._run(
            ["python", "-c", "x"],
            timeout=5,
            root=tmp_path,
            env={},
            python=tmp_path / "py",
            action="search",
        )
    assert exc.value.code == "TIMEOUT"
    assert "headless no" in exc.value.hint
    assert "smaller --limit" in exc.value.hint


def test_timeout_for_read_does_not_mention_a_limit(monkeypatch, tmp_path):
    native = _native()

    def fail(*args, **kwargs):
        raise ReachError("TIMEOUT", "MediaCrawler did not finish")

    monkeypatch.setattr(native, "run_cmd", fail)

    with pytest.raises(ReachError) as exc:
        native._run(
            ["python", "-c", "x"],
            timeout=5,
            root=tmp_path,
            env={},
            python=tmp_path / "py",
            action="read",
        )
    assert "smaller --limit" not in exc.value.hint


def test_needs_setup_from_run_cmd_keeps_the_setup_hint(monkeypatch, tmp_path):
    native = _native()

    def fail(*args, **kwargs):
        raise ReachError("NEED_SETUP", "MediaCrawler is not installed")

    monkeypatch.setattr(native, "run_cmd", fail)

    with pytest.raises(ReachError) as exc:
        native._run(["python", "-c", "x"], timeout=5, root=tmp_path, env={}, python=tmp_path / "py")
    assert exc.value.code == "NEED_SETUP"
    assert "douyin_python" in exc.value.hint


def test_oserror_starting_mediacrawler_is_need_setup(monkeypatch, tmp_path):
    native = _native()

    def fail(*args, **kwargs):
        raise OSError("exec format error")

    monkeypatch.setattr(native, "run_cmd", fail)

    with pytest.raises(ReachError) as exc:
        native._run(["python", "-c", "x"], timeout=5, root=tmp_path, env={}, python=tmp_path / "py")
    assert exc.value.code == "NEED_SETUP"
    assert "douyin_python" in exc.value.hint


def test_busy_and_blocked_hints_name_mediacrawler(tmp_path):
    native = _native()
    assert "Close the process" in native._hint_for("BUSY", tmp_path, tmp_path / "py")
    assert "verification" in native._hint_for(
        PLATFORM_BLOCKED, tmp_path, tmp_path / "py"
    )


# ── doctor ──


def test_setup_problems_report_missing_vendor(monkeypatch, tmp_path):
    native = _native()
    monkeypatch.setattr(native, "_DEFAULT_ROOT", tmp_path / "missing")
    monkeypatch.setattr(native, "_config_get", lambda key, default=None: default)

    problems = native.setup_problems()
    assert problems
    assert "douyin_python" in problems[0]


def test_setup_problems_report_missing_login(monkeypatch, tmp_path):
    native = _native()
    root = tmp_path / "MediaCrawler"
    interpreter = _fake_tree(root)
    monkeypatch.setattr(native, "_install", lambda: (root, interpreter))
    monkeypatch.setattr(native, "_profile_has_sessionid", lambda profile: False)
    monkeypatch.setattr(native, "_dependency_problems", lambda root, interpreter: [])

    problems = native.setup_problems()
    assert problems
    assert "headless no" in problems[0]


def test_setup_problems_report_incomplete_dependencies(monkeypatch, tmp_path):
    native = _native()
    root = tmp_path / "MediaCrawler"
    interpreter = _fake_tree(root)
    monkeypatch.setattr(native, "_install", lambda: (root, interpreter))
    monkeypatch.setattr(native, "_profile_has_sessionid", lambda profile: True)

    def fail(*args, **kwargs):
        raise CommandFailed(
            "UPSTREAM_BROKEN",
            "exit 1",
            stderr="ModuleNotFoundError: No module named 'playwright'",
        )

    monkeypatch.setattr(native, "run_cmd", fail)

    problems = native.setup_problems()
    assert problems
    assert "requirements.txt" in problems[0]
    assert "playwright" in problems[0]