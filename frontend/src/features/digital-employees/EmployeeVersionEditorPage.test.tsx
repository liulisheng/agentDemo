import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, expect, test, vi } from 'vitest'

import { EmployeeVersionEditorPage } from './EmployeeVersionEditorPage'
import { TenantProvider } from '../management/TenantContext'

const version = {
  employee_id: 'employee-1',
  tenant_id: 'tenant-a',
  version: 3,
  system_prompt: '初始系统指令',
  skill_ids: ['skill-1'],
  tool_ids: ['tool-1'],
  skill_version_refs: [{ skill_id: 'skill-1', version: 2 }],
  tool_version_refs: [{ tool_id: 'tool-1', version: 4 }],
  status: 'DRAFT',
}

const responses: Record<string, unknown> = {}

beforeEach(() => {
  window.localStorage.clear()
  Object.keys(responses).forEach((key) => delete responses[key])
  responses['GET /api/v1/digital-employees/employee-1'] = {
    id: 'employee-1', tenant_id: 'tenant-a', name: '工单助手', role_name: '客服', description: '处理工单',
    status: 'ACTIVE', published_to_gallery: false, resource_counts: { knowledge_base: 0, skill: 1, sop: 0, tool: 1 },
    version_count: 1, latest_published_version: null,
  }
  responses['GET /api/v1/digital-employees/employee-1/versions/3'] = version
  responses['GET /api/v1/skills?include_versions=true'] = {
    items: [{
      id: 'skill-1', tenant_id: 'tenant-a', name: '工单处理', skill_type: 'instruction',
      instruction: '按流程处理', allowed_tool_ids: ['tool-1'],
      allowed_tool_versions: [{ tool_id: 'tool-1', version: 4 }], version: 2, status: 'PUBLISHED',
    }],
  }
  responses['GET /api/v1/tools'] = {
    items: [{
      id: 'tool-1', tenant_id: 'tenant-a', name: '读取工单', description: '只读', version: 4,
      status: 'PUBLISHED', protocol: 'builtin', requires_recipient_credentials: false,
      credentials_configured: true, input_schema: { type: 'object' },
    }],
  }
  responses['PUT /api/v1/digital-employees/employee-1/versions/3'] = {
    ...version, system_prompt: '更新后的指令',
  }
  responses['POST /api/v1/digital-employees/employee-1/versions/3/test'] = {
    status: 'succeeded', result: 'fake: 预览这个版本',
    usage: { input_tokens: 2, output_tokens: 3, total_tokens: 5, source: 'ESTIMATED', estimated: true },
    events: [],
  }
})

function renderEditor() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <TenantProvider>
        <MemoryRouter initialEntries={['/enterprise/employees/employee-1/versions/3']}>
          <Routes>
            <Route path="/enterprise/employees/:employeeId/versions/:version" element={<EmployeeVersionEditorPage />} />
            <Route path="/enterprise/employees/:employeeId" element={<div>员工详情目标页</div>} />
          </Routes>
        </MemoryRouter>
      </TenantProvider>
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const key = `${init?.method ?? 'GET'} ${url}`
    if (responses[key] !== undefined) {
      return { ok: true, json: async () => responses[key] }
    }
    return { ok: false, status: 404, json: async () => ({ code: 'RESOURCE_NOT_FOUND', message: 'not found' }) }
  }))
})

