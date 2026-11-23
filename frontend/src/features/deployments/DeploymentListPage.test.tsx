import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, expect, test, vi } from 'vitest'

import { DeploymentListPage } from './DeploymentListPage'
import { TenantProvider } from '../management/TenantContext'

const deployment = {
  id: 'deployment-1', tenant_id: 'tenant-a', employee_id: 'employee-1', version: 2, status: 'PENDING_CONFIGURATION', runtime_config_version: null,
  version_summary: {
    version: 2, status: 'PUBLISHED', skill_count: 1, tool_count: 1,
  },
}

beforeEach(() => {
  window.localStorage.clear()
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    if (url.startsWith('/api/v1/deployments?')) {
      return { ok: true, json: async () => ({ items: [deployment], next_cursor: null }) }
    }
    if (url === '/api/v1/digital-employees?limit=100') {
      return { ok: true, json: async () => ({ items: [{ id: 'employee-1', name: '工单助手', role_name: '客服', status: 'ACTIVE' }] }) }
    }
    if (url === '/api/v1/digital-employees/employee-1/versions?status=PUBLISHED&limit=100') {
      return { ok: true, json: async () => ({ items: [
        { version: 2, status: 'PUBLISHED', skill_version_refs: [{ skill_id: 'skill-1', version: 2 }], tool_version_refs: [{ tool_id: 'tool-1', version: 4 }] },
        { version: 1, status: 'PUBLISHED', skill_version_refs: [{ skill_id: 'skill-1', version: 1 }], tool_version_refs: [] },
      ], next_cursor: null }) }
    }
    if (url === '/api/v1/audit-events?resource_type=deployment&resource_id=deployment-1&limit=20') {
      return { ok: true, json: async () => ({ items: [
        { event_id: 'event-1', actor_type: 'USER', actor_user_id: 'admin-local', event_type: 'DEPLOYMENT_CREATED', resource_type: 'deployment', resource_id: 'deployment-1', payload: { version: 2 }, created_at: '2026-11-15T10:00:00+00:00' },
      ], next_cursor: null }) }
    }
    if (url === '/api/v1/deployments' && init?.method === 'POST') {
      return { ok: true, json: async () => deployment }
    }
    if (url === '/api/v1/deployments/deployment-1/rollback' && init?.method === 'POST') {
      return { ok: true, json: async () => ({ ...deployment, version: 1, version_summary: { ...deployment.version_summary, version: 1 } }) }
    }
    return { ok: false, status: 404, json: async () => ({ code: 'RESOURCE_NOT_FOUND', message: 'not found' }) }
  }))
})

function renderDeployments() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <TenantProvider>
        <MemoryRouter initialEntries={['/enterprise/deployments']}>
          <Routes>
            <Route path="/enterprise/deployments" element={<DeploymentListPage />} />
          </Routes>
        </MemoryRouter>
      </TenantProvider>
    </QueryClientProvider>,
  )
}

async function chooseOption(label: string) {
  await userEvent.click(await screen.findByText(label))
}

test('deployment page creates from a published version, confirms rollback and shows audit events', async () => {
  const user = userEvent.setup()
  renderDeployments()

  expect(await screen.findByText('工单助手')).toBeInTheDocument()
  expect(screen.getByText('1 Skill · 1 Tool')).toBeInTheDocument()
  expect(screen.getByText('运行配置待设置')).toBeInTheDocument()
  expect(screen.getByText('待配置运行环境')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '审计记录' }))
  expect(await screen.findByText('DEPLOYMENT_CREATED')).toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: '新建 Deployment' }))
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '选择数字员工' }))
  await chooseOption('工单助手 · 客服')
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '选择已发布版本' }))
  await chooseOption('v2 · 1 Skill / 1 Tool')
  await user.click(screen.getByRole('button', { name: '创建 Deployment' }))
  await waitFor(() => {
    const request = vi.mocked(fetch).mock.calls.find(([input, init]) => (
      String(input) === '/api/v1/deployments' && init?.method === 'POST'
    ))
    expect(request).toBeDefined()
    expect(JSON.parse(String(request?.[1]?.body))).toEqual({ employee_id: 'employee-1', version: 2 })
  })

  await user.click(screen.getByRole('button', { name: '回滚' }))
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '回滚目标版本' }))
  await chooseOption('v1 · PUBLISHED')
  await user.click(screen.getByRole('button', { name: '确认回滚' }))
  const confirmation = await screen.findByRole('tooltip')
  await user.click(within(confirmation).getByRole('button', { name: /确认回滚/ }))
  await waitFor(() => {
    const request = vi.mocked(fetch).mock.calls.find(([input, init]) => (
      String(input) === '/api/v1/deployments/deployment-1/rollback' && init?.method === 'POST'
    ))
    expect(request).toBeDefined()
    expect(JSON.parse(String(request?.[1]?.body))).toEqual({ employee_id: 'employee-1', version: 1 })
  })
}, 30000)
