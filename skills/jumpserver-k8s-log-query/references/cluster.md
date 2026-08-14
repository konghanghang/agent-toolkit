# JumpServer / K8S 环境对照指南

本文件说明如何建立本机环境对照，不保存实际资产名、IP、账号、版本或故障快照。真实连接信息放在 `JMS_DIR/jms-credentials.json`，项目特有约束放在项目自己的私有文档。

## 先核对实例能力

- 从实际 JumpServer 版本和授权范围确认 API、网页登录和 Web Terminal 是否可用。
- 资产与系统用户从当前账号获授权的接口发现，不照抄其他环境的资产 ID。
- WebSocket 使用配置 `base_url` 或 smart endpoint 的 HTTP(S) 端口；SSH 端口不用于 WebSocket。
- 成功认证须同时获得 API Token 和有效 Web Session。API 成功、HTTP 302、终端回显和远程命令完成是不同证据。

## 本机配置

- `base_url`：用户明确提供的 JumpServer 地址。
- `master_asset`：必须填写已授权的控制节点资产。
- `master_system_user`、`worker_system_user`：按本机权限填写，使用具备所需只读能力的系统用户。
- `default_namespace`、`log_root`：按项目确认；受限入口仍固定在其声明的测试 namespace。

控制节点必须能运行受限的 `kubectl get/describe`。权限不足时停止并报告，不自动改用其他账号、主机或生产环境。

## 节点与日志

日志根目录是否为节点本地盘、共享盘，以及 Pod 到节点资产的对应关系，都应在当前环境确认。历史目录不能证明 Pod 仍在该节点。

固定查询顺序：

1. 用 `jms pods --service <service>` 在控制节点定位 Pod。
2. 读取结果中的 NODE。
3. 用 `jms logs --service <service>` 只连接这些 NODE 对应的 worker。
4. 映射异常时用 `jms nodes` 检查当前授权资产与节点映射。

禁止遍历全部 worker 找目录。没有对应授权资产时报告 `UNMAPPED`；断开、超时或缺少完整结果标记时报告连接失败，不能解释为没有日志。

## 环境边界

测试环境的日志、trace 和 ERROR 按本技能走 JumpServer；生产环境按项目约定使用 SLS。连接失败不构成切换环境或查询来源的授权。既有环境问题应记录在本机或项目私有文档，避免成为公共 Skill 的固定运行结论。
