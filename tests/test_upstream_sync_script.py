# -*- coding: utf-8 -*-
"""The fork sync script stays syntax-valid, non-pushing and merge-aware."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "upstream-sync.sh"


def _run(*args):
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_script_has_a_shebang_exec_bit_and_valid_syntax():
    assert SCRIPT.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash\n")
    assert SCRIPT.stat().st_mode & 0o111

    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_help_documents_the_merge_preserving_rebase():
    result = _run("--help")

    assert result.returncode == 0
    assert "--rebase-merges" in result.stdout
    assert "never pushes" in result.stdout.lower()


def test_script_never_pushes_and_never_uses_json_flags():
    text = SCRIPT.read_text(encoding="utf-8")

    assert "git push" not in text
    assert "--json" not in text
    assert "git rebase --rebase-merges" in text
    assert "pytest -q" in text
    assert "youtube channel" in text
    assert "bilibili search" in text


def test_unknown_argument_is_rejected():
    result = _run("--not-a-real-flag")

    assert result.returncode == 2
    assert "unknown argument" in result.stderr