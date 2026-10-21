# 身份、隐私与签名

主流程缺少有效证据或身份／签名上下文变化时读取。本节是预检的补充，不在每次提交时重新执行全部命令。

## 一次性核对实际身份

优先检查 Git 实际采用的身份，而非只看配置文件；身份环境变量也可能覆盖配置：

```bash
git var GIT_AUTHOR_IDENT
git var GIT_COMMITTER_IDENT
```

有异常或缺少解释时，再在同一次只读检查补充必要项：

```bash
git config --show-origin --get user.name
git config --show-origin --get user.email
git config --get commit.gpgsign
git config --get user.signingkey
git remote -v
```

没有配置值时 `git config --get` 可返回非零，不等于检查失败。不要用 `&&` 或 `set -e` 中断其余独立检查。remote URL 含凭证时隐藏凭证，不原样输出。

只提交时可使用已知 remote 与已有本地 tracking 历史判断身份惯例，不为此默认 fetch。GitHub 仓库近期已知提交稳定使用同一 `users.noreply.github.com` 地址，而本次突然采用不同私人邮箱时先停止说明；不因邮箱不是 noreply 就认定配置错误。没有可靠历史时说明核对边界，不猜其他远端或擅自修改身份。

尚未推送提交的完整身份集合在 push 预检中核对一次；只 commit 时不重复扫描它们。无 upstream 不影响创建本地提交。

## 仅依据最近提交决定例行签名

无历史仓库使用用户或项目明确策略；仍无法判断时询问。存在 HEAD 时检查实际提交对象的头部是否含 `gpgsig`，可输出布尔结论，不必打印整段签名或历史：

```bash
git rev-parse --verify HEAD
git cat-file -p HEAD
```

- 最近一笔带签名：本次使用签名；`commit.gpgsign=false` 时显式 `git commit -S`。没有可用签名配置时停止，不无签名降级。
- 最近一笔不带签名：本次保持无签名；`commit.gpgsign=true` 时显式 `git commit --no-gpg-sign`，不修改配置。
- `commit.gpgsign` 和 `user.signingkey` 只描述默认行为与能力，不证明仓库惯例。项目或用户明确策略优先于上述默认。
- SSH 签名缺少 `gpg.ssh.allowedSignersFile` 时，本地验证可失败；提交对象含 `gpgsig` 证明存在签名，不等于本地已验证其有效性。两种结论分开报告。

提交后只检查本次新提交的 Author、Committer 和签名是否符合预检策略。同一仓库连续提交且相关上下文未变时复用有效结论，不复用其他仓库的身份设置。

## 同步重写的签名

推送前受控 rebase 需要保留待重写提交原签名约定，不能只在结束后检查最后一个提交。先记录每个待重写提交是否带签名：

- 全部已签名：使用显式 `--gpg-sign`，结束后确认每个重写提交仍有签名。
- 全部未签名：使用显式 `--no-gpg-sign`，避免配置默认行为改变约定。
- 混合签名、身份或签名策略不明确：停止自动同步，不能统一重签或静默丢失签名。

签名不可用时保留现场并说明，不自动 amend 或改写已发布历史。具体同步边界见 [推送同步](push-sync.md)。
