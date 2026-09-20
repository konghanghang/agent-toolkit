# Agent Toolkit

这个仓库用于维护个人 AI Agent 工具箱，在多台电脑和多个 Agent 环境之间复用同一套工作流。

当前包含：

- Codex 与 Claude Code 共用的 Skills
- Codex 通用 custom Agents
- Codex 全局 Rules
- 公共运行时和命令入口
- 跨平台安装脚本

共享主体只使用 Codex 和 Claude Code 共同支持的 Skill 最小格式；平台专属内容放入对应平台目录，不混入通用 Skill 主契约。

## 目录结构

```text
agent-toolkit/
├── README.md
├── skills/
│   ├── git-commit/
│   ├── upload-to-easyimage/
│   ├── capture-iminling-draft/
│   ├── archery-prod-query/
│   ├── aliyun-sls-log-query/
│   ├── jumpserver-k8s-log-query/
│   ├── online-issue-triage/
│   ├── prometheus-query/
│   ├── tapd-story/
│   ├── work-feature-delivery/
│   ├── grilling/
│   ├── domain-modeling/
│   ├── tapd-weekly-report/
│   ├── verify-test-db/
│   ├── local-browser-verify/
│   └── gitlab-run-pipeline/
├── codex/
│   ├── agents/
│   │   └── evidence_explorer.toml
│   ├── config/
│   │   └── playwright-mcp.toml.template
│   └── rules/
│       ├── work-readonly.rules
│       ├── git-commit.rules
│       ├── github-readonly.rules
│       └── gitlab-pipeline.rules
├── claude/
├── common/
└── scripts/
    ├── install.py
    └── update_ui_ux_pro_max.py
```

目录职责：

- `skills/`：Codex、Claude Code 共用的 Skills，每个目录必须包含 `SKILL.md`。
- `codex/`：Codex 专属配置，保存可版本控制的 custom Agents、MCP 配置模板和全局 Rules。
- `claude/`：Claude Code 专属配置占位；没有真实配置前保持为空。
- `common/`：work 公共运行时、入口脚本和命令模板，不作为 Skill 安装。
- `scripts/`：工具箱自身的安装和维护脚本。

## Skill 兼容规则

共享 Skill 的 `SKILL.md` frontmatter 默认只使用：

```yaml
---
name: skill-name
description: 说明能力和触发场景
---
```

允许使用 Codex 的 `agents/openai.yaml` 作为可选展示边车，但必须满足：

- `SKILL.md` 是两端共同使用的唯一主契约。
- 主流程不能依赖 `agents/openai.yaml`。
- Claude Code 忽略边车后，Skill 仍能完整工作。
- 不在 Skill 中依赖某一端专属的私有编辑命令。

## 根目录术语

统一使用：

- `AGENT_TOOLKIT_ROOT`：本仓库根目录，例如 `~/.agent-toolkit`。
- `PYTHON_BIN`：公共运行时使用的 Python 解释器，默认指向 `AGENT_TOOLKIT_ROOT/.venv` 中的解释器。
- `WORK_ROOT`：业务工作区根目录。
- `TMP_DIR`：临时文件目录。

旧的 `SKILLS_ROOT` 只作为 work 运行时兼容别名保留，新文档和新脚本不要继续使用。

推荐 Shell 配置：

```bash
export AGENT_TOOLKIT_ROOT="$HOME/.agent-toolkit"
export PATH="$AGENT_TOOLKIT_ROOT/bin:$PATH"
```

## 安装

### Windows 前置条件

仓库的真实 Windows CI 使用 `windows-latest` 和 Python 3.11。Windows 本机安装前先准备：

- Git for Windows，并确保 `git.exe` 已加入 `PATH`。
- CPython 3.11，并安装 `py.exe` launcher、`pip` 和 `venv`。
- Windows PowerShell 5.1 的 `powershell.exe`。安装器使用它创建 directory junction，运行时使用它收紧敏感文件 ACL；PowerShell 7 的 `pwsh` 可以作为交互终端，但当前不能替代 `powershell.exe`。
- 将工具箱、用户 Home 以及 Codex/Claude 安装目标放在本机 NTFS 卷。网络盘、FAT/exFAT 和跨卷 junction 不属于已验证范围。

在 PowerShell 中先检查基础依赖：

```powershell
git --version
py -3.11 --version
py -3.11 -m pip --version
py -3.11 -c "import venv"
powershell.exe -NoProfile -Command '$PSVersionTable.PSVersion'
```

