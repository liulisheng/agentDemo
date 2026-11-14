import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'

import { EmployeeGallery } from './features/digital-employees/EmployeeGallery'
import { RunTracePage } from './features/runs/RunTrace'
import { AgentDashboard, ChatWorkspace, SkillList } from './features/platform/PlatformPages'

export function App() {
  // 路由保持项目自有页面边界，后续继续扩展员工、技能、工具和运行页面。
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/workspace/gallery" element={<EmployeeGallery />} />
        <Route path="/enterprise/platform" element={<EmployeeGallery />} />
        <Route path="/workspace/runs/:runId/trace" element={<RunTracePage />} />
        <Route path="/enterprise/dashboard" element={<AgentDashboard />} />
        <Route path="/enterprise/skills" element={<SkillList />} />
        <Route path="/workspace/chat" element={<ChatWorkspace />} />
        <Route path="*" element={<Navigate to="/workspace/gallery" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
