# Tasks

## 1. 表单字段说明与前端验收

- [x] 1.1 在员工目录创建/员工详情编辑、员工版本创建/编辑，以及员工上下文和资源中心的 Skill 创建表单中，为三个目标字段添加规格要求的辅助说明；更新对应前端测试，验证文案在字段附近可见且现有提交流程不变，运行 `npm --prefix frontend test -- EmployeeManagementPages.test.tsx EmployeeVersionEditorPage.test.tsx ResourceCenterPage.test.tsx`。
- [x] 1.2 新增 `docs/员工字段用途与填写说明.md`，包含组件关系图、填写流程图、数据字段说明、API/运行状态边界、已验证项/未验证项和验证命令；检查文档覆盖项目约定且无 API、数据模型或运行时行为变更。
- [x] 1.3 执行 `npm --prefix frontend run build` 与 `openspec validate employee-instruction-field-guidance --strict`，确认前端构建成功且 Change 的 proposal、spec、design、tasks 均通过严格校验。
