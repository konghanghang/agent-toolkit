# 维护指南

[返回首页](../README.md) · [安装指南](installation.md) · [开发工作流](workflows.md)

## 目录职责

| 目录 | 职责 |
|---|---|
| `skills/` | Codex、Claude Code 共用的 Skills，每个目录必须包含 `SKILL.md` |
| `codex/` | Codex custom Agents、MCP 模板、全局 Rules 与工作协作规则源文件 |
| `opencode/` | OpenCode 配置与 custom Agents；网关和密钥使用 `{env:NAME}` 引用 |
| `claude/` | Claude Code 专属配置占位，没有真实配置前保持为空 |
| `common/` | 工作公共运行时、入口脚本和命令模板，不作为 Skill 安装 |
| `scripts/` | 工具箱安装、校验和维护脚本 |
| `tests/` | 工具箱回归测试 |
| `evals/` | 行为评估材料和离线样例，不作为 Skill 安装 |
| `docs/` | 安装、Windows、工作流和维护专题文档 |

共享主体只使用 Codex 和 Claude Code 共同支持的 Skill 最小格式；平台专属内容放入对应平台目录，不混入通用 Skill 主契约。

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

旧的 `SKILLS_ROOT` 只作为 工作运行时兼容别名保留，新文档和新脚本不要继续使用。

推荐 Shell 配置：

```bash
export AGENT_TOOLKIT_ROOT="$HOME/.agent-toolkit"
export PATH="$AGENT_TOOLKIT_ROOT/bin:$PATH"
```

## 通用 Agents

Codex 与 OpenCode 共用以下五类职责，分别使用各平台的配置格式维护。

### 职责列表

- `evidence_explorer`：只读追踪真实调用链、数据边界、行为分支和待确认项，向主 Agent 返回带代码证据的压缩结果，不修改代码或生成最终计划。
- `reviewer`：只读审查当前改动，按严重程度返回正确性、回归、安全和测试缺口，不修改文件。
- `implementer`：按已确认契约完成分配范围的实现及局部验证，不操作 Git 交付或扩大需求。
- `verifier`：执行已授权复现、验收或性能检查，返回版本化行为证据，不修改受审源码和正式测试。
- `design_challenger`：只读检验难以逆转的设计，比较有据可查的候选和兼容代价，不替用户作取舍。

### 功能一致性

`codex/agents/<name>.toml` 与 `opencode/agents/<name>.md` 按同名成对维护，两个目录的 agent 名称集合必须一致。

新增、修改或删除任意一个 agent 时，必须在同一次改动中同步另一边，不能先改一边、把另一边留到以后。两端应完成同一种任务，保持职责、任务边界、证据要求和输出契约一致，并具备完成任务所需的读取、搜索和 Git 检查能力。模型、工具、沙箱和权限使用各自平台的字段，按实际行为核对；一边独有的步数限制或工具限制不能削弱任务能力。

| 功能契约 | Codex | OpenCode |
|---|---|---|
| 目录枚举、内容搜索和定点读取 | 使用父会话允许的工具 | 内置目录、grep 和 read 工具，角色不额外禁用必要读取 |
| Git 差异、状态和历史检查 | 使用父会话允许的命令，不执行 Git 交付 | 只读角色保留 Git 检查；执行角色沿用全局默认规则并拒绝常见 Git 写命令 |
| 探索、审查、设计质疑 | 正文约束只读，实际权限继承父会话 | 拒绝 `edit`，默认拒绝 shell，仅放行 Git 检查 |
| 实现 | 按交接范围修改及验证，实际权限继承父会话 | 编辑和普通运行沿用全局默认规则，不额外增加通配 `allow` |
| 运行验证 | 可生成授权产物，不修改受审源码和正式测试 | 拒绝直接 `edit`，运行沿用全局默认规则，正文保护受审实现 |
| 分工与输出 | 不自行派生；返回范围、版本、证据和缺口 | 同一职责并拒绝 `subagent`，不另设 20 步截断 |

