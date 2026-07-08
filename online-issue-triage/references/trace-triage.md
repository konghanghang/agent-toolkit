# 技术 Trace 排查

## 触发

- 用户直接给 trace ID
- 用户问“这是什么报错”
- 用户给出异常栈、慢调用、接口失败日志

## 排查顺序

1. 用 `sls` 查日志
2. 在本地代码里定位栈帧和分支
3. 用 `archery` 查业务状态验证假设
4. 汇总根因、影响、建议

## Step 1: SLS 查日志

先确定 SLS 路由：

- 海外服务
  - `sls query --scope overseas --service <service> --trace-id <id> --minutes 60 --size 50 --full --out /tmp/trace.json`
- 国内 `ai-open-api`
  - `sls query --service ai-open-api --trace-id <id> --minutes 60 --size 50 --full --out /tmp/trace.json`
- 国内其他服务
  - `sls query --scope domestic --service <service> --trace-id <id> --minutes 60 --size 50 --full --out /tmp/trace.json`

看这些信息：

- `ERROR` / `Exception`
- 关键栈帧 `file:line`
- 同一 trace 下的跨服务请求链
- 重试、死信、锁冲突、超时

## Step 2: 代码定位

不要搜整个工作区。先按服务定位项目，再在该项目里 `rg`。

常见映射参考：

- `references/service-mapping.md`

常见目标：

- NPE 判空缺失
- 分支条件未覆盖
- 幂等 / 锁 / 并发校验
- MQ 消费后的异常处理

## Step 3: Archery 验证

按业务归属选实例：

- 国内大部分传统业务：`main-db`
- 国内新业务（短信、短链、WA、回调、AgentCC）：`polarDB-TiDB`
- 海外业务：`海外生产数据库`

先用：

```bash
archery list-databases --scope domestic
archery list-databases --scope overseas
archery list-databases --module sms
```

确认库后，再查业务对象状态、失败记录、关联表字段。

## 常见结论

- 日志里直接抛异常，代码缺判空或命中异常分支
- 同一条消息多次重试，最终死信
- 数据状态本身异常，导致代码校验失败
- 实际是路由查错了 project / instance
