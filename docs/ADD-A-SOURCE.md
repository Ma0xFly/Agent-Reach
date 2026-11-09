# Add a source / 新增渠道

Use this guide when a site deserves its own channel: it has an independent
domain, its own login state, and a real site search (or another action the
`web` channel cannot express). If any of those is missing, read public pages
through the `web` channel instead.

The channel contract is in `agent_reach/channels/base.py`; the reference
implementation is `agent_reach/channels/v2ex.py`.

## 1. Generate the skeleton

```bash
python scripts/new_channel.py mysite --host mysite.com
```

This writes `agent_reach/channels/mysite.py` and
`tests/test_mysite_channel.py`. The generated channel uses only the standard
library and its tests never touch the network. `--class-name` overrides the
generated class name; `--host` defaults to `<name>.example` and should be
replaced before the live check. `--force` only overwrites files that carry
the generator's marker, so it cannot wipe a real channel by accident.

## 2. Implement one action at a time

Start with `search`, then add `read`, `comments`, or a site-specific action.
For every backend method:

- Lock requests to the channel's own HTTPS origin (the generated
  `_validate_api_url` is the starting point).
- Pass the dispatcher's `timeout` through to the HTTP/CLI call.
- Return items built with `make_item(...)`; keep the upstream object in `raw`.
- Raise `ReachError(code, message, hint=...)` with a code from the mapping
  below. Hints are instructions for the agent, not for the user to paste
  credentials into.
- Normalize targets in `validate_params` when the action accepts links or IDs,
  so every backend receives the same shape.

## 3. Register the channel

Add two lines to `agent_reach/channels/__init__.py`:

```python
from .mysite import MySiteChannel   # keep the imports alphabetized

ALL_CHANNELS = [
    # ...
    MySiteChannel(),
]
```

The generator prints the exact class name. Then:

```bash
agent-reach channels          # the action and params must appear
```

## 4. Declare the fallback chain and setup

Backends are ordered: the dispatcher tries `(label, method)` pairs left to
right. The first is normally a native/site backend; browser or public-web
paths come later. Example:

```python
Action(
    "search",
    "Search the site",
    (Param("query", "what to search for"),),
    (("mysite-native", "_native_search"), ("opencli", "_browser_search")),
    live_test={"query": "example", "limit": 2},
    timeout=120,
)
```

- `timeout` covers slow actions; without it the global default applies.
- `live_test` is what `agent-reach check <channel>` runs. A backend passes
  when it returns at least one item.
- `public_web` is for anonymous public URLs tried before site tools when the
  channel is not added yet, and as a last resort after a transient site
  failure. It is not a substitute for a real backend.
- If the channel needs a login, declare `login_url` or `session` +
  `login_argv`; see `CONTRIBUTING.md`. For an external runtime that keeps its
  own browser profile, declare `tools`/`prereqs` and explain the manual login
  path in the action hint.
- `setup_problems()` is what `agent-reach doctor` reports. Check the external
  runtime, interpreter, or key there, not only at query time.

## 5. Test, then live-check

```bash
python -m pytest -q tests/test_mysite_channel.py
python -m pytest -q tests/test_channel_contracts.py
ruff check agent_reach/channels/mysite.py tests/test_mysite_channel.py
```

On a machine with the real dependency and login:

```bash
agent-reach add mysite            # optional / experimental channels
agent-reach check mysite --text
```

Paste the `check --text` output into the pull request. Finally update:

- the README and `docs/README_en.md` channel tables;
- `agent_reach/skill/references/capabilities.md` (what is public, what Reach
  adds, what is not exhaustive);
- `CHANGELOG.md` under the next release.

## Error mapping

The dispatcher branches on `error.code`; choose the code by what the backend
actually learned, not by the HTTP status alone.