`scripts/install.py` 只负责 Skill、Agent 和 Rule 的链接或复制，以及 Codex MCP 配置的安全合并；它不创建公共虚拟环境，也不安装 Python 包。需要使用 work 公共入口或带脚本的 Skill 时，再运行 `common/bootstrap_work.py`；它会创建 `.venv` 并从 `requirements.txt` 安装 Python 依赖。

以下外部程序只在对应能力中需要，不是基础安装的硬依赖：

- Google Chrome：SLS 和 Archery 自动登录。
- `ripgrep`（`rg`）：`tapd-story` 的仓库检索，以及 `online-issue-triage` 的本地代码定位。
- `curl`（Windows 为 `curl.exe`）：`upload-to-easyimage` 的实际上传请求。
- Node.js（含 `npm` / `npx`）：运行 `local-browser-verify` 所需的 Playwright MCP、首次安装 PicGo Core 与 `web-uploader` 插件，以及维护 `ui-ux-pro-max` 外部 Skill；已有 PicGo 配置的日常上传不需要启动 Node.js 或 PicGo。
- Playwright MCP：`local-browser-verify` 的浏览器驱动；Codex 安装会写入标准 MCP 配置，首次启动时 `npx` 可能联网下载 npm 包。其他 Agent 仍由 Skill 在运行时检查，缺失时先提示并等待用户授权。
- PicGo Core 与 `web-uploader` 插件：仅在首次生成或重建 EasyImage 上传配置时需要；上传脚本日常只读取已生成的配置文件。
- Hugo：`capture-iminling-draft` 的草稿构建验证。
- `rsvg-convert`（librsvg）：`upload-to-easyimage --convert-svg`；不转换 SVG 时不需要。

### 克隆

```bash
git clone git@github.com:konghanghang/agent-toolkit.git ~/.agent-toolkit
```

Windows PowerShell：

```powershell
git clone git@github.com:konghanghang/agent-toolkit.git "$HOME\.agent-toolkit"
```

### 预览安装计划

```bash
python3 ~/.agent-toolkit/scripts/install.py --dry-run
```

### 安装 Codex、Claude Code、Codex Agents、Rules 和 MCP 配置

```bash
python3 ~/.agent-toolkit/scripts/install.py
```

安装脚本会：

1. 扫描 `skills/*/SKILL.md`。
2. 将 Skill 通过目录链接安装到 `~/.agents/skills/`。
3. 校验 `codex/agents/*.toml` 的必要字段，并将整个目录链接到 `~/.codex/agents/`。
4. 默认将 `codex/rules/*.rules` 原子复制到 `~/.codex/rules/`。
5. 把标准 Playwright MCP 审批、禁用工具和缓存目录安全合并到 `~/.codex/config.toml`，保留其他本机配置。
6. 将 Skill 通过目录链接安装到 `~/.claude/skills/`。
7. 安装内容包含 `verify-test-db` 时，在缺失的情况下创建私有的 `~/.local/agent-toolkit/work/test-db/profiles.json` 模板；已有文件绝不覆盖。
8. Skill、Agent、Rule 和 Playwright 配置已经一致时跳过。
9. 旧版受管链接可以显式迁移；未知文件、真实目录链接或 Playwright 配置冲突时拒绝覆盖。

目录链接按平台实现：

- macOS/Linux 使用目录软链接。
- Windows 使用 NTFS directory junction，不要求开启 Developer Mode，也不复制 Skill 或 Agent 内容。
- Windows junction 创建失败时安装器会停止并保留错误信息；仓库和安装目标应位于本机 NTFS 卷，且需要可用的 Windows PowerShell。安装器不会静默回退成普通目录副本。

Windows PowerShell 示例：

```powershell
py -3.11 "$HOME\.agent-toolkit\scripts\install.py" --dry-run
py -3.11 "$HOME\.agent-toolkit\scripts\install.py"
```

### Windows 命令入口与 ExecutionPolicy

运行 bootstrap 后会同时生成 `.cmd` 和 `.ps1` 入口。PowerShell 中默认使用 `.cmd`；它不受 PowerShell ExecutionPolicy 限制，也不需要修改本机策略：

```powershell
& "$HOME\.agent-toolkit\bin\sls.cmd" --help
& "$HOME\.agent-toolkit\bin\archery.cmd" list-instances-safe
```

只有明确选择运行 `.ps1` 时才检查策略：

```powershell
Get-ExecutionPolicy -List
```

如果 `MachinePolicy` 或 `UserPolicy` 已由组织配置，保留组织策略并继续使用 `.cmd`，不要尝试覆盖。如果没有组织策略、且用户明确希望启用本机 `.ps1` wrapper，可以只在当前用户范围使用 `RemoteSigned`：

