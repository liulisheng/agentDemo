# StaffDeck 行为参考

## 目的

本文档记录对本地 StaffDeck 实例的黑盒观察结果，用于提取数字员工平台的领域模型、页面信息架构和交互流程。平台自身将独立实现这些行为，不复制 StaffDeck 源代码、静态资源、样式、前端组件或私有实现。

## 观察边界

- 目标：`http://127.0.0.1:5173/workspace/gallery`
- 方式：REA 控制的隔离 Chromium 场景、DOM、无障碍树、截图和网络元数据。
- 数据范围：本地 `tenant_demo`，仅使用无副作用的登录、页面导航和一条测试消息。
- 未执行：业务工具写操作、审批动作、真实生产变更、StaffDeck 源码复制。

## 文档索引

- [页面和路由](page-map.md)
- [领域模型](domain-model.md)
- [运行时 API 和状态流](runtime-api-flow.md)
- [独立实现的交互规格](interaction-spec.md)
- [证据、限制和未知项](evidence.md)

## 独立实现原则

1. 只提取可观察的用户行为、资源关系和请求边界。
2. 领域对象使用本项目自己的命名和版本模型。
3. Runtime 通过 `RuntimeAdapter` 接入，平台不复制供应商内部 Agent Loop。
4. 页面交互先形成行为规格，再由本项目自己的前端和后端实现。
5. StaffDeck 的 URL、CSS、图片、文案和组件实现不作为代码依赖。
