# 共享 Skills

这个仓库用于维护一套个人共享 skills，目标是在多个 agent 环境之间复用同一份技能内容，包括：

- Codex
- Claude Code

核心原则是：共享技能只使用两端共同支持的最小格式，把平台私有扩展挡在共享层之外。

## 兼容目标

仓库里的 skill 默认必须同时满足：

- Codex 可发现、可使用
- Claude Code 可发现、可使用

因此共享 skill 不应把某一端的私有元数据或私有机制当作主行为契约。

允许在 skill 目录里放可选的平台边车文件（例如 Codex 的 `agents/openai.yaml`），用来增强该平台下的展示和触发。约束是：

- 主契约始终是 `SKILL.md`，两端都只读 `SKILL.md` 来发现和使用 skill；
- 边车文件只是展示增强，不能成为 skill 的主行为契约；
- 主流程必须只靠 `SKILL.md` + 共享脚本就能跑通，`SKILL.md` 里不能写「见 openai.yaml」之类的依赖；
- Claude Code 会忽略 `agents/openai.yaml`，不得因它的存在或缺失影响 Claude Code 侧行为。

## 仓库结构

```text
shared-skills/
├── README.md
├── .gitignore
├── common/
├── git-commit/
├── upload-to-easyimage/
├── archery-prod-query/
├── aliyun-sls-log-query/
├── online-issue-triage/
└── ...
```

## 最小交付清单

如果只想复制一套可运行的共享技能，最小范围通常是：

```text
shared-skills/
├── README.md
├── .gitignore
├── common/
├── archery-prod-query/
├── aliyun-sls-log-query/
└── online-issue-triage/
```

不需要附带任何本机运行时文件、凭证文件或 session 文件。

## 技能规则

### 1. 基本结构

每个 skill 都应放在独立目录中，并至少包含：

- `SKILL.md`

可选目录：

- `scripts/`
- `references/`
- `assets/`

### 2. `SKILL.md` 格式

每个共享 skill 必须使用 YAML frontmatter，并至少包含：

```yaml
---
name: skill-name
description: 说明这个 skill 在什么场景下使用
---
```

共享 skill 默认只使用公共子集：

- `name`
- `description`

平台专属字段（例如 Codex `agents/openai.yaml` 里的 `display_name`、`short_description`、`default_prompt`）只作为可选边车，不能写进 `SKILL.md` 的 frontmatter，也不能成为主逻辑依赖，详见上方「兼容目标」。

### 3. 运行时术语

统一使用下面这些通用术语：

- `SKILLS_ROOT`
- `PYTHON_BIN`
- `WORK_ROOT`
- `TMP_DIR`

避免写死下面这些内容：

- `CODEX_HOME`
- `~/.codex/bin`
- 个人机器绝对路径
- 本地抓包或调试文件路径
- 凭证、token、session 文件内容

### 4. 敏感信息

禁止提交：

- 凭证
- token
- cookie
- session 文件
- 本地调试抓包
- 机器相关运行时数据

另外，不要把下面这些本机产物一起复制给别人：

- `~/.local/agent-toolkit/work/runtime.json`
- `SLS_DIR/*`
- `ARCHERY_DIR/*`
- 已生成的 `SKILLS_ROOT/bin/sls`
- 已生成的 `SKILLS_ROOT/bin/archery`

## 安装方式

### 1. 克隆仓库

```bash
git clone <repo-url> ~/.shared-skills
```

### 2. 建立单个 skill 软链

`common` 是共享运行时库，不是 skill，**不要**链进 `skills/` 目录；各 skill 通过 `SKILLS_ROOT/common/...` 引用它。只链下面这些 skill 目录：

Codex 示例：

```bash
ln -s ~/.shared-skills/git-commit ~/.codex/skills/git-commit
ln -s ~/.shared-skills/upload-to-easyimage ~/.codex/skills/upload-to-easyimage
ln -s ~/.shared-skills/archery-prod-query ~/.codex/skills/archery-prod-query
ln -s ~/.shared-skills/aliyun-sls-log-query ~/.codex/skills/aliyun-sls-log-query
ln -s ~/.shared-skills/online-issue-triage ~/.codex/skills/online-issue-triage
ln -s ~/.shared-skills/tapd-weekly-report ~/.codex/skills/tapd-weekly-report
```

Claude Code 示例：