Codex 现行官方契约规定自定义角色继承父会话的沙箱、审批、provider 和 MCP，角色文件中的 `sandbox_mode` 不覆盖父配置。本机 0.163.0 尚未逐字段实测，故只读角色保留旧 `sandbox_mode = "read-only"` 标记用于兼容，但不将其视为已生效的独立沙箱证据。需要系统级只读保证时，应在父会话选择相应权限，不能把角色正文当成独立隔离。OpenCode V2 子角色使用自身权限，不是父角色权限的子集；全局规则后追加角色规则，按最后匹配生效，agent 规则可覆盖 global 规则；新执行角色不添加 `edit` 或 `shell` 的通配允许。即便 `edit` 被拒绝，允许的 shell 仍可能间接写文件，常见 Git 命令拒绝也不是任意程序的行为证明。功能验收应按角色核对读取、搜索、运行、直接工具限制和范围遵守情况，并分别说明配置、原生加载、模型行为与系统隔离的覆盖。依据：[Codex Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents)、[OpenCode V2 Agents](https://opencode.ai/v2/docs/agents)、[OpenCode V2 Permissions](https://opencode.ai/v2/docs/permissions)。

新角色省略模型配置，使用各平台的父会话或默认模型；现有 `evidence_explorer` 和 `reviewer` 的显式模型配置保留。角色定义不会自行启动工作；[通用流程的 Agent 分工规则](../skills/feature-delivery/references/agent-orchestration.md) 规定触发条件，满足条件时主 Agent 应主动委派。小任务不启动全套角色。

提交前核对名称集合、成对任务指令及只读能力，运行 `tests/test_agent_pairs.py` 对应的仓库测试，并同步本节的职责列表。测试覆盖配置契约；真实模型任务验收需另外执行。

### 新增 Codex Agent

新的 Codex custom Agent 必须：

1. 放入 `codex/agents/<agent-name>.toml`。
2. 至少定义 `name`、`description` 和 `developer_instructions`。
3. 使用清晰、稳定、项目无关的职责边界；项目特定规则继续放在项目 `AGENTS.md`。
4. 实际权限遵守父会话；只读角色可保留 `sandbox_mode = "read-only"` 兼容标记，但不得把它当作生效证明或用角色配置请求更宽权限。正文按职责明确只读、文件归属或验证产物范围。
5. 不写入凭证、个人机器绝对路径或某个项目独有的业务口径。
6. 同一次改动中按 [功能一致性](#功能一致性)同步 `opencode/agents/<agent-name>.md`。
7. 完成后运行安装器单元测试，以及默认和自定义 Codex Home 的 `--agents-only --dry-run`。

### 新增 OpenCode Agent

新的 OpenCode custom Agent 必须：

1. 放入 `opencode/agents/<agent-name>.md`。
2. 使用 YAML frontmatter，至少定义 `description` 和 `mode`，正文作为 agent 指令。
3. 按职责配置工具：只读角色拒绝 `edit` 并只放行 Git 检查；执行角色沿用全局默认与自身规则，不用通配 `allow` 放大编辑或运行权限；验证角色另拒绝直接编辑。所有角色禁止自行派生及执行 Git 交付。
4. 模型、网关和密钥使用可移植引用；不要写入真实 URL、API Key 或个人机器绝对路径。
5. 同一次改动中按 [功能一致性](#功能一致性)同步 `codex/agents/<agent-name>.toml`。
6. 完成后运行安装器单元测试，以及 `--opencode-only --dry-run`。

## Codex Rules

- `work-readonly.rules`：允许工作环境下的登录、受限 SLS 查询、Archery 元数据查询、固定测试命名空间日志查询，以及测试库元数据、受限查询、脱敏验证和只生成计划的写入准备；生产 SQL、测试库实际写入、任意 SLS 查询和通用 JMS 命令仍需审批。
- `git-commit.rules`：自动允许从 `origin` 获取更新、通过 `git switch -c` 新建分支，以及受控的 `git add`、新提交和普通主分支推送。
- `github-readonly.rules`：自动允许 `gh pr view/checks`、`gh run list/view`、`gh issue view` 和 `gh repo view`。
- `gitlab-pipeline.rules`：自动允许 `gitlab whoami/search/resolve/inspect/result/trigger` 六个子命令；Rule 只取消 Codex 权限弹窗，`trigger` 仍由 CLI 强制校验 `plan_id`、用户确认、计划漂移和生产环境授权。

Rules 的 `allow` 只处理命令越过沙箱的权限，不替代 Skill、`AGENTS.md` 或用户指令中的操作边界。

## 新增 Skill

新的共享 Skill 必须：

- 在 `install-profiles.json` 的 `skills` 中登记 `general` 或 `work`，并同步 [README 分类索引](../README.md#能力索引)。通用/个人项目能力归 general，内部服务、工作凭证、工作流程专用能力归 work；通用 Skill 对工作场景的可选说明不构成工作运行时依赖。
- 未登记分类或分类值无效时，安装器拒绝继续。外部 Skill 也必须登记；暂未还原的已登记外部 Skill 可以缺席。
- 新 Rule 同样登记到清单的 `rules`。当前 Agent 目录、OpenCode 配置和 Playwright 配置为通用共享组件；工作专属 Agent/MCP 不能直接加入共享目录或模板，须先扩展安装器隔离机制。

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
- Prometheus、Nacos 等通过 `runtime.json` 管理的内部服务地址
- `~/.local/agent-toolkit/work/runtime.json`
- 工作区绝对路径

例如 `capture-iminling-draft` 的目标仓库路径应在每台机器单独设置：

```bash
git config --global iminling.repoPath /absolute/path/to/iminling-pages
```

OpenCode 的网关和密钥来自运行 OpenCode 服务的进程环境：`NEWAPI_BASE_URL`、`GROK_API_KEY`、`NEWAPI_GEMINI_API_KEY`、`NEWAPI_CODEX_API_KEY`、`NEWAPI_ZAI_API_KEY`。变量缺失时 `{env:NAME}` 会原样保留，请求不会改用另一台机器的地址。修改这些变量后需要重启 OpenCode 服务。

## 更新受管组件

先按 [日常更新](../README.md#日常更新)拉取仓库，再处理需要重新安装的组件。

Skill、Codex Agent 和 OpenCode 的目录或文件链接会立即读取仓库内更新。Windows 的 OpenCode 配置副本和 Rules 都需要拉取后重新运行安装器才能更新。OpenCode 配置变更后需要 `opencode reload` 或重启服务。更新 Rules：

```bash
python3 ~/.agent-toolkit/scripts/install.py --rules-only
```

Windows 的配置副本更新命令见 [Windows 指南](windows.md#更新)。

Agent 或 Rule 更新后，新开会话或重启客户端使其生效。

## 外部 Skill 更新

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

## 验证与提交检查

提交前按改动的影响范围检查。未受影响、且内容和相关环境未变化的项目可复用有效结果，注明对应版本与覆盖范围；未执行、跳过和真实环境验收分别报告。安装器改动仍须检查 general、work 两个 profile。

- Skill 或安装清单改动：校验相关 Skill 格式和显式分类；安装器改动校验所有可安装 Skill。
- 安装器改动：覆盖各平台的链接权限、路径表示、配置保护和重复安装行为；两个 profile 的 dry-run 无未知冲突，Codex 与 Claude Code 的 Skill 目录链接目标正确。
- Agent 改动：核对两端名称、任务与输出契约，以及各角色的读取、搜索、运行、编辑与交付限制；检查默认和自定义 Codex Home 的 Agent 目录链接，以及 OpenCode Agent 目录链接。
- OpenCode 配置或安装逻辑改动：macOS/Linux 检查配置文件链接目标；Windows 检查受管副本内容与安装记录摘要。仓库内只有网关和密钥的环境变量引用。
- Rules 改动或安装迁移：确认受管 Rules 为与源文件一致的真实文件，执行 `codex execpolicy check --rules <文件> <命令及参数>`，检查代表性的允许与未放行命令；复用结果时明确记录来源。
- 路径或迁移改动：除迁移兼容说明外，没有旧 `~/.shared-skills` 路径残留。
- 每次提交：审查完整任务差异，确认没有凭证和机器相关运行时文件进入 Git，记录验证版本与范围。

使用已准备的项目解释器运行仓库测试；Windows 写法见 [Windows 指南](windows.md#测试与维护)：

```bash
~/.agent-toolkit/.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
```

### 工作流行为评估

修改交付流程或 Agent 契约时，可用 [工作流行为评估](../evals/workflow/README.md) 在临时目录准备可复用场景，检查授权延续、完整审查、证据分层、操作范围、事实查证和重复错误约束。先运行 `python evals/workflow/prepare.py`，再按说明在新会话执行并核对实际轨迹。

准备脚本不启动模型或真实浏览器。格式校验、离线样例测试、真实模型行为观察和真实环境验收分别报告；行为评估不是所有小改动的强制门禁。
