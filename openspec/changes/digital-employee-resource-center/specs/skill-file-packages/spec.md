# Spec Delta

## Purpose

让租户管理员在 Skill 草稿中维护版本化文件包，通过安全的 ZIP 导入或在线文件操作编辑内容，并使发布和运行固定到可校验的文件清单。

## ADDED Requirements

### Requirement: Skill 支持内联和文件包正文

系统 SHALL 支持 INLINE 与 FILE_PACKAGE 两种 Skill 内容模式，并在文件包版本中保存入口路径和规范化文件清单摘要。

#### Scenario: 保存内联 Skill
- **WHEN** 管理员创建或编辑 INLINE Skill 草稿
- **THEN** 系统使用 `instruction` 作为 Skill 主指令并保持现有行为

#### Scenario: 保存文件包 Skill
- **WHEN** 管理员选择 FILE_PACKAGE 模式并保存草稿
- **THEN** 系统将根目录 `SKILL.md` 固定为唯一入口，并保存文件清单摘要

### Requirement: Skill 文件只能在草稿版本中修改

系统 SHALL 在 DRAFT Skill 版本中允许查看、创建、编辑、重命名和删除文件；PUBLISHED、DEPRECATED 或 Hub REFERENCE 文件必须只读。

#### Scenario: 编辑草稿文件
- **WHEN** 管理员保存一个 DRAFT 文件的有效内容
- **THEN** 系统生成新 MediaAsset 并更新该版本文件清单和摘要

#### Scenario: 编辑已发布或引用文件
- **WHEN** 用户尝试修改已发布 Skill 或 Hub REFERENCE 文件
- **THEN** 系统拒绝修改并提示复制或创建新版本

#### Scenario: 从已发布 Skill 创建草稿版本
- **WHEN** 管理员对本租户 PUBLISHED Skill 版本发起“基于此版本创建 Skill 草稿”
- **THEN** 系统创建同一 Skill 的下一 DRAFT 版本，复制其元数据、Tool 依赖和不可变文件对象引用，已发布版本与文件清单保持不变

#### Scenario: 对 Hub REFERENCE 创建 Skill 草稿
- **WHEN** 管理员尝试直接从 Hub REFERENCE Skill 创建可编辑草稿
- **THEN** 系统拒绝该操作并要求先 COPY 到本租户

#### Scenario: 发布 Skill DRAFT 文件版本
- **WHEN** 管理员发布一个有效的 DRAFT Skill 精确版本
- **THEN** 系统校验其依赖与唯一根目录 `SKILL.md`，并仅将该版本标记为 PUBLISHED

### Requirement: ZIP 导入必须安全且原子

系统 SHALL 在导入 ZIP 前校验压缩大小、展开大小、文件数量、路径规范、重复路径、符号链接和入口文件；任何致命错误不得部分更新文件清单。

#### Scenario: 导入有效文件包
- **WHEN** DRAFT Skill 收到包含根目录唯一 `SKILL.md` 的有效 ZIP，且用户确认替换或合并策略
- **THEN** 系统预览后保存所有文件引用并一次性提交新清单摘要

#### Scenario: ZIP 路径穿越或入口无效
- **WHEN** ZIP 包含绝对路径、`..`、符号链接、重复规范化路径或缺少根 `SKILL.md`
- **THEN** 系统拒绝整个导入且不发布部分文件引用

### Requirement: Skill 文件对象受租户授权和 GC 保护

系统 SHALL 将文件字节存入 ObjectStore，并由 MySQL 保存租户范围的路径、MediaAsset、字节数和 SHA-256 引用；仍被任何 Skill 版本引用的对象不得由 GC 删除。

#### Scenario: 重启后读取文件
- **WHEN** 服务重启后租户读取已授权 Skill 版本的文件
- **THEN** 系统按租户元数据读取并验证内容摘要

#### Scenario: 删除仍被版本引用的对象
- **WHEN** GC 处理已从一个草稿移除但仍被已发布 Skill 版本引用的 MediaAsset
- **THEN** 系统保留对象并将清理任务延后重试

### Requirement: Run 固定 Skill 文件版本并只读交给 Runtime

系统 SHALL 在 RunSnapshot 中记录 Skill 文件版本和清单摘要，并将入口内容和辅助文件以只读引用交给 Runtime。

#### Scenario: Run 使用文件包 Skill
- **WHEN** Run 解析一个已发布 FILE_PACKAGE Skill
- **THEN** 系统使用固定入口文件和该版本辅助文件，不读取草稿或上游新版本

#### Scenario: Runtime 尝试修改 Skill 文件
- **WHEN** Runtime 执行期间尝试写回 Skill 源文件
- **THEN** 系统拒绝写入并将输出保存为独立 Run 产物

### Requirement: Hub Skill 的 COPY 和 REFERENCE 保持一致语义

系统 SHALL 将 Hub COPY 文件写入接收租户对象存储并允许 DRAFT 编辑；Hub REFERENCE 文件只读并按导入记录解析授权的固定版本。

#### Scenario: 复制 Hub Skill 文件包
- **WHEN** 接收租户以 COPY 导入 Skill 并创建新草稿
- **THEN** 新版本具有租户自己的文件对象和清单，不受来源后续修改影响

#### Scenario: 引用 Hub Skill 文件包
- **WHEN** 员工版本绑定一个有效 Hub REFERENCE Skill
- **THEN** Runtime 读取该 Hub 版本的只读入口和辅助文件，且租户不能在线修改源文件
