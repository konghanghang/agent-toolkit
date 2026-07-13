# Agent Toolkit

这个仓库用于维护个人 AI Agent 工具箱，在多台电脑和多个 Agent 环境之间复用同一套工作流。

当前包含：

- Codex 与 Claude Code 共用的 Skills
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
│   ├── online-issue-triage/
│   └── tapd-weekly-report/
├── codex/
│   └── rules/
│       ├── git-commit.rules
│       └── github-readonly.rules
├── claude/
├── common/
└── scripts/
    ├── install.py
    └── update_ui_ux_pro_max.py
```

目录职责：

- `skills/`：Codex、Claude Code 共用的 Skills，每个目录必须包含 `SKILL.md`。
- `codex/`：Codex 专属配置，目前保存可版本控制的全局 Rules。
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
- `PYTHON_BIN`：公共运行时使用的 Python 解释器。
- `WORK_ROOT`：业务工作区根目录。
- `TMP_DIR`：临时文件目录。

旧的 `SKILLS_ROOT` 只作为 work 运行时兼容别名保留，新文档和新脚本不要继续使用。

推荐 Shell 配置：

```bash
export AGENT_TOOLKIT_ROOT="$HOME/.agent-toolkit"
export PATH="$AGENT_TOOLKIT_ROOT/bin:$PATH"
```

## 安装

### 克隆

```bash
git clone git@github.com:konghanghang/agent-toolkit.git ~/.agent-toolkit
```

### 预览安装计划

```bash
python3 ~/.agent-toolkit/scripts/install.py --dry-run
```

### 安装 Codex、Claude Code 和 Codex Rules

```bash
python3 ~/.agent-toolkit/scripts/install.py
```

安装脚本会：

1. 扫描 `skills/*/SKILL.md`。
2. 将 Skill 链接到 `~/.agents/skills/`。
3. 将 Skill 链接到 `~/.claude/skills/`。
4. 将 `codex/rules/*.rules` 链接到 `~/.codex/rules/`。
5. 已有正确软链时跳过。
6. 普通文件、真实目录或未知软链发生冲突时拒绝覆盖。

从旧的 `~/.shared-skills` 迁移时使用：

```bash
python3 ~/.agent-toolkit/scripts/install.py --replace-managed
```

可选参数：

```text
--dry-run          只显示计划，不修改文件
--codex-only       只安装 Codex Skills 和 Rules
--claude-only      只安装 Claude Code Skills
--skill NAME       只处理指定 Skill，可重复使用
--replace-managed  替换指向旧工具箱或旧目录结构的受管软链
```

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

Codex Rules：

```text
~/.codex/rules/<name>.rules
  → ~/.agent-toolkit/codex/rules/<name>.rules
```

保留 `~/.codex/rules/` 为真实目录，不把整个目录替换为软链。这样 Codex 自动生成的 `default.rules` 和版本控制的规则可以共存。

Codex 在启动时加载 Rules；新增或更新规则后，新开会话或重启客户端。

## work 公共运行时

初始化：

```bash
python3 ~/.agent-toolkit/common/bootstrap_work.py
```

初始化会配置：

- `WORK_ROOT`
- `TMP_DIR`
- `SLS_DIR`
- `ARCHERY_DIR`
- `PYTHON_BIN`
- `AGENT_TOOLKIT_ROOT`

并在 `AGENT_TOOLKIT_ROOT/bin/` 生成：

- `sls`
- `archery`
- Windows 对应的 `.cmd` 与 `.ps1` 入口

`~/.local/agent-toolkit/work/runtime.json`、凭证和 Session 都是本机状态，不提交到本仓库。

## 当前 Skills

- `git-commit`
- `upload-to-easyimage`
  - [PicGo web-uploader 配置说明](skills/upload-to-easyimage/references/picgo-config.md)
  - [线上图文教程](https://www.iminling.com/2025/typora-how-to-use-picgo-upload-image-to-easyimage/)
- `capture-iminling-draft`
- `archery-prod-query`
- `aliyun-sls-log-query`
- `online-issue-triage`
- `tapd-weekly-report`

## 当前 Codex Rules

- `git-commit.rules`：自动允许受控的 `git add`、新提交和普通主分支推送。
- `github-readonly.rules`：自动允许 `gh pr view/checks`、`gh run list/view`、`gh issue view` 和 `gh repo view`。

Rules 的 `allow` 只处理命令越过沙箱的权限，不替代 Skill、`AGENTS.md` 或用户指令中的操作边界。

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

软链指向仓库内文件，拉取后不需要重新复制；只有新增或移除 Skill、Rules 时才需要重新运行安装脚本。

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

脚本会使用官方 npm CLI 在临时目录生成 Codex 版本，只提取主 `ui-ux-pro-max`
Skill，完成结构校验和搜索 smoke test 后原子替换共享目录。旧版本保存在 Git 忽略的
`.local/backups/`，版本、来源和内容摘要记录在 `external-skills.lock.json`。

如需安装指定版本：

```bash
python3 ~/.agent-toolkit/scripts/update_ui_ux_pro_max.py --version 2.10.2
```

提交前至少检查：

- 所有 Skill 均能通过格式校验。
- `scripts/install.py --dry-run` 无未知冲突。
- Codex 与 Claude Code 的 Skill 软链目标正确。
- Codex Rules 软链和 `execpolicy check` 正常。
- 除迁移兼容说明外，没有旧 `~/.shared-skills` 路径残留。
- 没有凭证和机器相关运行时文件进入 Git。
