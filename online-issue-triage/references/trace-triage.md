# 技术 Trace 排查

## 触发

- 用户直接给 trace ID
- 用户问“这是什么报错”
- 用户给出异常栈、慢调用、接口失败日志

## 排查顺序

1. 先确认环境：测试用 `jms`，生产用 `sls`
2. 在本地代码里定位栈帧和分支
3. 用 `archery` 查业务状态验证假设
4. 汇总根因、影响、建议

## 第 1 步：按环境查日志

### 测试环境

使用 `jms`，先从 master 定位 Pod 所在节点，再只读对应 worker 日志：

```bash
jms pods --service {service} --namespace test
jms logs --service {service} --namespace test --tail 200
```

- trace ID、ERROR、调用链不改变测试环境路由
- KoKo 认证失败必须按连接错误处理，不能解释成 `matches=0`
- 禁止遍历全部 worker

### 生产环境

确认是生产环境后，先从共享运行时读取 `TMP_DIR`，通过当前平台的路径 API 生成 `trace.json` 的完整路径并记为 `<TRACE_OUTPUT>`，再确定 SLS 路由：

- 海外服务
  - `sls query --scope overseas --service <service> --trace-id <id> --minutes 60 --size 50 --full --out "<TRACE_OUTPUT>"`
- 国内已配置服务
  - `sls query --service example-api --trace-id <id> --minutes 60 --size 50 --full --out "<TRACE_OUTPUT>"`
- 国内其他服务
  - `sls query --scope domestic --service <service> --trace-id <id> --minutes 60 --size 50 --full --out "<TRACE_OUTPUT>"`

命令中的 `<TRACE_OUTPUT>` 必须替换为实际路径，查询完成后从同一路径读取结果；不要把占位符原样传给命令，也不要把日志结果写入当前项目目录。

看这些信息：

- `ERROR` / `Exception`
- 关键栈帧 `file:line`
- 同一 trace 下的跨服务请求链
- 重试、死信、锁冲突、超时

## 第 2 步：代码定位

不要搜整个工作区。先按服务定位项目，再在该项目里 `rg`。

常见映射参考：

- `references/service-mapping.md`

常见目标：

- NPE 判空缺失
- 分支条件未覆盖
- 幂等 / 锁 / 并发校验
- MQ 消费后的异常处理

## 第 3 步：Archery 验证

先从项目和本机配置确认环境与实例，再用授权元数据入口发现实际数据库：

```bash
archery list-instances-safe
archery list-databases-safe --instance "<已确认实例>"
```

确认库、表与租户范围后，遵守生产行数据查询的确认要求，再验证业务对象状态、失败记录和关联字段。

## 常见结论

- 日志里直接抛异常，代码缺判空或命中异常分支
- 同一条消息多次重试，最终死信
- 数据状态本身异常，导致代码校验失败
- 实际是路由查错了 project / instance
