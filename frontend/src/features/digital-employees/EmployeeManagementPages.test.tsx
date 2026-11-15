import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, expect, test, vi } from 'vitest'

import { EmployeeListPage } from './EmployeeListPage'
import { EmployeeDetailPage } from './EmployeeDetailPage'
import { ManagementLayout } from '../management/ManagementLayout'
import { TenantProvider } from '../management/TenantContext'

const employee = {
  id: 'employee-1',
  tenant_id: 'tenant-a',
  name: '办公助手',
  role_name: '行政支持',
  description: '处理办公申请',
  status: 'DRAFT',
  published_to_gallery: false,
  resource_counts: { knowledge_base: 0, skill: 0, sop: 0, tool: 0 },
  version_count: 0,
  latest_published_version: null,
}
let fakeEmployeeState = { ...employee }

beforeEach(() => {
  window.localStorage.clear()
  fakeEmployeeState = { ...employee }
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input)
      if (url.startsWith('/api/v1/digital-employees?')) {
        return { ok: true, json: async () => ({ items: [], next_cursor: null }) }
      }
      if (url === '/api/v1/digital-employees' && init?.method === 'POST') {
        fakeEmployeeState = { ...employee }
        return { ok: true, json: async () => fakeEmployeeState }
      }
      if (url === '/api/v1/digital-employees/employee-1' && (!init?.method || init.method === 'GET')) {
        return { ok: true, json: async () => fakeEmployeeState }
      }
      if (url === '/api/v1/digital-employees/employee-1/versions?limit=100') {
        return { ok: true, json: async () => ({ items: [], next_cursor: null }) }
      }
      if (url === '/api/v1/digital-employees/employee-1' && init?.method === 'PATCH') {
        const body = JSON.parse(String(init.body))
        fakeEmployeeState = { ...fakeEmployeeState, ...body }
        return { ok: true, json: async () => fakeEmployeeState }
      }
      if (url === '/api/v1/digital-employees/employee-1/archive' && init?.method === 'POST') {
        fakeEmployeeState = { ...fakeEmployeeState, status: 'ARCHIVED' }
        return { ok: true, json: async () => fakeEmployeeState }
      }
      return { ok: false, status: 404, json: async () => ({ code: 'RESOURCE_NOT_FOUND', message: 'not found' }) }
    }),
  )
})

function renderEmployeeRoutes(path = '/enterprise/employees') {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <TenantProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes>
            <Route path="/enterprise" element={<ManagementLayout />}>
              <Route path="employees" element={<EmployeeListPage />} />
              <Route path="employees/:employeeId" element={<EmployeeDetailPage />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </TenantProvider>
    </QueryClientProvider>,
  )
}

test('employee list creates an employee and opens its detail page', async () => {
  const user = userEvent.setup()
  renderEmployeeRoutes()

  expect(await screen.findByRole('heading', { name: '数字员工' })).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '新建数字员工' }))
  await user.type(screen.getByLabelText('员工名称'), '办公助手')
  await user.type(screen.getByLabelText('员工类型'), '行政支持')
  await user.type(screen.getByLabelText('员工描述'), '处理办公申请')
  await user.click(screen.getByRole('button', { name: '创建员工' }))

  expect(await screen.findByRole('heading', { name: '办公助手' })).toBeInTheDocument()
  expect(screen.getByText('处理办公申请')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '归档员工' }))
  await user.click(await screen.findByRole('button', { name: '确认归档' }))
  expect(await screen.findByRole('heading', { name: '数字员工' })).toBeInTheDocument()
}, 10000)

test('employee detail edits basic information and hides actions for a member', async () => {
  const user = userEvent.setup()
  renderEmployeeRoutes('/enterprise/employees/employee-1')

  expect(await screen.findByRole('heading', { name: '办公助手' })).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '编辑基本信息' }))
  const description = screen.getByLabelText('员工描述')
  await user.clear(description)
  await user.type(description, '修改后的办公申请处理')
  await user.click(screen.getByRole('button', { name: '保存修改' }))
  expect(await screen.findByText('修改后的办公申请处理')).toBeInTheDocument()

  fireEvent.change(screen.getByLabelText('当前身份'), { target: { value: 'member-local' } })
  await waitFor(() => expect(screen.queryByRole('button', { name: '编辑基本信息' })).not.toBeInTheDocument())
  await waitFor(() => {
    const calls = vi.mocked(fetch).mock.calls
    expect(calls.some(([input, init]) => (
      String(input) === '/api/v1/digital-employees/employee-1'
      && new Headers(init?.headers).get('X-Fake-User') === 'member-local'
    ))).toBe(true)
  })
  expect(screen.getByRole('heading', { name: '办公助手' })).toBeInTheDocument()
})

test('member sees the empty directory but cannot create an employee', async () => {
  window.localStorage.setItem('agent-platform.fake-user', 'member-local')
  renderEmployeeRoutes()

  expect(await screen.findByText('还没有数字员工')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '新建数字员工' })).not.toBeInTheDocument()
})

test('employee list displays API errors and offers a retry action', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      json: async () => ({ code: 'TEMPORARY_FAILURE', message: '员工服务暂时不可用' }),
    }),
  )
  renderEmployeeRoutes()

  expect(await screen.findByText('员工服务暂时不可用')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: /重\s*试/ })).toBeInTheDocument()
})
