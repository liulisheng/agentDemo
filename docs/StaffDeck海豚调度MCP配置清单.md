# StaffDeck 海豚调度 MCP 配置清单

更新时间：2026-11-19

本文依据本机 StaffDeck Open API v1 和 `dolphin_ds` MCP `discover/tools/list` 导出。目标员工是 `小刘`，所用 Skill 与原始编辑路径一致。环境变量仅记录别名名称；值、Token、Cookie 和密码均未导出。完整机器可读 Schema 见[脱敏 JSON 清单](StaffDeck海豚调度MCP配置清单.json)。

## 员工、Skill 与 Stdio Profile

| 对象 | 导出值 |
|---|---|
| 租户 | `tenant_demo` |
| 数字员工 | `小刘` (`agent_ad12b7a68c684c2f`) |
| 已发布通用 Skill | `海豚调度数仓开发` (`dolphinscheduler-dw-skill-2`, `genskill_80e15485ade04366`) |
| MCP Server | `dolphin_ds` (`mcpsrv_ab7353cd91a14456`)，启用；MCP discovery 返回 25 个工具 |
| transport | `stdio` |
| command | `/usr/local/bin/python3` |
| args | `[/data/mcp/dolphinscheduler_tools_server.py]` |
| cwd | `/data/mcp` |
| MCP Profile 环境别名 | `DOLPHINSCHEDULER_BASE_URL`；StaffDeck API 遮蔽该值，因此本清单只保留别名 |
| StaffDeck 容器中存在的 D-S 环境变量名称 | `DOLPHINSCHEDULER_USERNAME`、`DOLPHINSCHEDULER_PASSWORD`、`DOLPHINSCHEDULER_DS_PASSWORD_WAREHOUSE`；值不写入清单 |

目标员工绑定 **20** 个工具；Server 总目录有 **25** 个工具。未绑定给“小刘”的 5 个工具不会进入其 Run allowlist：

- `dolphinscheduler_task_instances_list`
- `dolphinscheduler_task_log_read`
- `dolphinscheduler_workflow_failure_recovery_preview`
- `dolphinscheduler_workflow_instances_list`
- `dolphinscheduler_workflow_recover_failed_tasks`

## 风险、预览和幂等映射

分类依据是 StaffDeck MCP `readOnlyHint`、工具名称和工具说明；`destructiveHint=false` 本身不能把创建或状态变更操作视为只读。所有 WRITE 工具在 StaffDeck 侧要求匹配预览返回的 `confirmation_code`。其 `idempotentHint=false`，远端结果不确定时必须进入 UNKNOWN，不能自动重放。

### READ

全目录 7 项：`dolphinscheduler_projects_list`、`dolphinscheduler_datasources_list`、`dolphinscheduler_workflows_list`、`dolphinscheduler_schedules_list`、`dolphinscheduler_workflow_instances_list`、`dolphinscheduler_task_instances_list`、`dolphinscheduler_task_log_read`。前 4 项绑定给目标员工，后 3 项未绑定。

### PREVIEW

全目录 9 项；均为只读预览，`idempotentHint=true`：

| PREVIEW 工具 | 对应 WRITE 工具 |
|---|---|
| `dolphinscheduler_project_create_preview` | `dolphinscheduler_project_create` |
| `dolphinscheduler_datasource_create_preview` | `dolphinscheduler_datasource_create` |
| `dolphinscheduler_workflow_create_preview` | `dolphinscheduler_workflow_create_sql` |
| `dolphinscheduler_workflow_create_multi_preview` | `dolphinscheduler_workflow_create_multi` |
| `dolphinscheduler_workflow_release_preview` | `dolphinscheduler_workflow_set_release_state` |
| `dolphinscheduler_workflow_run_preview` | `dolphinscheduler_workflow_run_once` |
| `dolphinscheduler_schedule_create_preview` | `dolphinscheduler_schedule_create` |
| `dolphinscheduler_schedule_state_preview` | `dolphinscheduler_schedule_set_state` |
| `dolphinscheduler_workflow_failure_recovery_preview` | `dolphinscheduler_workflow_recover_failed_tasks` |

最后一组失败恢复工具未绑定给目标员工。

### WRITE

全目录 9 项，均为 `idempotentHint=false`：

`dolphinscheduler_project_create`、`dolphinscheduler_datasource_create`、`dolphinscheduler_workflow_create_sql`、`dolphinscheduler_workflow_create_multi`、`dolphinscheduler_workflow_set_release_state`、`dolphinscheduler_workflow_run_once`、`dolphinscheduler_schedule_create`、`dolphinscheduler_schedule_set_state`、`dolphinscheduler_workflow_recover_failed_tasks`。

本 Change 的两节点 ODS→DWD 流程应使用 `dolphinscheduler_workflow_create_multi_preview` 和 `dolphinscheduler_workflow_create_multi`。该 WRITE 工具接受 2–20 个节点，工具说明明确创建结果初始为 OFFLINE，不发布、不调度、不运行；响应不确定时不重试。

## Schema 摘要

MCP 全目录和目标员工绑定子集均按平台算法校验通过：

- 全目录 25 工具：`3598142049835c89ab091fc23bc404ca46aa2f953d693adc40d765a09f446484`
- 目标员工绑定 20 工具：`d88692f5413749580d80daa54461bb4d8cc1c73ed14cd423ddd9cd1698bf802c`

完整输入 JSON Schema、工具说明、`mcp_annotations`、分类和绑定标识保存在[脱敏 JSON 清单](StaffDeck海豚调度MCP配置清单.json)。JSON 中不含 `api_key`、Authorization、D-S 密码或环境变量值。

## Worker 部署边界

StaffDeck 把 MCP 程序放在命名卷 `staffdeck_staffdeck-data` 的 `/data/mcp` 下。`docker-compose.staffdeck-acceptance.yml` 将该目录以 `volume-subpath=mcp,read_only` 只读挂入 Run Worker 与 Approval Worker，不挂载 StaffDeck 完整数据卷。原 Stdio Server 的 `tools/list` 返回 25 项；本机 Run Worker 已完成项目、数据源和工作流只读查询，Approval Worker 在提交人批准后执行对应 WRITE。

本机 Acceptance Worker 使用 `/usr/local/bin/python3`、`/data/mcp` allowlist 和 `env://tenant-a/<环境变量名>` SecretRef。StaffDeck 清单的来源 tenant 是 `tenant_demo`；Control Plane 本机 Fake Identity 的目标租户是 `tenant-a`，本次将脱敏 Profile 和员工绑定导入 `tenant-a`，不跨租户复用身份上下文。不要把凭据值写入 MCP Profile 或文档。

本机 DolphinScheduler acceptance 当前保留了验收资源：项目 `AgentDemo Acceptance`（code `190137316985824`）、MYSQL 数据源 `agentdemo_warehouse`（id `1`）和两节点 `ods_test_user_to_dwd_test_user_acceptance` 工作流（code `190137931221984`）。工作流状态为 OFFLINE，未发布、未调度、未运行；预览审批与创建过程见[验收记录](验证记录.md)。
