# -*- coding: utf-8 -*-
"""The channel scaffold produces valid, contract-shaped Python without network."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("new_channel_script", ROOT / "scripts" / "new_channel.py")
assert _SPEC and _SPEC.loader
new_channel = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(new_channel)


@pytest.mark.parametrize("name", ["mysite", "my_site2", "a"])
def test_name_accepts_lowercase_identifiers(name):
    assert new_channel._validate_name(name) == name


@pytest.mark.parametrize("name", ["MySite", "1site", "my-site", "base", "class", ""])
def test_name_rejects_invalid_or_reserved_values(name):
    with pytest.raises(ValueError):
        new_channel._validate_name(name)


@pytest.mark.parametrize("host", ["mysite.com", "www.mysite.com", "api.mysite.co.uk"])
def test_host_accepts_domains(host):
    assert new_channel._validate_host(host) == host


@pytest.mark.parametrize("host", ["mysite", "https://mysite.com", "my_site.com", "bad-.com", ""])
def test_host_rejects_non_domains(host):
    with pytest.raises(ValueError):
        new_channel._validate_host(host)


def test_class_name_is_derived_from_the_channel_name():
    assert new_channel._class_name("mysite") == "MysiteChannel"
    assert new_channel._class_name("my_site") == "MySiteChannel"


def test_rendered_channel_and_test_compile():
    channel = new_channel.render_channel("mysite", "MysiteChannel", "mysite.com", "My site search")
    test = new_channel.render_test("mysite", "MysiteChannel", "mysite.com")

    compile(channel, "<generated channel>", "exec")
    compile(test, "<generated test>", "exec")
    assert "class MysiteChannel(Channel):" in channel
    assert "name = 'mysite'" in channel
    assert "_HOST = 'mysite.com'" in channel
    assert "description = 'My site search'" in channel
    assert "from agent_reach.channels import mysite as module" in test
    assert "https://mysite.com/item/1" in test
    assert new_channel._MARKER in channel
    assert new_channel._MARKER in test


def test_force_only_accepts_files_created_by_the_script(tmp_path):
    generated = tmp_path / "generated.py"
    generated.write_text(
        new_channel.render_channel("mysite", "MysiteChannel", "mysite.com", "My site search"),
        encoding="utf-8",
    )
    foreign = tmp_path / "foreign.py"
    foreign.write_text("class RealChannel:\n    pass\n", encoding="utf-8")

    assert new_channel._is_generated(generated) is True
    assert new_channel._is_generated(foreign) is False


def test_rendered_channel_locks_the_origin():
    channel = new_channel.render_channel("mysite", "MysiteChannel", "mysite.com", "My site search")
    namespace = {}
    # The excerpt is imported by the generated module; this only checks the
    # validation function's logic without depending on the package layout.
    snippet = channel.split("def _validate_api_url", 1)[1].split("def _get_json", 1)[0]
    exec('import urllib.parse\n_HOST = "mysite.com"\n\ndef _validate_api_url' + snippet, namespace)
    validate = namespace["_validate_api_url"]

    validate("https://mysite.com/search?q=x")
    with pytest.raises(ValueError):
        validate("https://mysite.com.evil.test/search?q=x")
    with pytest.raises(ValueError):
        validate("http://mysite.com/search?q=x")
    with pytest.raises(ValueError):
        validate("https://user:pass@mysite.com/search?q=x")