```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

安装器不会修改 `ExecutionPolicy`。不要设置 `LocalMachine` 范围，不要使用 `Unrestricted`，也不要通过持久化 `Bypass` 绕过策略；无法修改当前用户策略时继续使用 `.cmd`。

从旧的 `~/.shared-skills` 迁移时使用：

```bash
python3 ~/.agent-toolkit/scripts/install.py --replace-managed
```

可选参数：

```text
--dry-run          只显示计划，不修改文件
--codex-only       只安装 Codex Skills、Agents、Rules 和受管配置
--claude-only      只安装 Claude Code Skills
--rules-only       只安装 Codex Rules
--agents-only      只安装 Codex Agents
--config-only      只安装受管的 Codex Playwright MCP 配置
--codex-home PATH  将 Agents、Rules 和受管配置安装到指定的现有 Codex Home
--skill NAME       只处理指定 Skill，可重复使用
--replace-managed  替换指向旧工具箱或旧目录结构的受管链接
```

### 自定义 Codex Home

安装脚本无参数运行时始终使用默认的 `~/.codex`，不会扫描磁盘、解析 Shell alias
或读取 `CODEX_HOME` 来猜测其他 Codex Home。

存在额外 Codex Home 时，由用户或对应环境的初始化脚本明确指定目录，并分别安装 Agents、Rules 和受管配置：

```bash
python3 ~/.agent-toolkit/scripts/install.py \
  --agents-only \
  --codex-home ~/data/work/.codex \
  --dry-run

python3 ~/.agent-toolkit/scripts/install.py \
  --rules-only \
  --codex-home ~/data/work/.codex \
  --dry-run

python3 ~/.agent-toolkit/scripts/install.py \
  --config-only \
  --codex-home ~/data/work/.codex \
  --dry-run

python3 ~/.agent-toolkit/scripts/install.py \
  --agents-only \
  --codex-home ~/data/work/.codex

python3 ~/.agent-toolkit/scripts/install.py \
  --rules-only \
  --codex-home ~/data/work/.codex

python3 ~/.agent-toolkit/scripts/install.py \
  --config-only \
  --codex-home ~/data/work/.codex
```

自定义 Codex Home 必须已经存在；安装脚本不会因为路径拼写错误而创建一个新的 Home。
每次运行都会输出实际使用的 Codex Home，以及本次涉及的 Agents 或 Rules 目标目录。

## 安装结果

Codex Skills：

```text
~/.agents/skills/<name>
  → ~/.agent-toolkit/skills/<name>
```

Claude Code Skills：

```text
~/.claude/skills/<name>
  → ~/.agent-toolkit/skills/<name>
```

Codex Agents：

```text
~/.codex/agents
  → ~/.agent-toolkit/codex/agents

<custom-codex-home>/agents
  → ~/.agent-toolkit/codex/agents
```

Codex Rules：

```text
~/.agent-toolkit/codex/rules/<name>.rules
  --原子复制--> ~/.codex/rules/<name>.rules

~/.agent-toolkit/codex/rules/<name>.rules
  --原子复制--> <custom-codex-home>/rules/<name>.rules
```

Codex Playwright MCP 配置：

```text
~/.agent-toolkit/codex/config/playwright-mcp.toml.template
  --安全合并--> ~/.codex/config.toml
  --安全合并--> <custom-codex-home>/config.toml
```

安装器只管理 `[mcp_servers.playwright]` 下的 `default_tools_approval_mode`、`disabled_tools`，以及 `[mcp_servers.playwright.env]` 下的 `npm_config_cache`。Playwright 尚未配置时会写入完整标准配置；已经配置时保留原有 transport、其他 MCP、模型和权限设置。受管字段存在不同值时停止并报告冲突，不静默覆盖本机选择。新建 `config.toml` 在 macOS/Linux 使用 `0600` 权限，在 Windows 将 ACL 收紧到当前用户、SYSTEM 和 Administrators；更新已有文件时保留原有 POSIX 权限或 Windows ACL。

Codex Agents 使用目录链接，多个 Codex Home 可以共享同一份受版本控制的 Agent 定义。macOS/Linux 使用软链接，Windows 使用 directory junction。目标 `agents/` 已经是真实目录或未知目录链接时，安装器拒绝覆盖；需要纳入统一管理的 Agent 应先放入本仓库的 `codex/agents/`。

Codex 的 Rules 扫描器只加载 `rules/` 下的真实 `.rules` 文件，文件软链不会进入加载列表，因此 Rules 使用受管复制而不是目录链接。安装器通过文件头标记识别受管 Rule，安全更新已有副本，并保留本机 `default.rules`。Skill 和 Agent 目录链接不受影响。

Codex 会为新会话加载 Agents、Rules 和 MCP 配置。Agent 源文件通过目录链接立即更新，Rule 或 Playwright 配置变更后需要重新安装；完成后应新开会话或重启客户端。

## work 公共运行时

初始化：

```bash
python3 ~/.agent-toolkit/common/bootstrap_work.py
```

Windows PowerShell：

```powershell
py -3.11 "$HOME\.agent-toolkit\common\bootstrap_work.py"
```

首次初始化默认在仓库根目录创建 `.venv`，并从 `requirements.txt` 安装公共运行时和 Skill 校验依赖。`.venv` 是本机环境，不进入 Git；业务凭证和 Session 仍保存在 `~/.local/agent-toolkit/work/`。已有运行时配置会保留当前 `PYTHON_BIN` 默认值，迁移到项目环境时在交互提示中明确输入 `~/.agent-toolkit/.venv/bin/python`（Windows 使用 `.venv\Scripts\python.exe`）。

后续仓库测试和维护命令统一使用项目解释器，例如：

```bash
~/.agent-toolkit/.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

