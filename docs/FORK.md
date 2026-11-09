# Fork 维护 / Fork maintenance

这个仓库是 [`Panniantong/Agent-Reach`](https://github.com/Panniantong/Agent-Reach) 的 fork，
自己的仓库是 [`Ma0xFly/Agent-Reach`](https://github.com/Ma0xFly/Agent-Reach)。

**永远不要向 `Panniantong/Agent-Reach` 推送分支或开 PR。** 所有分支、PR 和发布都只进
`Ma0xFly/Agent-Reach`。同步上游时只 fetch，不 push。

## 远端约定

Fork 克隆通常有两个远端：

```bash
origin    https://github.com/Ma0xFly/Agent-Reach.git        # 自己的仓库
upstream  https://github.com/Panniantong/Agent-Reach.git     # 只读上游
```

本仓库当前的工作副本是另一种布局（`origin` 指上游、`fork` 指自己），所以脚本只依赖
`upstream` 这个名字；没有就按下面的 URL 添加一次：

```bash
git remote add upstream https://github.com/Panniantong/Agent-Reach.git
git fetch upstream main
```

`scripts/upstream-sync.sh` 会自动添加缺失的 `upstream`，并拒绝在 `upstream` 指向自己的
fork 时继续。

## 这个 fork 加了什么

新增文件（上游没有，通常不会产生 rebase 冲突）：

- `agent_reach/native/{xhs,bilibili,douyin}.py`、`native/_items.py`
- `agent_reach/channels/douyin.py`
- `scripts/new_channel.py`、`scripts/upstream-sync.sh`
- `docs/ADD-A-SOURCE.md`、`docs/FORK.md`
- 对应的 `tests/test_native_*.py`、`test_douyin_channel.py`、
  `test_youtube_comments_channel.py`、`test_new_channel.py` 等
- `改造计划.md`（本地改造记录，不参与上游发布）

修改了上游已有文件。计划最初按“5 个核心文件”估算：
`channels/bilibili.py`、`channels/xiaohongshu.py`、`channels/youtube.py`、
`channels/__init__.py`、`runtime/dispatch.py`。P1–P5 实际还改了下面这些集成文件，
但它们都是小改动，rebase 时多数会自动合并。

| 文件 | 改动意图 | 冲突风险 |
|---|---|---|
| `agent_reach/channels/bilibili.py` | 挂 native search/read/subtitle/summary；read 链 `bilibili-api → native`；**bili-cli 集成已删除**（`_cli_*`、`_bili_json`、`_reuse_subtitle_login`、login session），登录改由 config 的 `bilibili_sessdata` 提供 | 高（上游也改 B站） |
| `agent_reach/channels/xiaohongshu.py` | search/read/comments 改为 `native → opencli`；timeout 120s；`setup_problems` | 中 |
| `agent_reach/channels/youtube.py` | 新增 comments/channel；严格 video/channel target；复用 `_flat_video_item` | 高（上游改 yt-dlp/TLS/VTT） |
| `agent_reach/channels/__init__.py` | 注册 `DouyinChannel()` | 低（两行） |
| `agent_reach/runtime/dispatch.py` | 浏览器 queue/pacing 改为按 backend label 判断，native 后端不继承 | 高（上游也改 dispatch） |
| `agent_reach/runtime/cli.py` | `read` / `check` 的默认 timeout 从 Action 取，不再固定 60s | 低 |
| `agent_reach/runtime/manage.py` | `run_checks` 按 Action timeout；skill 渠道表显示真实后端链 | 中 |
| `agent_reach/runtime/onboarding.py` | 抖音、YouTube comments/channel、B站 summary 的能力与限制 | 低 |
| `scripts/record_release_acceptance.py` | 补 bilibili summary、douyin read/comments 的 target 来源 | 中（P2/P3 合并时手工组合过） |
| `agent_reach/skill/SKILL.md`、`SKILL_en.md`、`references/*` | 路由链、可降级/终态错误、抖音登录路径、能力边界 | 中（上游也改 skill） |
| `README.md`、`docs/README_en.md`、`llms.txt`、`CHANGELOG.md`、`CONTRIBUTING.md` | 平台表、设计示例、变更记录、脚手架入口 | 低 |
| `pyproject.toml`、`.gitignore` | `vendor/` 排除、pytest 收集范围 | 低 |
| `tests/test_manage.py`、`test_release_policy.py`、`test_unified_entry.py` | 新行为的回归测试 | 低到中 |

## 同步步骤

推荐直接跑脚本：

```bash
scripts/upstream-sync.sh --dry-run   # 只打印计划
scripts/upstream-sync.sh             # fetch + rebase + pytest + 3 条冒烟
```

脚本做的事：

1. 检查当前分支和 tracked 改动；有未提交改动就停下。
2. 确保存在 `upstream` 远端；拒绝指向自己 fork 的 `upstream`。
3. `git fetch upstream main`
4. `git rebase --rebase-merges upstream/main`
5. `pytest -q`
6. 三条只读冒烟（不带 `--json`）：
   - `agent-reach channels`
   - `agent-reach youtube channel @YouTube --limit 2`
   - `agent-reach bilibili search 车联网 --limit 2`
7. 不推送。检查 diff 后，由维护者自己推到自己的 fork；**绝不推到 upstream**。

手工等价命令：

```bash
git fetch upstream main
git rebase --rebase-merges upstream/main
# 解决冲突，git add，git rebase --continue；放弃用 git rebase --abort
pytest -q
agent-reach channels
agent-reach youtube channel "@YouTube" --limit 2
agent-reach bilibili search "车联网" --limit 2
```

### 为什么用 `--rebase-merges`

`main` 里有 P1/P2/P3 的集成 merge commit，里面的冲突已经手工解决过。普通
`git rebase upstream/main` 会把各分支的提交逐个重放，丢失这些 merge 的解决结果，于是
`scripts/record_release_acceptance.py` 会在 P3 就冲突；`native/__init__.py` /
`native/_items.py` 也容易以 add/add 形式再次冲突。`--rebase-merges` 保留 merge 结构，
2026-11-09 的演练只有 4 个文件需要处理。

## 已知冲突与处理

| 冲突文件 | 原因 | 处理 |
|---|---|---|
| `scripts/record_release_acceptance.py` | P2 加 bilibili summary，P3 加 douyin，两条分支都改了 `FROM_SEARCH` | 保留两边：`("bilibili","summary")`、`("douyin","read")`、`("douyin","comments")` 都留着 |
| `agent_reach/native/__init__.py`、`native/_items.py` | P1/P2/P3 各自新增同一文件，merge 时曾手工合并 | 优先保留 re-base 前 `main` 的整合版本，例如 `git checkout <pre-rebase-branch> -- agent_reach/native/__init__.py agent_reach/native/_items.py`，再检查各平台映射是否都在 |
| `agent_reach/channels/youtube.py` | 上游改了 `_VTT_TIME_RE` 并加 `ytdlp_tls_args`；P4 加了 video/channel 正则和 comments/channel 动作 | 保留上游的 VTT/TLS 改动，再保留 `_VIDEO_PATH_RE`、`_CHANNEL_ID_RE`、`_CHANNEL_PATH_RE` 及两个新 Action |
| `agent_reach/runtime/dispatch.py` | 上游也改这段，我们的 patch 是“按 label 进浏览器队列/节流” | 以原因为准：若上游已经按 label 判断，删掉我们的 patch；否则把 `browser_backends` / `query_slot` / `_pace_browser` 那段重新套到上游新结构上。不改 `FINAL_CODES`、冷却和降级语义 |
| `agent_reach/channels/bilibili.py`、`xiaohongshu.py` | 上游可能改登录、Cookie、返回格式 | 先保留上游修复，再把我们的 native Action 和 backend 元组插回去；跑对应 native 测试 |
| `agent_reach/skill/*`、README、llms、tests | 上游也在改文案/路径 | 先接上游版本，再把本 fork 的平台/动作条目补回；`render_skill` 的后端链列必须保留 |

解决完冲突后，至少跑：

```bash
pytest -q
ruff check agent_reach tests scripts
mypy agent_reach
```

## 2026-11-09 演练记录

- merge base：`422478a`（2026-11-06）
- upstream：`c70ae6d`，比 fork 多 54 个提交
- fork main：`9e7f19f`
- 在临时分支执行 `git rebase --rebase-merges upstream/main`
- 冲突 4 个文件：`scripts/record_release_acceptance.py`、
  `agent_reach/native/__init__.py`、`agent_reach/native/_items.py`、
  `agent_reach/channels/youtube.py`
- 按上表解决后：`pytest -q` → **891 passed**（fork main 当时是 806 passed，上游新增了测试）
- 冒烟：`channels` 15 个；YouTube channel 返回 `ok/yt-dlp/2`；B站 search 返回
  `ok/native/2`
- 演练分支已删除，`fork/main` 没有被改写；本记录只证明同步路径可行，正式同步仍由维护者执行

## 纪律

- 新能力优先放新文件；上游已有文件只做 `actions` 元组插入和必要的集成小改。
- 运行时语义补丁只保留 `runtime/dispatch.py` 的浏览器判定粒度；`runtime/cli.py` /
  `runtime/manage.py` 的 timeout 修复是让 `Action.timeout` 生效的通用修正，上游若已修就删我们的。
  不改 `result.py`、信封语义、`FINAL_CODES`。
- 上游如果自己实现了小红书/B站/Douyin 原生后端，先比对；能替代就删我们的文件，减少维护面。
- `vendor/` 不进 git；任何明文 Cookie/SESSDATA 不得进入 diff（提交前
  `git diff | grep SESSDATA=` 必须为空）。
- 每次同步后跑测试；不自动 push，不向 upstream 开 PR。
- skill 的源头是仓库里的 `agent_reach/skill/`；**模板里的 `{{CHANNEL_TABLE}}` 和 `{{VERSION}}` 由 `render_skill()` 现场从代码生成**，所以改完模板必须重铺，**不能裸 `cp`**——裸 cp 会把占位符原样铺出去，Agent 打开 skill 看到的是一张空表。重铺方式二选一：
  ```bash
  agent-reach update --sync-only          # 正规入口：重铺 skill + 同步工具
  # 或只重渲染这一份：
  python -c "from pathlib import Path; from agent_reach.runtime.manage import render_skill; \
Path.home().joinpath('.agents/skills/agent-reach/SKILL.md').write_text(render_skill('zh'), encoding='utf-8')"
  ```
  验证：`grep -c '{{' ~/.hermes/skills/agent-reach/SKILL.md` 必须是 0，且渠道行数 15。