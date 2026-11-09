# vendor/ — 第三方项目本体（不入 git）

物理上与本仓库同处一棵树，方便统一管理；**不进入版本控制**（1.2GB，其中 1.05GB 是各自 venv）。

| 目录 | 上游 | 版本 | 大小 | 自带 .git | 许可证 |
|---|---|---|---|---|---|
| `Spider_XHS/` | https://github.com/cv-cat/Spider_XHS | `e1888d7` | 359M（venv 355M） | ✅ | 见仓库 LICENSE |
| `MediaCrawler/` | https://github.com/NanmiCoder/MediaCrawler | 快照，无 .git | 855M（venv 697M + browser_data 130M） | ❌ | 见仓库 LICENSE |

## 规则

1. **不改 vendor 里的上游代码**。要改就改成我们 `agent_reach/native/` 里的封装。
2. 更新 vendor 项目 = 在各自目录里 `git pull`（Spider_XHS）/ 重新下载（MediaCrawler），然后跑一遍冒烟：
   ```bash
   cd vendor/Spider_XHS && .venv/bin/python -c "import xhs_utils,apis; print('ok')"
   cd vendor/MediaCrawler && .venv/bin/python -c "import playwright; print('ok')"
   ```
3. **venv 跟着目录搬动仍然可用**（`sys.prefix` 按可执行文件解析），但 venv 里 `bin/` 下 console script 的 shebang 是旧绝对路径 —— 只影响直接执行 `bin/pip` 这类脚本，用 `.venv/bin/python -m pip` 代替即可。真要重建：`uv venv && uv pip install -r requirements.txt`。
4. 旧路径 `~/Projects/Spider_XHS`、`~/Projects/MediaCrawler` 保留为**软链**指向这里。所以所有硬编码了旧路径的脚本（`product-recommender`、`rental-search`、`xhs_api.py` 等）零改动继续工作。

## 为什么不用 git submodule

submodule 需要联网 fetch、会把 vendor 的 commit 钉在主仓 index 里、并且 `git clone` 主仓时默认拉空目录。这三个代价对本机单用户场景都不划算；软链 + 本文档钉版本更直接。