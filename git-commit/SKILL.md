---
name: git-commit
description: 安全地检查、拆分、暂存并提交 Git 变更，并在 GitHub 仓库中预检提交身份与邮箱隐私。适用于“提交”“帮我 commit”“准备提交”“拆分提交”“生成提交信息”、检查 staged diff、排查 GH007 或修复未推送提交邮箱等任务；遵守当前项目规范，不自动 push、amend、强推或覆盖已有改动。
---

# Git 提交

## 确认任务意图

先根据用户请求确定本次操作范围：

- 只要求检查或拆分时，只分析改动，不执行提交。
- 只要求生成提交信息时，只输出提交信息。
- 要求准备提交时，完成检查和暂存后等待确认。
- 明确要求提交时，完成必要检查后执行 `git commit`。
- 未明确要求时，禁止执行 `git push`、`git commit --amend` 或历史改写。

## 读取项目规则

1. 查找并读取当前目录范围内适用的项目协作和提交规范，例如 `AGENTS.md`、`CLAUDE.md`、`CONTRIBUTING.md`、Git commit template 和 commitlint 配置。
2. 遵守当前运行环境的指令优先级和目录作用域。
3. 项目存在明确提交规范时优先遵守；没有明确规范时，参考最近的 Git 历史，沿用现有语言、格式和 scope 习惯。
4. 规则冲突且无法根据指令优先级解决时，停止并询问用户。

## 检查工作区

1. 确认当前目录位于 Git 仓库中。
2. 执行 `git status --short --branch`，分别识别暂存、未暂存和未跟踪文件。
3. 检查暂存区和工作区 diff，理解每项改动的实际语义，不只根据文件名判断。
4. 保留用户已有改动。禁止执行 `git reset --hard`、`git checkout --`、`git clean` 或其他破坏性清理。
5. 发现与当前任务无关但不冲突的改动时，不回滚、不暂存；发现直接冲突或来源不明的异常改动时，停止并询问用户。

## 预检 Git 身份与邮箱隐私

在执行任何新提交前检查实际生效的作者和提交者身份：

```bash
git config --show-origin --get user.name
git config --show-origin --get user.email
git config --show-origin --get commit.gpgsign
git config --show-origin --get user.signingkey
git var GIT_AUTHOR_IDENT
git var GIT_COMMITTER_IDENT
git remote -v
```

如果存在上游分支，再检查最近已被远端接受的身份和所有尚未推送提交：

```bash
git log @{upstream} -1 --format='%an <%ae> | %cn <%ce>'
git log @{upstream}..HEAD --format='%h %an <%ae> | %cn <%ce> | %s'
```

执行时遵守以下规则：

1. 远端是 GitHub，且近期远端提交稳定使用同一个 `users.noreply.github.com` 地址，而当前仓库突然使用不同的私人邮箱时，将其视为高风险身份漂移；在提交前停止并向用户说明。
2. 不要仅凭“不是 noreply 地址”就断言配置错误；公开邮箱也可能是用户的明确选择。优先依据仓库历史、用户指令和实际 `GH007` 错误判断。
3. 普通提交任务中，不静默修改 Git 身份。若用户明确要求修复身份或处理 `GH007`，可以沿用该仓库远端历史中已经被接受的确切 noreply 地址。
4. 只设置仓库级身份，例如 `git config user.email <address>`；除非用户明确要求，禁止修改 `--global` 或系统级配置。
5. 检查 `commit.gpgsign` 和 `user.signingkey`，沿用已有签名策略；禁止为解决邮箱问题关闭或绕过签名。
6. 没有上游分支时，先判断当前分支是否存在对应的 `origin/<branch>`；都不存在时只报告本地身份，不猜测远端惯例。

## 排除不应提交的内容

提交前检查并排除：

- `.env` 和本地配置
- 密钥、Token、密码、验证码、cookie 和 session
- 构建产物、缓存、日志和临时文件
- 与本次提交无关的格式化或重命名
- 用户未要求提交的其他工作区改动

禁止为了让提交变得干净而删除或覆盖这些文件。

## 拆分原子提交

1. 一个提交只表达一个主功能、主修复、主重构或主文档变更。
2. 能够独立理解、验证、回滚或 cherry-pick 的改动，应拆成不同提交。
3. 同一功能对应的代码、测试、配置和文档可以放在同一个提交中。
4. 纯格式化、纯重命名和无关文档调整原则上单独提交。
5. 如果拆分会导致中间提交无法编译、无法测试或行为不完整，可以保持为一个提交。
6. 已有暂存内容不属于当前提交时，禁止擅自取消暂存或混入新提交；先向用户说明。

## 执行必要验证

1. 根据项目规则和本次改动范围选择测试、编译、lint 或文档一致性检查。
2. 优先使用项目文档、现有脚本和 CI 中已经定义的验证入口，禁止猜测不存在的命令。
3. 验证失败时停止提交并报告失败原因，除非用户明确接受风险并且当前项目规则允许继续。
4. 无法执行验证时，明确说明未验证的项目和原因。

## 暂存文件

1. 显式列出并暂存属于当前原子提交的文件。
2. 默认禁止使用 `git add .` 或 `git add -A`，避免夹带无关改动。
3. 同一文件同时包含相关和无关改动时，优先使用安全的局部暂存方式；无法可靠拆分时先询问用户。
4. 暂存后必须执行：