Windows PowerShell：

```powershell
& "$HOME\.agent-toolkit\.venv\Scripts\python.exe" -m unittest discover -s tests -p "test_*.py"
```

初始化会配置：

- `WORK_ROOT`
- `TMP_DIR`
- `PROMETHEUS_BASE_URL`（可选；仅使用 `prometheus-query` 时需要）
- `SLS_DIR`
- `ARCHERY_DIR`
- `JMS_DIR`
- `TEST_DB_DIR`
- `GITLAB_DIR`
- `PYTHON_BIN`
- `AGENT_TOOLKIT_ROOT`

并在 `AGENT_TOOLKIT_ROOT/bin/` 生成：

- `sls`
- `archery`
- `jms`
- `testdb`
- `gitlab`
- Windows 对应的 `.cmd` 与 `.ps1` 入口

`~/.local/agent-toolkit/work/runtime.json`、凭证和 Session 都是本机状态，不提交到本仓库。

GitLab 的实际域名和 Token 保存在 `GITLAB_DIR/gitlab-credentials.json`，仓库内不写死服务地址。安装器和 bootstrap 会在文件不存在时创建仅含 `base_url`、`private_token` 两个空 value 的私有模板，已有文件绝不覆盖。填写后可用 `gitlab whoami` 验证，并用 `gitlab trigger` 触发和跟踪 Pipeline、`gitlab result` 查询最终结果。

## 当前 Skills