| Upstream signal | Return | Final? | Why |
|---|---|---|---|
| Local dependency, interpreter, key, or cookie store is missing | `NEED_SETUP` | no | Another backend in the chain may still work |
| The platform rejects the session the user supplied | `NEED_LOGIN` | yes | Only the user can log in; never ask for a password |
| Content is deleted, private, or absent | `NOT_FOUND` | yes | Retrying or switching backends cannot create it |
| Link/ID/params are invalid | `BAD_INPUT` | yes | Fix the request, not the backend |
| Captcha, risk check, or "verify you are human" | `PLATFORM_BLOCKED` | yes | Stop automation; the user resolves it on the site |
| HTTP 429 / "too many requests" | `RATE_LIMITED` | yes | The channel enters cooldown; do not rotate backends |
| HTML instead of JSON, 5xx, changed upstream shape | `UPSTREAM_BROKEN` | no | The next backend or public-web path may work |
| Request or subprocess exceeded the budget | `TIMEOUT` | no | Next backend gets the remaining budget |
| Browser queue is full or a login window is open | `BUSY` | yes | Wait; never close the user's window or switch profiles |

`FINAL_CODES` is `NOT_FOUND`, `BAD_INPUT`, `PLATFORM_BLOCKED`,
`RATE_LIMITED`, `NEED_LOGIN`, and `BUSY`. `NEED_SETUP`, `UPSTREAM_BROKEN`,
and `TIMEOUT` fall through to the next backend.

## Five pitfalls

1. **`host_matches` false positives.** Use
   `host_matches(url, "mysite.com")`; never substring or `endswith` checks.
   `host_matches` already rejects lookalikes (`mysite.com.evil.test`),
   userinfo disguises, non-HTTP schemes, and malformed ports. Pass the bare
   domain, not a full URL or a parent domain that covers unrelated services
   (use `youtube.com`, not `google.com`).
2. **Picking a final error for a retryable failure.** `NEED_SETUP` is
   deliberately non-final so a browser or CLI fallback can take over. Do not
   use `NEED_LOGIN` for "no local cookie found"; that is `NEED_SETUP`. Do not
   use `PLATFORM_BLOCKED` for a single 403 that might be transient.
3. **Treating `NEED_LOGIN` as retryable.** It is terminal: the dispatcher
   stops the chain. Return it only after the platform clearly rejected the
   supplied session. Keep public results already returned; never log in for
   the user or print cookies.
4. **Dragging a native backend into the browser queue.** Only the
   `opencli` and `boss-agent-cli` labels take a browser slot and search
   pacing; a native backend earlier in the chain does not. If you add a
   browser backend, its queue wait and 10-second search interval apply. A
   slow native action needs an explicit `timeout`, not a fake browser label.
5. **Adding a parameter to only one backend.** The dispatcher calls every
   backend with every `Param`. Update every method signature in the chain
   (for example `part` and `lang` in the Bilibili subtitle chain). Use
   `type=int` only for integers; booleans are rejected by dispatch, so use a
   string or integer flag and validate it. If `validate_params` normalizes a
   target, write the normalized value back to `params`.

## Other rules

- Channel modules are imported when `agent_reach.channels` loads. Keep
  imports side-effect free: no Chrome/network access at import time, and no
  top-level `fcntl` (it breaks Windows). Import heavy runtimes inside the
  backend method.
- Do not change `FINAL_CODES`, the result envelope, or the public-web
  semantics for one channel. If a channel needs a different routing rule,
  discuss it as a runtime change.
- Never read a personal browser Cookie database or log in for the user. A
  one-time site-scoped import needs explicit user authorization; keep state
  under `~/.agent-reach/`.

## 中文速查

1. `python scripts/new_channel.py <名字> --host <域名>` 生成 channel 和离线测试。
2. 把 stub 换成真实接口；只锁本站 HTTPS 源、透传 `timeout`、用 `make_item`、按错误表抛 `ReachError`。
3. 在 `channels/__init__.py` 加 import 和实例，跑 `agent-reach channels` 确认。
4. 在 `Action.backends` 里按 `native → fallback` 排链；补 `login_url`/`tools`/`setup_problems()` 和 `timeout`。
5. 跑生成测试、契约测试、`agent-reach check <渠道> --text`，再更新 README、capabilities 和 CHANGELOG。

五个坑：`host_matches` 别用字符串包含；可重试失败别用终态码；`NEED_LOGIN` 是终态；只有 `opencli`/`boss-agent-cli` 进浏览器队列；新参数必须被链上每个 backend 接受。