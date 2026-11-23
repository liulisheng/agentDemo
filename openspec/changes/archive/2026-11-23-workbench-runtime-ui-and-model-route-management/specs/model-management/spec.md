# Spec Delta

## ADDED Requirements

### Requirement: 默认运行环境页按已确认原型呈现独立设置

系统 SHALL 在模型管理页的“默认运行环境”页签并列呈现作用域默认表单和 ExecutionProfile 版本目录，并分别展示引擎与模型的有效值、来源和保存状态；窄屏下保持该顺序并纵向排列。

#### Scenario: 管理员打开默认运行环境
- **WHEN** 租户管理员打开模型管理的“默认运行环境”页签
- **THEN** 页面显示个人/租户默认、EngineProfile 与 ModelRoute 独立选择、当前解析来源和 Profile 版本目录，不把 ModelRoute 合并进 Profile

#### Scenario: 成员查看全局默认
- **WHEN** 普通成员打开“默认运行环境”页签
- **THEN** 成员可保存自己的引擎和模型默认；租户默认可查看但不可编辑，也不显示租户保存操作

#### Scenario: 目录为空
- **WHEN** 当前租户没有可用 ExecutionProfile 或 ModelRoute
- **THEN** 页面分别显示空状态和对应管理入口，不显示不可提交的伪选项

#### Scenario: 默认目录加载失败
- **WHEN** EngineProvider、Profile、ModelRoute 或默认绑定读取失败
- **THEN** 页面显示可区分的读取错误与重试入口，且不覆盖用户当前未保存的选择

### Requirement: ModelRoute 页提供租户路由管理操作

系统 SHALL 在“模型路由”页签提供可发现的创建入口，并为授权管理员提供创建、编辑和逻辑删除 ModelRoute 的操作；成员只读。

#### Scenario: 管理员从路由页创建路由
- **WHEN** 租户管理员在 ModelRoute 页点击“创建路由”并选择当前租户已启用连接及目录中的可用模型
- **THEN** 系统创建带别名和非敏感参数的路由，并在当前路由表中显示其版本和状态

#### Scenario: 创建时没有可用连接或模型
- **WHEN** 租户没有已启用连接或所选连接没有 ACTIVE 目录模型
- **THEN** 创建表单说明缺少的依赖并提供前往连接/模型目录的入口，不允许提交无效路由

#### Scenario: 管理员编辑路由
- **WHEN** 租户管理员在路由行选择“编辑”并保存有效的别名、目录模型或非敏感参数
- **THEN** 系统保存新路由版本，刷新路由表并继续固定已经创建的 RunSnapshot

#### Scenario: 管理员删除路由
- **WHEN** 租户管理员在路由行确认“删除”
- **THEN** 系统按逻辑删除处理并显示已删除状态；删除失败时保留原路由并显示依赖冲突

#### Scenario: 成员打开路由页
- **WHEN** 普通租户成员打开 ModelRoute 页
- **THEN** 成员可以查看连接和路由信息，但不能创建、编辑、停用、启用或删除路由
