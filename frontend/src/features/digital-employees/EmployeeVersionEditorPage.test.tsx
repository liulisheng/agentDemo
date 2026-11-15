import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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
  execution_profile_id: 'profile-fake',
  execution_profile_version: 1,
  model_route_id: 'route-1',
  workspace_profile_id: 'workspace-1',
  workspace_profile_version: 2,
  sandbox_profile_id: null,
  sandbox_profile_version: null,
  approval_policy: 'required-for-write',
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
  responses['GET /api/v1/skills'] = {
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
  responses['GET /api/v1/execution-profiles'] = {
    items: [{
      id: 'profile-fake', tenant_id: 'tenant-a', version: 1, engine_name: 'fake', model_alias: 'fake-model',
      tool_policy: [], context_policy: 'default', model_route_id: 'route-1',
      workspace_profile_id: 'workspace-1', workspace_profile_version: 2,
      sandbox_profile_id: null, sandbox_profile_version: null, approval_policy: 'none', status: 'PUBLISHED',
    }],
  }
  responses['GET /api/v1/model-routes'] = {
    items: [{
      route_id: 'route-1', tenant_id: 'tenant-a', alias: '客服模型', status: 'ACTIVE',
      profile: { model_name: 'model-x', provider: 'fake', endpoint: 'fake://model' },
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
    if (key === 'POST /api/v1/skills') {
      const createdSkill = {
        id: 'skill-created', tenant_id: 'tenant-a', name: '新建 Skill', skill_type: 'instruction',
        instruction: '按验收流程处理', allowed_tool_ids: [], allowed_tool_versions: [], version: 1, status: 'DRAFT',
      }
      const currentSkills = responses['GET /api/v1/skills'] as { items: Record<string, unknown>[] }
      responses['GET /api/v1/skills'] = { items: [...currentSkills.items, createdSkill] }
      return { ok: true, json: async () => createdSkill }
    }
    if (key === 'POST /api/v1/skills/skill-created/publish') {
      const currentSkills = responses['GET /api/v1/skills'] as { items: Record<string, unknown>[] }
      const publishedSkills = currentSkills.items.map((skill) => (
        skill.id === 'skill-created' ? { ...skill, status: 'PUBLISHED' } : skill
      ))
      responses['GET /api/v1/skills'] = { items: publishedSkills }
      return {
        ok: true,
        json: async () => publishedSkills.find((skill) => skill.id === 'skill-created'),
      }
    }
    if (responses[key] !== undefined) {
      return { ok: true, json: async () => responses[key] }
    }
    return { ok: false, status: 404, json: async () => ({ code: 'RESOURCE_NOT_FOUND', message: 'not found' }) }
  }))
})

test('version editor shows exact refs, saves draft bindings and previews the fake runtime', async () => {
  const user = userEvent.setup()
  renderEditor()

  expect(await screen.findByRole('heading', { name: '员工版本配置' })).toBeInTheDocument()
  expect(screen.getByText('有效 · 读取工单')).toBeInTheDocument()
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
    expect(body.execution_profile_id).toBe('profile-fake')
  })

  await user.click(screen.getByRole('button', { name: '测试版本' }))
  await user.clear(screen.getByLabelText('测试 prompt'))
  await user.type(screen.getByLabelText('测试 prompt'), '预览这个版本')
  await user.click(screen.getByRole('button', { name: '运行 Fake Runtime' }))
  expect(await screen.findByText('fake: 预览这个版本')).toBeInTheDocument()
}, 15000)

test('version editor warns before an in-app navigation with unsaved changes', async () => {
  const user = userEvent.setup()
  const confirm = vi.fn(() => false)
  vi.stubGlobal('confirm', confirm)
  renderEditor()

  expect(await screen.findByRole('heading', { name: '员工版本配置' })).toBeInTheDocument()
  await user.type(screen.getByLabelText('系统指令'), ' 未保存')
  fireEvent.click(screen.getByRole('link', { name: /返回员工详情/ }))
  expect(confirm).toHaveBeenCalled()
  expect(screen.queryByText('员工详情目标页')).not.toBeInTheDocument()
})

test('version editor creates and publishes a Skill then binds it to the employee draft', async () => {
  const user = userEvent.setup()
  renderEditor()

  expect(await screen.findByRole('heading', { name: '员工版本配置' })).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '新建 Skill' }))
  await user.type(screen.getByLabelText('Skill 名称'), '新建 Skill')
  await user.type(screen.getByLabelText('入口指令'), '按验收流程处理')
  await user.click(screen.getByRole('button', { name: '创建并加入草稿' }))

  await waitFor(() => {
    expect(vi.mocked(fetch).mock.calls.some(([input, init]) => (
      String(input) === '/api/v1/skills' && init?.method === 'POST'
    ))).toBe(true)
    expect(vi.mocked(fetch).mock.calls.some(([input, init]) => (
      String(input) === '/api/v1/skills/skill-created/publish' && init?.method === 'POST'
    ))).toBe(true)
  })
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
}, 15000)
