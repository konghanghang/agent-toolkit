# 安装指南

[返回首页](../README.md) · [Windows 指南](windows.md) · [维护与验证](maintenance.md)

首次安装的克隆、外部 Skill 还原和安装命令见 [快速开始](../README.md#快速开始)。本文说明安装范围、参数、配置保护与工作环境初始化。

## 安装分类与本机记录

分类唯一来源为 [`install-profiles.json`](../install-profiles.json)。`general` 用于通用与个人用途；`work` 包含 general + 工作扩展。完整能力清单见 [首页索引](../README.md#能力索引)。

通用组同时安装全部通用 Agents、`git-commit.rules`、`github-readonly.rules` 和 Codex Playwright MCP 配置。工作组额外安装 `work-readonly.rules`、`gitlab-pipeline.rules`，并按所选 Skills 创建 GitLab 与测试库配置模板。平台和组件范围仍由 `--codex-only`、`--claude-only`、`--rules-only` 等参数限制；Claude-only 不写 Codex 配置。

首次安装必须显式选择 profile；未选择时只给出提示并退出，不默认安装全量。成功安装后，选择保存在 `~/.local/agent-toolkit/install-profile.json`，后续无 `--profile` 的安装或更新沿用它。记录属于本机当前用户，由不同仓库检出和安装目标共用，不跟随 `--codex-home` 单独变化，也不进入 Git。切换 profile 不自动卸载已有组件。

选择顺序为：显式 `--profile` → 机器级记录 → 当前检出的旧记录 `.local/install-profile.json`。只有新位置不存在时才读取旧位置；高优先级记录损坏或无效时直接报错，不能回退后静默改变安装范围。显式指定 profile 可覆盖保存值。使用旧记录成功安装后，只向机器级新位置保存，旧文件保留；dry-run 和失败安装都不会迁移或修改记录。

`--skill` 只在当前 profile 内进一步筛选；例如 `--profile general --skill verify-test-db` 会报错，不会越过分类安装工作内容。Rules 也按 profile 过滤，因此不再因选择一个通用 Skill 而附带工作 Rules。

完整 `work` profile 包含 `feature-delivery` 和 `work-feature-delivery`；使用 `--skill` 手动筛选公司交付技能时，需同时选择 `feature-delivery`。两者的配合方式见 [开发工作流](workflows.md)。

外部 `ui-ux-pro-max` 未还原时，安装器会明确提示缺失并安装其余可用内容；新检出应先运行 `scripts/update_ui_ux_pro_max.py --from-lock`，安装器不隐式联网下载。工作运行时初始化是独立步骤，需要本机工作区等信息；服务凭证按需填写。

### 后续如何识别本机用途

本机用途保存在 `~/.local/agent-toolkit/install-profile.json`。例如，家庭电脑的记录为：

```json
{
  "schema_version": 1,
  "profile": "general"
}
```

后续在本项目内检查安装、更新、修复、迁移或清理时，先读取这份记录；新位置不存在时兼容读取当前检出的旧记录。沿用其中的 profile：`general` 只处理通用组件，`work` 处理通用组件和工作扩展。用户当前明确指定的用途优先；两个位置均无记录或选中的记录无效时，需要明确选择用途，不能根据仓库内存在工作 Skills 或历史备份推断本机应安装工作内容。

安装器会自动读取保存值，因此日常预览可直接运行：

```bash
python3 scripts/install.py --dry-run
```

检查输出的 `Installation profile` 是否符合本机用途，再按预览处理。安装范围的识别不代表安装或清理授权，切换 profile 也不会自动卸载已有组件。

这项约定的维护范围是本仓库的安装说明与本机记录；客户端的用户级全局指令由用户独立管理。

## 安装器管理范围

`scripts/install.py` 只负责 Skill、Agent 和 Rule 的链接或复制，以及 Codex MCP 配置的安全合并；它不创建公共虚拟环境，也不安装 Python 包。需要使用工作组的 工作公共入口或依赖公共运行时的 Skill 时，再运行 `common/bootstrap_work.py`；它会创建 `.venv` 并从 `requirements.txt` 安装 Python 依赖。

以下以通用安装为例；工作电脑改为 `--profile work`。安装脚本会：

1. 扫描 `skills/*/SKILL.md`，校验分类清单并按 profile 筛选。
2. 将 Skill 通过目录链接安装到 `~/.agents/skills/`。
3. 校验 `codex/agents/*.toml` 的必要字段，并将整个目录链接到 `~/.codex/agents/`。
4. 将当前 profile 的 `codex/rules/*.rules` 原子复制到 `~/.codex/rules/`。
5. 把标准 Playwright MCP 审批、禁用工具和缓存目录安全合并到 `~/.codex/config.toml`，保留其他本机配置。
6. 校验 `opencode/agents/*.md` 的 frontmatter，并把 `opencode/agents/` 链接到 `~/.config/opencode/agents/`。OpenCode 配置安装到 `~/.config/opencode/opencode.json`：macOS/Linux 使用文件链接，Windows 使用受管副本，无需文件符号链接权限。
7. 将 Skill 通过目录链接安装到 `~/.claude/skills/`。
8. 安装内容包含 `verify-test-db` 时，在缺失的情况下创建私有的 `~/.local/agent-toolkit/work/test-db/profiles.json` 模板；已有文件绝不覆盖。
9. Skill、Agent、Rule 和 Playwright 配置已经一致时跳过。
10. 旧版受管链接可以显式迁移；未知文件、真实目录链接或 Playwright 配置冲突时拒绝覆盖。OpenCode 的已有文件或目录只有在内容与仓库逐字节一致时才会接管，原路径先移到 Git 忽略的 `.local/backups/opencode/`。Windows 受管配置副本按安装记录更新；本机修改过的内容拒绝覆盖。

目录链接按平台实现：

- macOS/Linux 使用目录软链接。
- Windows 使用 NTFS directory junction，不要求开启 Developer Mode，也不复制 Skill 或 Agent 内容。
- Windows junction 创建失败时安装器会停止并保留错误信息；仓库和安装目标应位于本机 NTFS 卷，且需要可用的 Windows PowerShell。安装器不会静默回退成普通目录副本。

Windows 的依赖检查、PowerShell 命令和入口行为见 [Windows 指南](windows.md)。

## 安装参数与旧版迁移

可选参数：

```text
--profile NAME     general = 通用；work = 通用 + 工作扩展；省略时沿用本机记录
--dry-run          只显示计划，不修改文件
--codex-only       只安装 Codex Skills、Agents、Rules 和受管配置
--claude-only      只安装 Claude Code Skills
--rules-only       只安装 Codex Rules
--agents-only      只安装 Codex Agents
--opencode-only    只安装 OpenCode 配置和 Agents
--config-only      只安装受管的 Codex Playwright MCP 配置
--codex-home PATH  将 Agents、Rules 和受管配置安装到指定的现有 Codex Home
--skill NAME       只处理指定 Skill，可重复使用
--replace-managed  替换指向旧工具箱或旧目录结构的受管链接
```

从旧的 `~/.shared-skills` 迁移时，先预览，再替换受管链接；profile 沿用本机记录，首次使用须显式指定：

```bash
python3 ~/.agent-toolkit/scripts/install.py --replace-managed --dry-run
python3 ~/.agent-toolkit/scripts/install.py --replace-managed
```

## 自定义 Codex Home

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

## 安装结果与配置保护

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

安装器只管理 `[mcp_servers.playwright]` 下的 `default_tools_approval_mode`、`disabled_tools`，以及 `[mcp_servers.playwright.env]` 下的 `npm_config_cache`。模板中的包版本是 `@playwright/mcp@0.0.83`。Playwright 尚未配置时会写入完整标准配置，包含这个版本；已经配置时保留原有 `command`、`args`、其他 MCP 和权限设置，不会把已有的 `@latest` 或其他版本改掉。受管字段存在不同值时停止并报告冲突，不静默覆盖本机选择。新建 `config.toml` 在 macOS/Linux 使用 `0600` 权限，在 Windows 将 ACL 收紧到当前用户、SYSTEM 和 Administrators；更新已有文件时保留原有 POSIX 权限或 Windows ACL。

Codex Agents 使用目录链接，多个 Codex Home 可以共享同一份受版本控制的 Agent 定义。macOS/Linux 使用软链接，Windows 使用 directory junction。目标 `agents/` 已经是真实目录或未知目录链接时，安装器拒绝覆盖；需要纳入统一管理的 Agent 应先放入本仓库的 `codex/agents/`。

OpenCode：

```text
~/.config/opencode/opencode.json
  → ~/.agent-toolkit/opencode/opencode.json

~/.config/opencode/agents
  → ~/.agent-toolkit/opencode/agents
```

上图的配置文件链接适用于 macOS/Linux；Windows 使用普通文件副本，Agents 使用目录 junction。Windows 在同目录的 `.opencode.json.agent-toolkit.json` 中记录安装内容的 SHA-256；重复安装时跳过一致的副本，仓库配置更新后重新运行安装器即可更新副本。本机文件被修改、未知文件内容不同或安装记录无效时拒绝覆盖，`--replace-managed` 也不会覆盖本机修改。已有正确的配置文件链接可继续使用。

网关地址和 API Key 只通过 `{env:NAME}` 引用，不写入仓库。`cli.json`、`tui.json`、`service.json` 和本机依赖仍留在 `~/.config/opencode/`，不由安装器管理。目标已经指向仓库时跳过；接管内容一致的普通文件或目录、更新 Windows 配置副本时，原路径先移到 `.local/backups/opencode/`；目录里有额外文件时拒绝覆盖。目录比较包含隐藏文件，文件路径和内容均须一致。

安装后按 [OpenCode 使用说明](opencode.md#本机环境变量)配置本机环境变量、同步服务地址并重启服务。Gemini 原生接入单独使用 `NEWAPI_GEMINI_BASE_URL`，地址路径为 `/v1beta`；其他 provider 使用的 `NEWAPI_BASE_URL` 路径为 `/v1`。

Codex 的 Rules 扫描器只加载 `rules/` 下的真实 `.rules` 文件，文件软链不会进入加载列表，因此 Rules 使用受管复制而不是目录链接。安装器通过文件头标记识别受管 Rule，安全更新已有副本，并保留本机 `default.rules`。Skill 和 Agent 目录链接不受影响。

Codex 会为新会话加载 Agents、Rules 和 MCP 配置。Agent 源文件通过目录链接立即更新，Rule 或 Playwright 配置变更后需要重新安装；完成后应新开会话或重启客户端。

## 工作公共运行时

仅工作环境需要初始化；通用安装不运行 bootstrap，也不创建 工作运行时。Windows 命令见 [Windows 指南](windows.md#安装)。

初始化：

```bash
python3 ~/.agent-toolkit/common/bootstrap_work.py
```

首次初始化默认在仓库根目录创建 `.venv`，并从 `requirements.txt` 安装公共运行时和 Skill 校验依赖。`.venv` 是本机环境，不进入 Git；业务凭证和 Session 仍保存在 `~/.local/agent-toolkit/work/`。已有运行时配置会保留当前 `PYTHON_BIN` 默认值，迁移到项目环境时在交互提示中明确输入 `~/.agent-toolkit/.venv/bin/python`（Windows 使用 `.venv\Scripts\python.exe`）。

初始化会配置：

- `WORK_ROOT`
- `TMP_DIR`
- `PROMETHEUS_BASE_URL`（可选；仅使用 `prometheus-query` 时需要）
- `NACOS_CN_BASE_URL`、`NACOS_OS_BASE_URL`（可选；仅使用 `nacos-config-query` 时需要）
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

## 工作环境私有配置

SLS 的 project/logstore、环境与服务路由、日志保留天数、Archery 实例路由，以及 TAPD 周报的工作区和成员列表，放在本机 `~/.local/agent-toolkit/work/config.json`。真实资源和人员信息不写入源码、测试或公共文档；仓库只保存空模板和虚构示例。

在仓库根目录预览并创建模板：

```bash
python3 scripts/init_work_config.py --dry-run
python3 scripts/init_work_config.py
```

Windows PowerShell 使用 `py -3.11 scripts\init_work_config.py`，同样支持 `--dry-run`。初始化只创建缺失文件，保留已有配置；新文件在 macOS/Linux 使用 `0600`，Windows 使用与其他私有配置相同的受限 ACL。该步骤独立于 Skills 安装，通用安装不创建工作配置。

默认路径以外的配置文件，可通过 `AGENT_TOOLKIT_WORK_CONFIG` 指定；初始化脚本和周报脚本另支持 `--config PATH`，显式参数优先。若在仓库内保存本机配置，应放进已被 Git 忽略的 `.local/` 目录：

```bash
export AGENT_TOOLKIT_WORK_CONFIG="$HOME/.agent-toolkit/.local/work/config.json"
python3 scripts/init_work_config.py --dry-run
python3 scripts/init_work_config.py
```

填充后格式如下，示例中的名称和 ID 均需替换为本机实际值。SLS 初始化模板使用单目标格式，`retention_days` 初始为 `null`（未知）；确认日志库实际保留天数后填写，以下 `3` 仅为示例：

```json
{
  "schema_version": 1,
  "sls": {
    "retention_days": 3,
    "default": {
      "project": "example-project",
      "logstore": "example-logstore"
    },
    "services": {}
  },
  "archery": {
    "scopes": {
      "domestic": "example-domestic-instance",
      "overseas": "example-overseas-instance"
    },
    "modules": {
      "example-module": "example-module-instance"
    }
  },
  "tapd_weekly_report": {
    "workspace_id": "example-workspace",
    "members": ["组员甲", "组员乙", "组长"]
  }
}
```

- SLS 单目标无需填写环境；多环境放在 `sls.scopes`，可用国内/海外或自定义名称，并用 `default_scope` 显式选择默认环境。`services` 按服务名覆盖目标，未覆盖时使用所选范围的 `default`。旧 `sls.domestic` / `sls.overseas` 继续兼容。完整示例与迁移方式见 [SLS 目标与日志保留期配置](../skills/aliyun-sls-log-query/references/configuration.md)。
- `retention_days` 是对应范围的实际日志保留天数，服务覆盖可单独配置；缺省或 `null` 表示未知，不默认 3 天。入口会裁剪部分过期的窗口，整段已过期时在登录和查询前停止。受限入口单次最多 3 天的窗口限制独立保留。完整显式指定 `--project` 和 `--logstore` 时不读路由配置，保留期未知；受限入口继续禁止自选目标。
- Archery 的 `--scope` 和 `--module` 分别读取 `archery.scopes` 和 `archery.modules`，只选择已配置实例；显式 `--instance` 无需路由配置。服务地址仍放在现有私有凭证的 `base_url` 字段中，缺失时不会使用公共示例地址登录。
- 周报的 `members` 同时决定展示顺序和多人需求归属优先级：取需求负责人中第一个出现在此列表的人。需要组员优先时，把所有组员排在组长之前，并使用 TAPD 中的实际名称；多名组员共同负责时取数组中靠前的一人。不要为了把组长先展示而将其移到首位。新增组员也要加入组长之前，漏配的人不参与归类。完整归属表见 [组长与组员如何配置](../skills/tapd-weekly-report/SKILL.md#组长与组员如何配置)。`--workspace-id`、`TAPD_WORKSPACE_ID` 优先于配置中的工作区。
- 每次执行重新读取配置；修改后下一次命令生效。所用功能缺少必要配置时，在请求外部系统前报错，不回退到源码中的公司环境值。周报 `--dry-run` 仅推导版本日期和路径，无需真实配置。
- JumpServer 的控制节点资产在其私有凭证的 `master_asset` 中配置；`exec --asset` 可直接指定独立执行目标，不覆盖 Kubernetes 控制节点配置。公共文档不记录本机资产拓扑。
- `TAPD_TOKEN` 继续通过环境变量提供；SLS 凭证和 Session 继续由现有私有目录管理，不放进模板。使用 SLS 前仍需准备公共运行时及登录配置。

现有机器升级时，应先把原来的实际路由、工作区和成员优先顺序填入私有配置，再使用新入口；初始化空模板不会自动恢复旧值。

## 工作环境协作规则

规则原文件保存在 [`codex/instructions/work.md`](../codex/instructions/work.md)，由本仓库统一维护版本。
源文件使用普通 Markdown 文件名，避免被 Agent 当作工具箱仓库的 `AGENTS.md` 自动加载。
这份规则适用于 `~/data/work` 的工作环境，引用的工作区文档和 Skills 需要在使用环境中另行准备。

以下以该工作区为例，通过软链接保留加载入口：

```text
~/data/work/.codex/AGENTS.md
  -> ~/.agent-toolkit/codex/instructions/work.md
```

Codex 使用 `~/data/work/.codex` 作为 Codex Home 时，从上述入口加载规则。
`~/data/work/CLAUDE.md` 保持 `@.codex/AGENTS.md` 引用，通过同一入口读取规则；
Claude Code 可能要求批准工作区外的文件导入，迁移后应在新会话中用 `/context` 核对加载结果。
日常修改请直接编辑仓库中的源文件，再检查 Git 差异并提交。

当前安装器不创建这个文件链接，`--agents-only` 只安装子 Agent 定义。
在 macOS/Linux 的新环境中，确认目标入口不存在后可手动建立链接：

```bash
mkdir -p ~/data/work/.codex
ln -s ~/.agent-toolkit/codex/instructions/work.md ~/data/work/.codex/AGENTS.md
```

如果入口已存在，应先核对并备份原内容，不使用强制覆盖选项。

## 按能力配置

- 测试库：[多实例 profiles 配置](../skills/verify-test-db/references/configuration.md)。
- EasyImage：[PicGo web-uploader 配置](../skills/upload-to-easyimage/references/picgo-config.md)与[线上图文教程](https://www.iminling.com/2025/typora-how-to-use-picgo-upload-image-to-easyimage/)。
- OpenCode：网关、密钥导出、模型档位和服务重启见 [OpenCode 使用说明](opencode.md)。

## 按能力准备外部依赖

以下外部程序只在对应能力中需要，不是基础安装的硬依赖：

- Google Chrome：SLS 和 Archery 自动登录。
- `ripgrep`（`rg`）：`tapd-story` 的仓库检索，以及 `online-issue-triage` 的本地代码定位。
- `curl`（Windows 为 `curl.exe`）：`upload-to-easyimage` 的实际上传请求。
- Node.js（含 `npm` / `npx`）：运行 `local-browser-verify` 所需的 Playwright MCP、首次安装 PicGo Core 与 `web-uploader` 插件，以及维护 `ui-ux-pro-max` 外部 Skill；已有 PicGo 配置的日常上传不需要启动 Node.js 或 PicGo。
- Playwright MCP：`local-browser-verify` 的浏览器驱动；Codex 安装会写入标准 MCP 配置，首次启动时 `npx` 可能联网下载 npm 包。其他 Agent 仍由 Skill 在运行时检查，缺失时先提示并等待用户授权。
- PicGo Core 与 `web-uploader` 插件：仅在首次生成或重建 EasyImage 上传配置时需要；上传脚本日常只读取已生成的配置文件。
- Hugo：`capture-iminling-draft` 的草稿构建验证。
- `rsvg-convert`（librsvg）：`upload-to-easyimage --convert-svg`；不转换 SVG 时不需要。