```bash
ln -s ~/.shared-skills/git-commit ~/.claude/skills/git-commit
ln -s ~/.shared-skills/upload-to-easyimage ~/.claude/skills/upload-to-easyimage
ln -s ~/.shared-skills/archery-prod-query ~/.claude/skills/archery-prod-query
ln -s ~/.shared-skills/aliyun-sls-log-query ~/.claude/skills/aliyun-sls-log-query
ln -s ~/.shared-skills/online-issue-triage ~/.claude/skills/online-issue-triage
ln -s ~/.shared-skills/tapd-weekly-report ~/.claude/skills/tapd-weekly-report
```

### 3. 初始化运行时

clone 后执行：

```bash
python3 ~/.shared-skills/common/bootstrap_work.py
```

初始化会完成这些事情：

- 写入本机运行时配置
- 创建或校验 Python 环境
- 安装通用依赖
- 在 `SKILLS_ROOT/bin/` 下生成命令入口

初始化时会询问这些值：

1. `WORK_ROOT`
   - 本地工作区根目录
2. `TMP_DIR`
   - 临时目录
3. `SLS_DIR`
   - SLS 凭证和 session 目录
4. `ARCHERY_DIR`
   - Archery 凭证和 session 目录
5. `PYTHON_BIN`
   - 统一 Python 解释器

### 4. 把 `bin/` 加入 PATH

```bash
export PATH="$HOME/.shared-skills/bin:$PATH"
```

## `bin/` 目录说明

`SKILLS_ROOT/bin/` 里放的是可执行入口，例如：

- `sls`
- `archery`

事实源是：

- `common/templates/bin/`

生成物是：

- `bin/`

即使本地 checkout 里已经有 `bin/`，也仍然应该先执行一次初始化脚本。

初始化完成后会生成：

- `~/.local/agent-toolkit/work/runtime.json`
- `SKILLS_ROOT/bin/sls`
- `SKILLS_ROOT/bin/archery`

Windows 下还会生成：

- `SKILLS_ROOT/bin/sls.cmd`
- `SKILLS_ROOT/bin/archery.cmd`
- `SKILLS_ROOT/bin/sls.ps1`
- `SKILLS_ROOT/bin/archery.ps1`

初始化完成后，还需要自己补齐：

- `SLS_DIR/aliyun-credentials.json`
- `ARCHERY_DIR/archery-credentials.json`

## 新增 Skill 规范

新的共享 skill 默认遵守以下规则：

1. 同时兼容 Codex 和 Claude Code
2. 只使用共享 skill 的公共结构
3. 不把平台专属机制当成主接口；可选的平台边车文件（如 Codex 的 `agents/openai.yaml`）只作展示增强，不进 `SKILL.md` 主契约
4. 不写死机器路径
5. 不把敏感信息提交进仓库
6. 示例命令优先使用 `SKILLS_ROOT/...`

## 提交前检查

提交前至少检查：

- `SKILL.md` 是否只使用共享兼容格式
- 是否残留 `CODEX_HOME` 或 `~/.codex/bin`
- 是否残留个人机器路径
- 是否包含凭证或 session
- 命令示例是否统一使用 `SKILLS_ROOT`

## 当前共享 Skills

- `git-commit`
- `upload-to-easyimage`
  - [PicGo web-uploader 配置说明](upload-to-easyimage/references/picgo-config.md)
  - [线上图文教程](https://www.iminling.com/2025/typora-how-to-use-picgo-upload-image-to-easyimage/)
- `archery-prod-query`
- `aliyun-sls-log-query`
- `online-issue-triage`
- `tapd-weekly-report`

`common` 不是 skill，是共享运行时库（bootstrap + runtime + 命令入口模板），供上面这些 skill 通过 `SKILLS_ROOT/common/...` 引用，不单独作为 skill 软链或被发现。

## 验证方式

初始化完成后，先验证命令入口：

```bash
sls --help
archery --help
```

不依赖公共命令入口的 skill 可以直接运行自身脚本，例如：

```bash
python3 "$SKILLS_ROOT/upload-to-easyimage/scripts/upload_easyimage.py" --help
```

如果还没把 `SKILLS_ROOT/bin` 加进 PATH，也可以直接运行：

```bash
<SKILLS_ROOT>/bin/sls --help
<SKILLS_ROOT>/bin/archery --help
```

帮助输出正常后，再执行首次登录：

```bash
sls login
archery login
```

## 常用入口

- 查日志：`sls ...`
- 查数据库：`archery ...`
- 做总排查：`online-issue-triage`
- 检查并提交 Git 变更：`git-commit`

如果命令报“未找到运行时配置”或“skills_root 不一致”，通常说明本机还没初始化，或者共享目录位置已变化。此时重新执行：

```bash
python3 <SKILLS_ROOT>/common/bootstrap_work.py
```
