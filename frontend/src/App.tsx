import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'

import { EmployeeGallery } from './features/digital-employees/EmployeeGallery'

export function App() {
  // 路由保持项目自有页面边界，后续继续扩展员工、技能、工具和运行页面。
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/workspace/gallery" element={<EmployeeGallery />} />
        <Route path="/enterprise/platform" element={<EmployeeGallery />} />
        <Route path="*" element={<Navigate to="/workspace/gallery" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