- `git-commit`
- `upload-to-easyimage`
  - [PicGo web-uploader 配置说明](skills/upload-to-easyimage/references/picgo-config.md)
  - [线上图文教程](https://www.iminling.com/2025/typora-how-to-use-picgo-upload-image-to-easyimage/)
- `capture-iminling-draft`
- `archery-prod-query`
- `aliyun-sls-log-query`
- `jumpserver-k8s-log-query`
- `online-issue-triage`
- `prometheus-query`
- `tapd-story`
- [work-feature-delivery](skills/work-feature-delivery/SKILL.md)：衔接 work 已授权交付阶段，按需记录验证、审查和交付证据
  - [work 交付规则](skills/work-feature-delivery/references/delivery-workflow.md)：分支、提测和上线流程的统一维护位置
- `grilling`
- `domain-modeling`
- `tapd-weekly-report`
- `verify-test-db`
  - [测试库配置](skills/verify-test-db/references/configuration.md)
- `local-browser-verify`
  - [线索信息管理待初审夹具](skills/local-browser-verify/references/clue-unsubmitted-fixture.md)
- `gitlab-run-pipeline`

## 当前 Codex Agents

- `evidence_explorer`：只读探索真实调用链、数据边界、行为分支和待确认项，向主 Agent 返回带代码证据的压缩结果，不修改代码或生成最终计划。

## 当前 Codex Rules

- `work-readonly.rules`：允许 work 的登录、受限 SLS 查询、Archery 元数据查询、固定测试命名空间日志查询，以及测试库元数据、受限查询、脱敏验证和只生成计划的写入准备；生产 SQL、测试库实际写入、任意 SLS 查询和通用 JMS 命令仍需审批。
- `git-commit.rules`：自动允许从 `origin` 获取更新、通过 `git switch -c` 新建分支，以及受控的 `git add`、新提交和普通主分支推送。
- `github-readonly.rules`：自动允许 `gh pr view/checks`、`gh run list/view`、`gh issue view` 和 `gh repo view`。
- `gitlab-pipeline.rules`：自动允许 `gitlab whoami/search/resolve/inspect/result/trigger` 六个子命令；Rule 只取消 Codex 权限弹窗，`trigger` 仍由 CLI 强制校验 `plan_id`、用户确认、计划漂移和生产环境授权。

Rules 的 `allow` 只处理命令越过沙箱的权限，不替代 Skill、`AGENTS.md` 或用户指令中的操作边界。

## 新增 Codex Agent

新的 Codex custom Agent 必须：

1. 放入 `codex/agents/<agent-name>.toml`。
2. 至少定义 `name`、`description` 和 `developer_instructions`。
3. 使用清晰、稳定、项目无关的职责边界；项目特定规则继续放在项目 `AGENTS.md`。
4. 只读 Agent 必须显式配置 `sandbox_mode = "read-only"`。
5. 不写入凭证、个人机器绝对路径或某个项目独有的业务口径。
6. 完成后运行安装器单元测试，以及默认和自定义 Codex Home 的 `--agents-only --dry-run`。

## 新增 Skill

新的共享 Skill 必须：

1. 放入 `skills/<skill-name>/`。
2. 同时兼容 Codex 和 Claude Code。
3. 使用公共 `SKILL.md` frontmatter。
4. 不写入凭证、Token、Cookie、Session 或个人机器绝对路径。
5. 将确定性逻辑放入自身 `scripts/`。
6. 将详细参考资料放入自身 `references/`。
7. 不在 Skill 目录额外创建 README、安装指南或变更日志。
8. 完成后运行 Skill 校验和安装脚本 dry-run。

## 机器相关配置

这些值不要提交：

- API Key、Token、Cookie、Session
- SSH 私钥和认证文件
- EasyImage、PicGo 的真实凭证
- 数据库和生产环境凭证
- Prometheus 等通过 `runtime.json` 管理的内部服务地址
- `~/.local/agent-toolkit/work/runtime.json`
- 工作区绝对路径

例如 `capture-iminling-draft` 的目标仓库路径应在每台机器单独设置：

```bash
git config --global iminling.repoPath /absolute/path/to/iminling-pages
```

## 更新

拉取工具箱：

```bash
git -C ~/.agent-toolkit pull
```

Skill 和 Agent 目录链接会立即读取仓库内更新。Rules 是受管副本，拉取后需要重新运行：

```bash
python3 ~/.agent-toolkit/scripts/install.py --rules-only
```

Agent 或 Rule 更新后，新开会话或重启客户端使其生效。

### 更新外部 `ui-ux-pro-max`

`ui-ux-pro-max` 来自
[`nextlevelbuilder/ui-ux-pro-max-skill`](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill)，
本地内容保存在 `skills/ui-ux-pro-max/` 并通过 `.gitignore` 排除，不直接提交上游生成文件。

预览官方最新版的生成和校验结果：

```bash
python3 ~/.agent-toolkit/scripts/update_ui_ux_pro_max.py --dry-run
```

确认后更新：

```bash
python3 ~/.agent-toolkit/scripts/update_ui_ux_pro_max.py
```

脚本会使用官方 npm CLI 在临时目录生成当前 `.agents/skills/` 版本，并兼容旧版
`.codex/skills/` 输出；只提取主 `ui-ux-pro-max` Skill。更新前会运行可独立执行的
上游单元测试、数据校验和搜索 smoke test，再把详细检查表迁入 `references/` 做渐进
披露，最后原子替换共享目录。只适用于上游源码仓库、依赖未打包维护脚本的测试会被
明确列为 source-only，不会伪装成已通过。旧版本保存在 Git 忽略的 `.local/backups/`，
版本、来源、本地转换和内容摘要记录在 `external-skills.lock.json`。

如需安装指定版本：

```bash
python3 ~/.agent-toolkit/scripts/update_ui_ux_pro_max.py --version 2.10.2
```

全新检出或 CI 可严格按锁文件还原版本、本地转换和内容摘要，且不重写锁文件：

```bash
python3 ~/.agent-toolkit/scripts/update_ui_ux_pro_max.py --from-lock
```

提交前至少检查：

- 所有 Skill 均能通过格式校验。
- `scripts/install.py --dry-run` 无未知冲突。
- Codex 与 Claude Code 的 Skill 目录链接目标正确。
- 默认和自定义 Codex Home 的 Agent 目录链接目标正确。
- Codex Rules 是与源文件一致的真实文件，且 `execpolicy check` 正常。
- 除迁移兼容说明外，没有旧 `~/.shared-skills` 路径残留。
- 没有凭证和机器相关运行时文件进入 Git。
