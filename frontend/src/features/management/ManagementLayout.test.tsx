import { fireEvent, render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { expect, test } from 'vitest'

import { ManagementLayout, ManagementPlaceholder } from './ManagementLayout'
import { TenantProvider, useTenantContext } from './TenantContext'

function TenantName() {
  const { currentIdentity } = useTenantContext()
  return <span>{currentIdentity.tenantName} / {currentIdentity.roleName}</span>
}

test('management shell exposes three top-level areas and keeps skills under employee detail', async () => {
  window.localStorage.clear()
  render(
    <MemoryRouter initialEntries={['/enterprise/employees']}>
      <TenantProvider>
        <Routes>
          <Route path="/enterprise" element={<ManagementLayout />}>
            <Route path="employees" element={<ManagementPlaceholder title="员工目录内容" />} />
            <Route path="resources" element={<ManagementPlaceholder title="资源中心内容" />} />
            <Route path="deployments" element={<ManagementPlaceholder title="部署记录内容" />} />
          </Route>
        </Routes>
      </TenantProvider>
    </MemoryRouter>,
  )

  const managementNav = screen.getByRole('navigation', { name: '管理导航' })
  expect(within(managementNav).getByRole('link', { name: '数字员工' })).toHaveAttribute('href', '/enterprise/employees')
  expect(within(managementNav).getByRole('link', { name: '运行工作台' })).toHaveAttribute('href', '/enterprise/workbench')
  expect(within(managementNav).getByRole('link', { name: '资源中心' })).toHaveAttribute('href', '/enterprise/resources')
  expect(within(managementNav).getByRole('link', { name: '部署记录' })).toHaveAttribute('href', '/enterprise/deployments')
  expect(screen.queryByRole('link', { name: 'Skill 管理' })).not.toBeInTheDocument()
  expect(screen.getByText('员工目录内容')).toBeInTheDocument()

  fireEvent.change(screen.getByLabelText('当前身份'), { target: { value: 'admin-other' } })
  expect(screen.getByRole('heading', { name: '租户 B' })).toBeInTheDocument()
  expect(screen.getByLabelText('当前身份')).toHaveValue('admin-other')
})
