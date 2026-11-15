import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, expect, test, vi } from 'vitest'

import { ToolManagementPage } from './ToolManagementPage'
import { TenantProvider } from '../management/TenantContext'

let tools: Record<string, unknown>[] = []

beforeEach(() => {
  window.localStorage.clear()
  tools = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    if (url === '/api/v1/tools' && method === 'GET') {
      return { ok: true, json: async () => ({ items: tools }) }
    }
    if (url === '/api/v1/digital-employees/employee-1' && method === 'GET') {
      return {
        ok: true,
        json: async () => ({ id: 'employee-1', name: '工单助手', status: 'ACTIVE' }),
      }
    }
    if (url === '/api/v1/tools' && method === 'POST') {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>
      const tool = {
        id: 'tool-created', tenant_id: 'tenant-a', version: 1, status: 'DRAFT',
        protocol: 'builtin', requires_recipient_credentials: false, credentials_configured: true,
        ...body,
      }
      tools = [...tools, tool]
      return { ok: true, json: async () => tool }
    }
    if (url === '/api/v1/tools/tool-created' && method === 'PATCH') {
      const body = JSON.parse(String(init?.body)) as Record<string, unknown>
      tools = tools.map((tool) => ({ ...tool, ...body }))
      return { ok: true, json: async () => tools[0] }
    }
    if (url === '/api/v1/tools/tool-created/publish' && method === 'POST') {
      tools = tools.map((tool) => ({ ...tool, status: 'PUBLISHED' }))
      return { ok: true, json: async () => tools[0] }
    }
    return { ok: false, status: 404, json: async () => ({ code: 'RESOURCE_NOT_FOUND', message: 'not found' }) }
  }))
})

function renderTools() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <TenantProvider>
        <MemoryRouter initialEntries={['/enterprise/employees/employee-1/tools/new']}>
          <Routes>
            <Route path="/enterprise/employees/:employeeId/tools/:toolId" element={<ToolManagementPage />} />
          </Routes>
        </MemoryRouter>
      </TenantProvider>
    </QueryClientProvider>,
  )
}

test('employee Tool management creates a draft and publishes it for version binding', async () => {
  const user = userEvent.setup()
  renderTools()

  expect(await screen.findByRole('heading', { name: 'Tool 管理' })).toBeInTheDocument()
  expect(await screen.findByText(/还没有 Tool/)).toBeInTheDocument()
  await user.click(screen.getAllByRole('button', { name: '新建 Tool' })[0])
  await user.type(screen.getByLabelText('Tool 名称'), '查询工单')
  await user.type(screen.getByLabelText('Tool 描述'), '按工单编号查询状态')
  await user.click(screen.getByRole('button', { name: '创建 Tool' }))

  const row = await screen.findByRole('row', { name: /查询工单/ })
  expect(within(row).getByText('DRAFT')).toBeInTheDocument()
  await user.click(within(row).getByRole('button', { name: '编辑草稿' }))
  await user.clear(screen.getByLabelText('Tool 名称'))
  await user.type(screen.getByLabelText('Tool 名称'), '读取工单状态')
  await user.click(screen.getByRole('button', { name: '保存草稿' }))
  const updatedRow = await screen.findByRole('row', { name: /读取工单状态/ })
  expect(vi.mocked(fetch).mock.calls.some(([input, init]) => (
    String(input) === '/api/v1/tools/tool-created' && init?.method === 'PATCH'
  ))).toBe(true)
  await user.click(within(updatedRow).getByRole('button', { name: '发布' }))
  await waitFor(() => expect(within(updatedRow).getByText('PUBLISHED')).toBeInTheDocument())
  expect(vi.mocked(fetch).mock.calls.some(([input, init]) => (
    String(input) === '/api/v1/tools/tool-created/publish' && init?.method === 'POST'
  ))).toBe(true)
}, 15000)