```bash
git diff --cached --name-status
git diff --cached --check
```

5. 必要时检查完整的 staged diff，确认内容、权限变化和删除操作符合预期。

## 生成提交信息

1. 优先使用当前项目明确规定的提交格式。
2. 项目没有明确规范时，参考最近的 commit subject，保持语言和结构一致。
3. 标题简短直接，只描述本次提交做了什么，不写背景、过程、评价或句号。
4. 多个提交应分别生成能够独立表达语义的标题。
5. 用户只要求“生成提交信息”时，最终回复只包含提交信息本身。

## 执行提交

1. 只有用户明确要求提交，或者当前任务已经明确授权提交时，才能执行 `git commit`。
2. 沿用当前 Git 的作者和签名配置，禁止主动修改、关闭或绕过签名设置。
3. 禁止自动执行 `git commit --amend`。只有用户明确要求修改提交，或明确要求处理 `GH007` 且满足下方安全条件时，才允许改写尚未推送的提交。
4. 提交失败时保留现场，报告错误，不通过修改全局 Git 配置强行绕过。

## 推送前检查

用户明确要求推送时，在 `git push` 前执行：

```bash
git status --short --branch
git log @{upstream}..HEAD --format='%h %an <%ae> | %cn <%ce> | %s'
```

确认：

- 工作区状态符合预期；
- 分支没有意外落后或分叉；
- 所有领先提交的 Author 和 Committer 身份都符合仓库惯例；
- 没有私人邮箱、错误机器人身份或混入的其他用户提交；
- 推送目标和分支正确。

如果本机未配置 `gpg.ssh.allowedSignersFile`，`git log --show-signature` 或 `%G?` 可能无法在本地验证 SSH 签名。不要因此直接断言提交未签名；可以用 `git cat-file -p <commit>` 检查是否存在 `gpgsig` 字段，并明确区分“存在签名”和“本地已验证签名”。

## 安全处理 GitHub GH007

当远端明确返回 `GH007: Your push would publish a private email address`，先确认问题来自尚未推送提交中的 Author 或 Committer 邮箱，不要关闭 GitHub 邮箱隐私保护来绕过。

先检查分支关系和待修复范围：

```bash
git status --short --branch
git rev-list --left-right --count @{upstream}...HEAD
git log @{upstream}..HEAD --format='%h %an <%ae> | %cn <%ce> | %s'
```

`git rev-list` 输出的第一个数字是本地落后数量，第二个数字是本地领先数量。只有结果为 `0 N` 且 `N > 0` 时，才满足“只领先、未分叉”的批量恢复前提。

只有同时满足以下条件，才允许重写提交：

1. 用户明确要求处理该推送失败。
2. 工作区和暂存区干净，或现有改动已被安全保留且不会参与重写。
3. 待修复提交全部位于上游分支之后，尚未被远端接受。
4. 当前分支只领先远端，没有落后或分叉。
5. 已从远端历史、GitHub 账户信息或用户指令确认正确的邮箱地址。
6. 已检查领先提交的 Author 和 Committer；不存在需要保留原作者的 cherry-pick、合并提交或他人提交。

推荐恢复流程：

1. 用 `git rev-parse HEAD` 记录重写前的 `HEAD`，并列出上游之后的全部提交。
2. 将正确邮箱写入当前仓库的 `.git/config`，不要修改全局配置。
3. 只有最新一笔未推送提交需要修复时，可在用户授权下执行：

   ```bash
   git commit --amend --no-edit --reset-author
   ```

4. 多笔未推送提交全部属于当前用户、且 Author 与 Committer 都应统一修复时，可在用户授权下从上游之后重放：

   ```bash
   git rebase --exec 'git commit --amend --no-edit --reset-author' @{upstream}
   ```

5. 如果领先提交包含其他作者、cherry-pick、合并提交或不同身份，不使用批量 `--reset-author`；停止并制定逐提交修复方案。
6. 重写后比较旧 `HEAD` 与新 `HEAD` 的文件树，确保内容没有变化：

   ```bash
   git diff --exit-code <old-head> HEAD
   ```

7. 再次检查所有领先提交的 Author、Committer 和 `gpgsig`，确认提交信息、顺序和签名策略符合预期。
8. 因为 GH007 拒绝发生在远端更新之前，用户要求完成或重试推送时，修复后应先尝试普通 `git push`；用户只要求修复提交时，不自动推送。禁止把 `--force` 或 `--force-with-lease` 作为 GH007 的默认解决方案。

以下情况必须停止并请求用户决定：

- 远端已经包含待改写提交；
- 分支落后或已经分叉；
- 正确邮箱无法确认；
- 提交中混有其他作者或需要保留作者身份的历史；
- 重写前后文件树不一致；
- 推送需要强制更新远端。

## 提交后检查

提交完成后执行：

```bash
git status --short --branch
git log -1 --oneline
```

若存在上游分支，再检查所有领先提交的身份：

```bash
git log @{upstream}..HEAD --format='%h %an <%ae> | %cn <%ce> | %s'
```

向用户报告：

- commit ID 和标题
- 是否仍有未提交改动
- 当前分支是否领先远端
- Author 和 Committer 是否符合仓库身份惯例
- 哪些验证已经完成，哪些未执行

除非用户明确要求，否则禁止执行 `git push`。
