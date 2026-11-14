# P2 生命周期完善设计

## 意图

把当前只有 `run()` / `close()` 的引擎接口补齐为可验证的生命周期协议，覆盖启动、会话创建与恢复、运行、取消和关闭。Fake 引擎用于离线验证，DeepSeek 适配器只暴露已经能由 SDK 或适配器明确证明的能力。

## 目标

- 冻结引擎无关的生命周期类型、状态和异常。
- 统一 `start()`、`create_session()`、`resume_session()`、`run()`、`cancel()`、`close()` 语义。
- 关闭后拒绝新操作；恢复失败不得偷偷创建新会话；不支持的能力明确报错。
- 保留现有 `EngineRequest`、`EngineResult` 和 CLI 的兼容入口。
- 为后续 Skill 注册和子智能体运行保留父子运行标识、事件和元数据扩展点，但本阶段不实现插件执行器、调度器或持久化。

## 非目标

- 不实现 MySQL 仓储、租约、fencing token 或沙箱调度。
- 不假设 DeepSeek SDK 已支持未验证的恢复、取消或事件 API。
- 不实现完整 Skill/子智能体编排，只冻结可扩展的数据边界。

## 约束

- Python 3.12+，不新增运行时依赖。
- 核心模块不得导入 DeepSeek SDK 类型。
- 生命周期方法必须可重复安全调用：`start()` 和 `close()` 幂等。
- 能力不足时抛出统一的 `UnsupportedCapabilityError`，禁止静默降级。
- 取消结果必须区分已确认、进行中和未知。

## 方案

1. **完整生命周期协议（选定）**：在 `core.py` 增加类型和协议；Fake 实现完整状态机；DeepSeek 适配器以构造期 SDK 初始化兼容 `start()`，对未验证能力显式拒绝。优点是一次冻结后续应用层可依赖的边界，代价是需要更新现有测试和 CLI。
2. **只给现有适配器添加方法**：改动较小，但会把会话、异常和取消语义留在两个适配器里，后续持久化时会再次破坏公共接口，因此不选。

## 选定设计

`HarnessEngine` 公开 `state`、`start`、`create_session`、`resume_session`、`run`、`cancel`、`close`。`SessionRef` 携带引擎名、会话 ID 和可扩展元数据；`EngineRequest` 增加可选 `session`、`run_id`、`parent_run_id` 和元数据字段，同时继续接受旧的 `session_id`。`EngineResult` 增加运行标识、会话引用和终止状态字段，旧字段保持不变。

`HarnessEvent` 使用字符串事件类型和结构化载荷，允许后续增加 Skill 与子智能体事件而不修改核心协议。子智能体只通过 `parent_run_id` 建立父子关系；本阶段不负责创建或调度它们。

Fake 引擎维护内存会话表和活动运行表，支持创建、恢复和取消测试。DeepSeek 引擎使用已有 SDK 调用；SDK 没有经过验证的独立恢复或取消接口时返回统一能力异常，不把传入 `session_id` 当作恢复成功，也不伪造取消成功。

## 验收

- 生命周期顺序、幂等关闭、关闭后拒绝操作和能力拒绝均有单元测试。
- Fake 会话恢复只返回已有会话，未知会话明确失败。
- CLI 在执行前启动引擎，并始终关闭引擎。
- 全部现有离线测试通过，且不需要 API Key。
