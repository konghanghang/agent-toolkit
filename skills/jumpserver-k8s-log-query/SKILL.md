---
name: jumpserver-k8s-log-query
description: 经 JumpServer 只读查询 测试环境 K8S 日志。先在 master 用 kubectl 定位服务所在 NODE，再只到对应 worker 读取 /data/logs/{namespace}/{service}/，禁止遍历全部 worker。用户提到测试环境日志、JMS、JumpServer、KoKo、test namespace、worker 日志、/data/logs/test、Pod 所在节点，或明确要求测试环境不用 SLS 时使用；即使给了 trace ID、ERROR 或调用链，只要环境是测试环境也应使用本技能。
---

# JumpServer K8S 宿主机日志查询

## 路由

- 测试环境：使用本技能走 JMS；trace ID、ERROR、调用链不改变环境路由。
- 生产环境：使用 `aliyun-sls-log-query`。
- 环境不明确：先确认环境，不因 trace ID 默认查生产 SLS。

本技能读取 worker 本地盘文件，不读取容器 stdout 或 SLS 索引。

## 准备

仅当运行时配置不存在时，在交互终端初始化：

```bash
python3 "$AGENT_TOOLKIT_ROOT/common/bootstrap_work.py"
```

初始化脚本需要 stdin；已有配置时不要重复运行。使用运行时中的 `PYTHON_BIN`、`AGENT_TOOLKIT_ROOT` 和 `JMS_DIR`。凭证、Token、Cookie、密码只保存在 `JMS_DIR`，禁止写入仓库或回复。

推荐入口：

```bash
jms login
jms pods-safe --service example-flow-service
jms logs-safe --service example-flow-service --tail 120
jms nodes
```

Windows PowerShell 中优先调用初始化生成的公共 `.cmd` 入口，不直接拼接底层 Python 脚本：

```powershell
$AgentToolkitRoot = (Resolve-Path (Join-Path $HOME ".agent-toolkit")).Path
& "$AgentToolkitRoot\bin\jms.cmd" login
& "$AgentToolkitRoot\bin\jms.cmd" logs-safe --service example-flow-service --tail 120
```

Codex 日常排查优先使用 `pods-safe` / `logs-safe`：两者固定 `test` namespace，校验服务名，且 `logs-safe` 不接受任意文件路径。只有确实需要其他 namespace、指定文件或通用只读命令时才使用原始 `pods` / `logs` / `exec`，并保留审批。

凭证文件：`JMS_DIR/jms-credentials.json`。使用 `nodes`、`pods`、`logs` 或按节点执行时，必须填写 `master_asset`；单独登录和显式按资产执行不要求控制节点配置。脚本在读取凭证和写入 session 前会收紧本机权限：macOS/Linux 使用 `0600` 文件与 `0700` 目录，Windows 使用关闭继承的受保护 ACL，只保留当前用户、`SYSTEM` 和内置管理员：

```json
{
  "base_url": "http://<jumpserver-host>",
  "username": "<username>",
  "password": "<password>",
  "org_id": "<已授权组织 ID>",
  "master_asset": "<已授权控制节点资产>",
  "master_system_user": "root",
  "worker_system_user": "root",
  "default_namespace": "test",
  "log_root": "/data/logs/{namespace}"
}
```

脚本使用 JumpServer 网页登录和 `JMS-KOKO` WebSocket，不依赖 Chrome、`expect` 或本机 `ssh`。密码按 JumpServer 网页协议加密；bootstrap 默认安装 Python `cryptography`，本机 `openssl` 只作为兼容兜底，禁止降级为明文提交。

## 真实执行路径

1. `/core/auth/login/` 建立 Django Web Session。
2. `/api/v1/authentication/auth/` 获取 Bearer Token，用于资产与系统用户 API。
3. `/api/v1/terminal/endpoints/smart/` 获取真实终端端点。
4. `/koko/terminal/` 初始化终端。
5. `/koko/ws/terminal/` 以 `JMS-KOKO` 子协议执行只读命令。

smart endpoint 的 `http_port` / `https_port` 为 `0` 时表示未提供 Web 端口，脚本回退到 `base_url` 的显式端口或 HTTP(S) 默认端口。`22` / `2222` 按 SSH 端口处理，不用于 WebSocket；若 `base_url` 也落到 SSH 端口则直接失败。

