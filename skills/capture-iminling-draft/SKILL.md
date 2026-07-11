---
name: capture-iminling-draft
description: '将当前对话中值得长期沉淀的问题排查、解决方案、技术知识、概念理解、操作经验、工具对比、技术决策或文章灵感，整理为 iminling-pages 仓库中的 Hugo 草稿文章。适用于“把刚才的内容记录成文章”“把这个知识点写下来”“新建一篇博客草稿”“记录这个排查过程”“把这次讨论整理成文章”或从任意项目把当前上下文投递到 iminling 博客等任务；根据内容类型选择正文结构，遵守目标仓库的写作和 URL 规范，默认保持 draft: true，不负责封面、上传、提交或发布。'
---

# 捕获 iminling 文章草稿

把当前对话中已经形成的知识、证据和思路写入 `iminling-pages`，而不是在当前工作项目中创建文章。使用 [scripts/resolve_iminling_repo.py](scripts/resolve_iminling_repo.py) 定位目标仓库；确定内容类型后读取 [references/article-shapes.md](references/article-shapes.md) 选择正文结构。

## 边界

- 只创建或完善 `draft: true` 的文章，并执行草稿构建验证。
- 不生成封面、不上传图片、不将 `draft` 改为 `false`，不提交或推送。
- 不修改当前对话所在的其他项目；所有文章改动只写入解析出的 `iminling-pages` 仓库。
- 不覆盖已有文章。发现同名文件、相同 `slug`、相同 `url` 或明显重复主题时，先检查已有草稿，再决定补充已有草稿或使用更准确的新名称；无法安全判断时询问用户。
- 不把推测写成事实。缺少证据、需要联网核实或尚未验证的内容使用 Markdown 注释标记 `TODO`。
- 不写入 Token、Cookie、Session、密码、私有邮箱、真实内网地址或其他敏感信息。

## 记录深度

根据用户表达选择，不机械追问：

- “先记录一下”“别丢了这个想法”“留个草稿”：快速捕获，保存核心知识、证据、提纲和待验证项。
- “写成文章”“整理成完整草稿”：完整初稿，生成可连续阅读的正文、示例、边界和总结。
- 表达不明确时，默认生成可继续编辑的完整初稿，但只扩写当前对话能够支持的内容。

## 工作流

### 1. 定位目标仓库

运行：

```bash
IMINLING_REPO="$(python3 "$AGENT_TOOLKIT_ROOT/skills/capture-iminling-draft/scripts/resolve_iminling_repo.py")"
```

脚本按以下顺序解析：

1. 显式 `--repo` 参数
2. `IMINLING_PAGES_REPO` 环境变量
3. 全局 Git 配置 `iminling.repoPath`
4. 当前目录及其父目录

首次使用且无法定位时，让用户任选一种方式配置：

```bash
export IMINLING_PAGES_REPO=/absolute/path/to/iminling-pages
```

或：

```bash
git config --global iminling.repoPath /absolute/path/to/iminling-pages
```

如果运行环境限制跨工作区写入，按平台正常权限流程申请目标仓库写入权限，不要退回到当前项目创建文件。

### 2. 读取目标项目规则

至少读取目标仓库中的：

- `AGENTS.md`
- `docs/README.md`
- `docs/reference/article-writing.md`
- `docs/reference/url-naming.md`

目标仓库规则是文章格式、分类、URL 和验证命令的事实源；与本 skill 冲突时以目标仓库为准。开始编辑前检查目标仓库 `git status --short --branch`，保留已有改动，不暂存、不清理。

### 3. 提炼当前对话

只提取与文章主题有关的内容：

- 为什么值得记录
- 已确认的概念、机制或结论
- 实际命令、错误信息、配置和验证结果
- 关键推理、方案权衡和适用边界
- 可以迁移到其他场景的经验
- 尚未确认的疑问

去掉对话式寒暄、重复试错和与主题无关的仓库细节。保留对读者理解因果关系有帮助的失败路径，不把聊天记录直接拼接成正文。

### 4. 选择文章形态

读取 `references/article-shapes.md`，在概念学习、技术机制、故障排查、操作教程、工具对比、技术决策、经验总结或灵感提纲中选择最符合内容的一种。可以组合相邻形态，但不要强行套用“问题—解决方案”。

### 5. 规划 Front Matter 和文件位置

确定：

- 中文标题
- 英文小写 kebab-case 文件名
- `slug` 或 `url`，两者只保留一个
- `description`
- 现有 `categories`
- 精简且拼写一致的 `tags`
- `content/post/<category>/<filename>.md` 目标路径

创建前搜索目标仓库中的文件名、标题、`slug` 和 `url`，避免重复。独立文章通常使用 `slug`；系列、专题或需要脱离默认 permalink 的文章按目标项目规则使用 `url`。

### 6. 创建草稿

使用当前 Agent 可用的安全文件编辑能力直接创建 Markdown，不依赖特定平台的私有编辑命令。Front Matter 至少包含目标项目要求的完整字段，并保持：

```yaml
image: https://images.iminling.com/app/hide.php?key=TODO-replace-with-cover
draft: true
```

正文要求：

- 不手写目录、更新时间、作者信息或相关文章推荐章节。
- 不为凑篇幅重复解释，不编造对话中没有的测试结果。
- 命令、路径、字段、错误信息和产品名保留原文，但替换敏感值。
- 快速捕获模式可以使用 Markdown 注释记录待办；完整初稿应具备自然开头、主体、边界和总结。
- 如果当前信息只够形成提纲，就明确生成提纲草稿，不伪装成已经完成的文章。

### 7. 验证

从任意目录执行草稿构建：

```bash
hugo --source "$IMINLING_REPO" --minify --buildDrafts
```

同时检查：

- 新文件只存在于目标仓库。
- Front Matter 可解析，`url` / `slug` 没有冲突。
- `draft` 仍是 `true`，`image` 仍是明确占位地址。
- 目标仓库没有被意外暂存、提交或生成无关文件。

验证失败时保留草稿状态，修复格式或构建问题后再报告；不要绕过构建错误。

### 8. 交付

向用户报告：

- 新建或更新的文章路径
- 标题、文章形态、分类以及 `slug` / `url`
- 快速捕获还是完整初稿
- 已保留的 `TODO` 或待核实项
- Hugo 草稿构建结果

不要因为草稿创建成功而继续调用封面、上传或 Git 类 skill，除非用户随后明确要求。
