# 常见表脑图

> 这里只是排查入口。最终以 DAO / Mapper / 实际 SQL 为准。

## 任务与号码

- `ai_task`
- `ai_task_detail`
- `call_task`
- `ai_number`
- `ai_blacklist`

## 流程

- `flow_data`
- `flow_task_node_data`
- `flow_task_node`

## 短信

- `ai_sms`
- `ai_sms_extend`
- `ai_sms_template`
- `ai_sms_short_url_relation`

## 公司

- `ai_company`
- `ai_company_user`

## 使用原则

- 先确认业务实例，再确认数据库，再查表
- 不要因为脑图里有表名就默认该表一定在当前实例可读
