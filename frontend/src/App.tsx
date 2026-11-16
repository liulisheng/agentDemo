import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'

import { EmployeeGallery } from './features/digital-employees/EmployeeGallery'
import { EmployeeDetailPage } from './features/digital-employees/EmployeeDetailPage'
import { EmployeeListPage } from './features/digital-employees/EmployeeListPage'
import { EmployeeVersionEditorPage } from './features/digital-employees/EmployeeVersionEditorPage'
import { SkillFileWorkspacePage } from './features/digital-employees/SkillFileWorkspacePage'
import { SkillCreationPage } from './features/digital-employees/SkillCreationPage'
import { ToolManagementPage } from './features/digital-employees/ToolManagementPage'
import { DeploymentListPage } from './features/deployments/DeploymentListPage'
import { ResourceCenterPage } from './features/resources/ResourceCenterPage'
import { ManagementLayout } from './features/management/ManagementLayout'
import { TenantProvider } from './features/management/TenantContext'
import { RunTracePage } from './features/runs/RunTrace'
import { ChatWorkspace } from './features/platform/PlatformPages'

export function App() {
  // 资源中心管理租户 Skill/Tool，员工工作台负责把精确版本绑定到员工草稿。
  return (
    <TenantProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Navigate to="/enterprise/employees" replace />} />
          <Route path="/workspace/gallery" element={<EmployeeGallery />} />
          <Route path="/enterprise/platform" element={<EmployeeGallery />} />
          <Route path="/workspace/runs/:runId/trace" element={<RunTracePage />} />
          <Route path="/enterprise/dashboard" element={<Navigate to="/enterprise/employees" replace />} />
          <Route path="/enterprise/skills" element={<Navigate to="/enterprise/employees" replace />} />
          <Route path="/workspace/chat" element={<ChatWorkspace />} />
          <Route path="/enterprise" element={<ManagementLayout />}>
            <Route index element={<Navigate to="employees" replace />} />
            <Route path="employees" element={<EmployeeListPage />} />
            <Route path="employees/:employeeId" element={<EmployeeDetailPage />} />
            <Route path="employees/:employeeId/versions/:version" element={<EmployeeVersionEditorPage />} />
            <Route path="employees/:employeeId/skills/new" element={<SkillCreationPage />} />
            <Route path="employees/:employeeId/skills/:skillId/versions/:version/files" element={<SkillFileWorkspacePage />} />
            <Route path="employees/:employeeId/tools/:toolId" element={<ToolManagementPage />} />
            <Route path="resources/skills/:skillId/versions/:version/files" element={<SkillFileWorkspacePage />} />
            <Route path="resources" element={<ResourceCenterPage />} />
            <Route path="deployments" element={<DeploymentListPage />} />
          </Route>
          <Route path="*" element={<Navigate to="/enterprise/employees" replace />} />
        </Routes>
      </BrowserRouter>
    </TenantProvider>
  )
}
