# 服务名参考

## 原则

- 这里只记录常见服务名，不记录本地项目目录
- 不同同事的本地代码摆放位置可能不同，不要把目录结构写死
- 先确认服务名，再在自己的 `WORK_ROOT` 里缩小搜索范围

## 常见服务名

- `sms-consumer`
- `sms-server`
- `ai-flow-server`
- `data-center`
- `call-task`
- `ai-admin`
- `ai-open-api`
- `agentcc`

## 推荐搜索方式

先按服务名在 `WORK_ROOT` 下缩小范围：

```bash
rg -n "sms-consumer|sms-server|ai-flow-server|data-center|call-task|ai-admin|ai-open-api|agentcc" WORK_ROOT
```

如果已经从日志里拿到类名、方法名、异常名，再继续搜更精确的关键词：

```bash
rg -n "DataInconsistencyException" WORK_ROOT
rg -n "handleHangupStatusReport" WORK_ROOT
rg -n "SpringRequestLogFilter" WORK_ROOT
```

## 使用建议

- 先从 SLS 确认服务名
- 再从代码里确认类、方法、分支
- 如果服务归属不清楚，先问清是国内、海外、还是 `ai-open-api`
- 如果代码仓库归属不清楚，不要硬猜目录，先用 `rg` 缩小范围