test('version editor shows exact refs, saves draft bindings and previews the fake runtime', async () => {
  const user = userEvent.setup()
  renderEditor()

  expect(await screen.findByLabelText('系统指令')).toBeInTheDocument()
  expect(screen.getByText('员工与 Skill 均允许 · 读取工单')).toBeInTheDocument()
  const systemPrompt = screen.getByLabelText('系统指令')
  await user.clear(systemPrompt)
  await user.type(systemPrompt, '更新后的指令')
  await user.click(screen.getByRole('button', { name: '保存草稿' }))

  await waitFor(() => {
    const calls = vi.mocked(fetch).mock.calls
    const updateCall = calls.find(([input, init]) => (
      String(input) === '/api/v1/digital-employees/employee-1/versions/3' && init?.method === 'PUT'
    ))
    expect(updateCall).toBeDefined()
    const body = JSON.parse(String(updateCall?.[1]?.body))
    expect(body.skill_version_refs).toEqual([{ resource_id: 'skill-1', version: 2 }])
    expect(body.tool_version_refs).toEqual([{ resource_id: 'tool-1', version: 4 }])
    expect(body).toEqual({
      system_prompt: '更新后的指令',
      skill_version_refs: [{ resource_id: 'skill-1', version: 2 }],
      tool_version_refs: [{ resource_id: 'tool-1', version: 4 }],
    })
  })

  await user.click(screen.getByRole('button', { name: '测试版本' }))
  await user.clear(screen.getByLabelText('测试 prompt'))
  await user.type(screen.getByLabelText('测试 prompt'), '预览这个版本')
  await user.click(screen.getByRole('button', { name: '运行 Fake Runtime' }))
  expect(await screen.findByText('fake: 预览这个版本')).toBeInTheDocument()
}, 15000)

test('version editor explains the system prompt scope beside its field', async () => {
  renderEditor()

  const systemPrompt = await screen.findByLabelText('系统指令')
  const promptGuidance = screen.getByText('定义员工每次处理请求时都遵守的角色、规则和边界。')
  expect(promptGuidance.closest('.ant-form-item')).toBe(systemPrompt.closest('.ant-form-item'))
})

test('employee version editor leaves engine and model to the recipient deployment', async () => {
  renderEditor()

  expect(await screen.findByLabelText('系统指令')).toBeInTheDocument()
  expect(screen.queryByText('运行配置')).not.toBeInTheDocument()
  expect(screen.queryByLabelText('ExecutionProfile')).not.toBeInTheDocument()
  expect(screen.queryByLabelText('ModelRoute')).not.toBeInTheDocument()
  expect(screen.getByText(/员工版本只保存系统指令和 Skill\/Tool 精确绑定/)).toBeInTheDocument()
  expect(vi.mocked(fetch).mock.calls.some(([input]) => (
    String(input) === '/api/v1/execution-profiles' || String(input) === '/api/v1/model-routes/selectable'
  ))).toBe(false)
})

test('version editor warns before an in-app navigation with unsaved changes', async () => {
  const user = userEvent.setup()
  const confirm = vi.fn(() => false)
  vi.stubGlobal('confirm', confirm)
  renderEditor()

  expect(await screen.findByRole('heading', { name: '编辑草稿 v3' })).toBeInTheDocument()
  await user.type(screen.getByLabelText('系统指令'), ' 未保存')
  fireEvent.click(screen.getByRole('link', { name: /返回员工详情/ }))
  expect(confirm).toHaveBeenCalled()
  expect(screen.queryByText('员工详情目标页')).not.toBeInTheDocument()
})

test('version editor keeps Skill and Tool selectors in separate resource sections', async () => {
  renderEditor()

  expect(await screen.findByRole('heading', { name: '编辑草稿 v3' })).toBeInTheDocument()
  const skillSection = screen.getByRole('region', { name: 'Skill 绑定' })
  const toolSection = screen.getByRole('region', { name: 'Tool 绑定' })
  expect(within(skillSection).getByLabelText('选择 Skill 版本')).toBeInTheDocument()
  expect(within(toolSection).getByLabelText('选择 Tool 版本')).toBeInTheDocument()
  expect(within(skillSection).queryByLabelText('选择 Tool 版本')).not.toBeInTheDocument()
  expect(within(toolSection).queryByLabelText('选择 Skill 版本')).not.toBeInTheDocument()
})

test('version editor sends Skill creation to its dedicated workspace', async () => {
  renderEditor()

  expect(await screen.findByRole('heading', { name: '编辑草稿 v3' })).toBeInTheDocument()
  const createLink = screen.getByRole('link', { name: '新建 Skill 草稿' })
  expect(createLink).toHaveAttribute('href', '/enterprise/employees/employee-1/skills/new')
  expect(vi.mocked(fetch).mock.calls.some(([input, init]) => (
    String(input) === '/api/v1/skills' && init?.method === 'POST'
  ))).toBe(false)
})