`jms login` 必须同时拿到有效 API Token 和 Web Session。API 登录成功但 Web Session 无效时不得继续；WebSocket 302 到登录页属于认证失败，不得解释成 `matches=0`。

本技能不自动回退到 KoKo SSH 或反复尝试一次性口令。终端兼容性与认证失败按当前环境证据报告，不能把某个部署的故障当作所有实例的固定结论。

## 查询流程

给出服务名后必须先 master、再目标 worker。禁止扫描全部 worker。

### 1. 在 master 定位 Pod

```bash
jms pods --service <service> [--namespace test]
```

控制节点使用本机配置的 `master_system_user`，必须具备所需的只读 `kubectl` 权限。权限不足时停止，不自动切换账号或主机。

匹配规则：

- 优先匹配 `app.kubernetes.io/name` 或 `app` 标签。
- 否则匹配完整服务名或 Deployment 副本后缀。
- `example-admin` 不得误匹配 `example-admin-ui`。

只有 master 命令收到完整结果标记且远程退出码为 0 后，`matches=0` 才表示没有匹配 Pod。连接中断、超时、缺少结果标记一律按失败处理。

### 2. 只读目标 worker 日志

```bash
jms logs --service <service>
jms logs --service <service> --tail 200
jms logs --service <service> --file app.log
```

脚本必须：

1. 用 master 的 kubectl 确认 Pod → NODE。
2. 用节点 InternalIP 映射 JumpServer 资产。
3. 只连接这些 NODE 对应的 worker。
4. 读取 `/data/logs/<namespace>/<service>/`，列目录并 tail 较新的日志文件。

### 3. 必要时查看映射或执行只读命令

```bash
jms nodes
jms exec --node <已确认节点> --command 'ls -lah /data/logs/test'
jms exec --asset <已授权资产> --system-user <已授权系统用户> --command 'kubectl get pods -n test'
```

`exec` 仅允许单条只读命令或只读管道，不允许换行、重定向、命令替换、控制语句或其他 shell 展开。禁止改文件、`rm`、编辑器、重启以及 `kubectl apply/delete/exec/edit`。`service`、`namespace` 和 `file` 只接受不含路径分隔符的安全名称。

## 硬约束

1. 禁止扫描全部 worker；服务查询必须先用 master 定位 NODE。
2. `/data/logs/test` 是节点本地盘，不是 NFS；残留目录不能证明 Pod 当前在该节点。
3. 只读；不改配置、不重启、不进入容器、不安装软件。
4. 不回显或记录密码、Bearer Token、Cookie、Web Session；应用日志中的常见认证头也由 CLI 统一脱敏。
5. 不自动回退 KoKo SSH `:2222`，也不因 JMS 失败改查 SLS。
6. Web Terminal 缺少完整随机结果标记或远程退出码时必须失败，禁止把终端回显当成功。

## 故障处理

| 现象 | 处理 |
|---|---|
| 找不到凭证文件 | 创建 `JMS_DIR/jms-credentials.json` 并设为 `0600` |
| API 登录失败 | 核对账号、密码和组织；不要切换到 SLS |
| 网页登录仍停留在登录页 | 检查账号状态与登录策略；不复用 API 登录 Cookie |
| WebSocket 返回 302/login | Web Session 失效，重新 `jms login` |
| WebSocket 收到 `SSH-` banner | WebSocket 连到了 SSH 端口；检查 `base_url` 和 smart endpoint 的 HTTP(S) 端口 |
| 配置的控制节点或系统用户不可用 | 停止；不自动更换账号或资产 |
| Web Terminal 断开或缺结果标记 | 按连接失败处理，不输出 `matches=0` |
| 找不到资产或 `UNMAPPED` | 用 `jms nodes` 检查授权资产与节点映射 |
| `MISSING_DIR` | 以 kubectl NODE 为准；该节点当前无对应目录 |
| 拒绝非只读命令 | 改用 `ls`、`tail`、`grep`、`kubectl get/describe` |

环境对照与能力确认方法见 [references/cluster.md](references/cluster.md)。

## 资源

- `$AGENT_TOOLKIT_ROOT/bin/jms`：短命令入口。
- `scripts/jms_ops.py`：登录、WebSocket 执行、Pod 定位与日志读取。
- `references/cluster.md`：本机 JumpServer/K8S 对照指南。
