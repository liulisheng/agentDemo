import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom'
import { beforeEach, expect, test, vi } from 'vitest'

import { EmployeeListPage } from './EmployeeListPage'
import { EmployeeDetailPage } from './EmployeeDetailPage'
import { SkillCreationPage } from './SkillCreationPage'
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
let fakeVersions: Record<string, unknown>[] = []

beforeEach(() => {
  window.localStorage.clear()
  fakeEmployeeState = { ...employee }
  fakeVersions = []
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
        return { ok: true, json: async () => ({ items: fakeVersions, next_cursor: null }) }
      }
      if (url === '/api/v1/skills' && (!init?.method || init.method === 'GET')) {
        return { ok: true, json: async () => ({ items: [
          { id: 'skill-1', name: '工单流程', version: 2, status: 'PUBLISHED' },
          { id: 'skill-2', name: '退款规则', version: 1, status: 'PUBLISHED' },
        ] }) }
      }
      if (url === '/api/v1/skills' && init?.method === 'POST') {
        return { ok: true, json: async () => ({
          id: 'skill-created', tenant_id: 'tenant-a', name: '新建的 Skill',
          skill_type: 'instruction', instruction: '初始指令', allowed_tool_ids: [],
          allowed_tool_versions: [], version: 1, status: 'DRAFT',
        }) }
      }
      if (url === '/api/v1/skills?include_versions=true' && (!init?.method || init.method === 'GET')) {
        return { ok: true, json: async () => ({ items: [
          { id: 'skill-1', name: '工单流程', version: 1, status: 'PUBLISHED' },
          { id: 'skill-1', name: '工单流程', version: 2, status: 'PUBLISHED' },
          { id: 'skill-2', name: '退款规则', version: 1, status: 'PUBLISHED' },
          { id: 'skill-3', name: '草稿技能', version: 2, status: 'DRAFT' },
        ] }) }
      }
      if (url === '/api/v1/tools' && (!init?.method || init.method === 'GET')) {
        return { ok: true, json: async () => ({ items: [
          { id: 'tool-1', name: '读取工单', version: 3, status: 'PUBLISHED' },
        ] }) }
      }
      if (url === '/api/v1/hub/resources?resource_type=SKILL&limit=200' && (!init?.method || init.method === 'GET')) {
        return { ok: true, json: async () => ({ items: [{
          hub_resource_id: 'hub-skill-1', resource_type: 'SKILL', name: '共享客服 Skill',
          description: '共享的工单处理约定', publisher_name: '租户 B', visibility: 'PLATFORM',
          latest_version: 4, available_modes: ['COPY', 'REFERENCE'],
        }] }) }
      }
      if (url === '/api/v1/hub/resources/hub-skill-1/versions/4' && (!init?.method || init.method === 'GET')) {
        return { ok: true, json: async () => ({
          hub_resource_id: 'hub-skill-1', resource_type: 'SKILL', name: '共享客服 Skill',
          description: '共享的工单处理约定', publisher_name: '租户 B', visibility: 'PLATFORM',
          latest_version: 4, available_modes: ['COPY', 'REFERENCE'], version: 4,
          manifest_sha256: 'a'.repeat(64), dependency_count: 1,
        }) }
      }
      if (url === '/api/v1/hub/resources/hub-skill-1/versions/4/manifest?mode=COPY' && (!init?.method || init.method === 'GET')) {
        return { ok: true, json: async () => ({
          hub_resource_id: 'hub-skill-1', version: 4, manifest_sha256: 'a'.repeat(64),
          manifest: {
            resource: { type: 'SKILL', name: '共享客服 Skill', version: 4 },
            content: {},
            dependencies: [{ sequence_no: 1, type: 'TOOL', resource_id: 'tool-shared', version: 2, required: true }],
            requires_recipient_credentials: false,
          },
        }) }
      }
      if (url === '/api/v1/hub/imports' && init?.method === 'POST') {
        return { ok: true, json: async () => ({
          import_id: 'import-1', hub_resource_id: 'hub-skill-1', hub_version: 4,
          mode: 'COPY', local_resource_type: 'SKILL', local_resource_id: 'skill-copy',
          local_resource_version: 1, manifest_sha256: 'a'.repeat(64), status: 'ACTIVE',
        }) }
      }
      if (url === '/api/v1/digital-employees/employee-1/versions/1/skill-bindings' && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as { skill_version_refs: { resource_id: string; version: number }[] }
        const current = fakeVersions[0]
        const updated = {
          ...current,
          skill_ids: body.skill_version_refs.map((item) => item.resource_id),
          skill_version_refs: body.skill_version_refs.map((item) => ({ skill_id: item.resource_id, version: item.version })),
        }
        fakeVersions = [updated]
        return { ok: true, json: async () => updated }
      }
      if (url === '/api/v1/digital-employees/employee-1/versions/2/skill-bindings' && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as { skill_version_refs: { resource_id: string; version: number }[] }
        const updated = {
          ...fakeVersions[0],
          skill_ids: body.skill_version_refs.map((item) => item.resource_id),
          skill_version_refs: body.skill_version_refs.map((item) => ({ skill_id: item.resource_id, version: item.version })),
        }
        fakeVersions = [updated, ...fakeVersions.slice(1)]
        return { ok: true, json: async () => updated }
      }
      if (url === '/api/v1/digital-employees/employee-1/versions/1/tool-bindings' && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as { tool_version_refs: { resource_id: string; version: number }[] }
        const updated = {
          ...fakeVersions[0],
          tool_ids: body.tool_version_refs.map((item) => item.resource_id),
          tool_version_refs: body.tool_version_refs.map((item) => ({ tool_id: item.resource_id, version: item.version })),
        }
        fakeVersions = [updated]
        return { ok: true, json: async () => updated }
      }
      if (url === '/api/v1/digital-employees/employee-1/versions/1/fork' && init?.method === 'POST') {
        const created = { ...fakeVersions[0], version: 2, status: 'DRAFT' }
        fakeVersions = [created, ...fakeVersions]
        return { ok: true, json: async () => created }
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
              <Route path="employees/:employeeId/skills/new" element={<SkillCreationPage />} />
              <Route path="employees/:employeeId/skills/:skillId/versions/:version/files" element={<SkillFileRouteTestPage />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </TenantProvider>
    </QueryClientProvider>,
  )
}

function SkillFileRouteTestPage() {
  const { skillId = '', version = '' } = useParams()
  return <div>Skill 文件工作区：{skillId} v{version}</div>
}

test('employee list creates an employee and opens its detail page', async () => {
  const user = userEvent.setup()
  renderEmployeeRoutes()

  expect(await screen.findByRole('heading', { name: '数字员工' })).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '新建数字员工' }))
  const descriptionGuidance = screen.getByText('用于员工目录和详情说明，不会作为模型运行指令。')
  expect(descriptionGuidance.closest('.ant-form-item')).toBe(screen.getByLabelText('员工描述').closest('.ant-form-item'))
  await user.type(screen.getByLabelText('员工名称'), '办公助手')
  await user.type(screen.getByLabelText('员工类型'), '行政支持')
  await user.type(screen.getByLabelText('员工描述'), '处理办公申请')
  await user.click(screen.getByRole('button', { name: '创建员工' }))

  expect(await screen.findByRole('heading', { name: '办公助手' })).toBeInTheDocument()
  expect(screen.getByText('处理办公申请')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: /归\s*档/ }))
  await user.click(await screen.findByRole('button', { name: '确认归档' }))
  expect(await screen.findByRole('heading', { name: '数字员工' })).toBeInTheDocument()
}, 15000)

test('employee detail edits basic information and hides actions for a member', async () => {
  const user = userEvent.setup()
  renderEmployeeRoutes('/enterprise/employees/employee-1')

  expect(await screen.findByRole('heading', { name: '办公助手' })).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '编辑资料' }))
  const descriptionGuidance = screen.getByText('用于员工目录和详情说明，不会作为模型运行指令。')
  expect(descriptionGuidance.closest('.ant-form-item')).toBe(screen.getByLabelText('员工描述').closest('.ant-form-item'))
  const description = screen.getByLabelText('员工描述')
  await user.clear(description)
  await user.type(description, '修改后的办公申请处理')
  await user.click(screen.getByRole('button', { name: '保存修改' }))
  expect(await screen.findByText('修改后的办公申请处理')).toBeInTheDocument()

  fireEvent.change(screen.getByLabelText('当前身份'), { target: { value: 'member-local' } })
  await waitFor(() => expect(screen.queryByRole('button', { name: '编辑资料' })).not.toBeInTheDocument())
  await waitFor(() => {
    const calls = vi.mocked(fetch).mock.calls
    expect(calls.some(([input, init]) => (
      String(input) === '/api/v1/digital-employees/employee-1'
      && new Headers(init?.headers).get('X-Fake-User') === 'member-local'
    ))).toBe(true)
  })
  expect(screen.getByRole('heading', { name: '办公助手' })).toBeInTheDocument()
}, 15000)

test('new employee version explains the scope of its system prompt', async () => {
  const user = userEvent.setup()
  renderEmployeeRoutes('/enterprise/employees/employee-1')

  expect(await screen.findByRole('heading', { name: '办公助手' })).toBeInTheDocument()
  await user.click(screen.getByRole('tab', { name: 'Skills' }))
  await user.click(await screen.findByRole('button', { name: '新建草稿版本' }))

  const promptGuidance = screen.getByText('定义员工每次处理请求时都遵守的角色、规则和边界。')
  expect(promptGuidance.closest('.ant-form-item')).toBe(screen.getByLabelText('系统指令').closest('.ant-form-item'))
})

test('employee workspace gives each resource type its own tab', async () => {
  fakeVersions = [{
    employee_id: 'employee-1', tenant_id: 'tenant-a', version: 2, status: 'DRAFT',
    system_prompt: '按员工流程处理', skill_ids: ['skill-1', 'skill-2'], tool_ids: ['tool-1'],
    skill_version_refs: [{ skill_id: 'skill-1', version: 2 }, { skill_id: 'skill-2', version: 1 }],
    tool_version_refs: [{ tool_id: 'tool-1', version: 3 }],
    execution_profile_id: 'profile-fake', execution_profile_version: 1,
    approval_policy: 'none', model_route_id: null,
  }]
  const user = userEvent.setup()
  renderEmployeeRoutes('/enterprise/employees/employee-1')

  expect(await screen.findByRole('heading', { name: '办公助手' })).toBeInTheDocument()
  expect(screen.queryByRole('tab', { name: /能力与资源/ })).not.toBeInTheDocument()
  await user.click(screen.getByRole('tab', { name: /Skills/ }))
  const skillSection = await screen.findByRole('region', { name: 'Skills' })
  expect(within(skillSection).getByText('工单流程')).toBeInTheDocument()
  expect(within(skillSection).getByText('退款规则')).toBeInTheDocument()
  expect(within(skillSection).queryByText('读取工单')).not.toBeInTheDocument()
  await user.click(screen.getByRole('tab', { name: /Tools/ }))
  const toolSection = await screen.findByRole('region', { name: 'Tools' })
  expect(within(toolSection).getByText('读取工单')).toBeInTheDocument()
  expect(within(toolSection).queryByText('工单流程')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '管理 Tools' })).toBeInTheDocument()
}, 10000)

test('adding a skill stays in the Skills tab and saves only skill bindings', async () => {
  fakeVersions = [{
    employee_id: 'employee-1', tenant_id: 'tenant-a', version: 1, status: 'DRAFT',
    system_prompt: '原系统指令', skill_ids: [], tool_ids: [],
    skill_version_refs: [], tool_version_refs: [],
    execution_profile_id: 'profile-fake', execution_profile_version: 1,
    model_route_id: null, approval_policy: 'required',
  }]
  const user = userEvent.setup()
  renderEmployeeRoutes('/enterprise/employees/employee-1')

  await user.click(await screen.findByRole('tab', { name: /Skills/ }))
  await user.click(await screen.findByRole('button', { name: '添加 Skill' }))
  expect(await screen.findByText('添加 Skill 到 办公助手 · v1')).toBeInTheDocument()
  expect(screen.getByRole('tab', { name: '本租户 Skills' })).toBeInTheDocument()
  expect(screen.queryByText('读取工单')).not.toBeInTheDocument()
  expect(screen.queryByRole('checkbox', { name: /草稿技能/ })).not.toBeInTheDocument()
  await user.click(screen.getByRole('checkbox', { name: /工单流程 v2/ }))
  await user.click(screen.getByRole('button', { name: '绑定所选 Skill' }))

  expect(await screen.findByText('工单流程')).toBeInTheDocument()
  expect(screen.queryByText('添加 Skill 到 办公助手 · v1')).not.toBeInTheDocument()
  const bindingCall = vi.mocked(fetch).mock.calls.find(([input, init]) => (
    String(input) === '/api/v1/digital-employees/employee-1/versions/1/skill-bindings'
    && init?.method === 'PUT'
  ))
  expect(bindingCall).toBeDefined()
  expect(JSON.parse(String(bindingCall?.[1]?.body))).toEqual({
    skill_version_refs: [{ resource_id: 'skill-1', version: 2 }],
  })
  expect(screen.getByRole('tab', { name: /Skills/ })).toHaveAttribute('aria-selected', 'true')
}, 10000)

test('Skills picker exposes a separate Skill draft creation action', async () => {
  fakeVersions = [{
    employee_id: 'employee-1', tenant_id: 'tenant-a', version: 1, status: 'DRAFT',
    system_prompt: '只读配置', skill_ids: [], tool_ids: [],
    skill_version_refs: [], tool_version_refs: [],
    execution_profile_id: 'profile-fake', execution_profile_version: 1,
    model_route_id: null, approval_policy: 'none',
  }]
  const user = userEvent.setup()
  renderEmployeeRoutes('/enterprise/employees/employee-1')

  await user.click(await screen.findByRole('tab', { name: /Skills/ }))
  await user.click(await screen.findByRole('button', { name: '添加 Skill' }))

  expect(await screen.findByRole('button', { name: '新建 Skill' })).toBeInTheDocument()
}, 10000)

test('new Skill creates a DRAFT then opens only its Skill file workspace', async () => {
  fakeVersions = [{
    employee_id: 'employee-1', tenant_id: 'tenant-a', version: 1, status: 'DRAFT',
    system_prompt: '不要被 Skill 创建流程修改', skill_ids: [], tool_ids: [],
    skill_version_refs: [], tool_version_refs: [],
    execution_profile_id: 'profile-fake', execution_profile_version: 1,
    model_route_id: null, approval_policy: 'required',
  }]
  const user = userEvent.setup()
  renderEmployeeRoutes('/enterprise/employees/employee-1')

  await user.click(await screen.findByRole('tab', { name: /Skills/ }))
  await user.click(await screen.findByRole('button', { name: '添加 Skill' }))
  await user.click(await screen.findByRole('button', { name: '新建 Skill' }))
  expect(await screen.findByRole('heading', { name: '新建 Skill' })).toBeInTheDocument()
  const instructionGuidance = screen.getByText('描述可复用的处理步骤、触发条件和例外；只有绑定此 Skill 的员工版本会应用。')
  expect(instructionGuidance.closest('.ant-form-item')).toBe(screen.getByLabelText('初始指令').closest('.ant-form-item'))
  await user.type(screen.getByLabelText('Skill 名称'), '新建的 Skill')
  await user.type(screen.getByLabelText('初始指令'), '初始指令')
  await user.click(screen.getByRole('button', { name: '创建草稿并编辑文件' }))

  expect(await screen.findByText('Skill 文件工作区：skill-created v1')).toBeInTheDocument()
  const createCall = vi.mocked(fetch).mock.calls.find(([input, init]) => (
    String(input) === '/api/v1/skills' && init?.method === 'POST'
  ))
  expect(JSON.parse(String(createCall?.[1]?.body))).toMatchObject({
    name: '新建的 Skill', skill_type: 'instruction', instruction: '初始指令',
  })
  expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes('/skills/skill-created/versions/1/publish'))).toBe(false)
  expect(vi.mocked(fetch).mock.calls.some(([input]) => String(input).includes('/versions/1/skill-bindings'))).toBe(false)
}, 15000)

test('adding a skill from a published version forks it and stays in the Skills tab', async () => {
  fakeVersions = [{
    employee_id: 'employee-1', tenant_id: 'tenant-a', version: 1, status: 'PUBLISHED',
    system_prompt: '已发布系统指令', skill_ids: ['skill-2'], tool_ids: ['tool-1'],
    skill_version_refs: [{ skill_id: 'skill-2', version: 1 }],
    tool_version_refs: [{ tool_id: 'tool-1', version: 3 }],
    execution_profile_id: 'profile-fake', execution_profile_version: 1,
    model_route_id: 'route-fixed', approval_policy: 'required',
  }]
  const user = userEvent.setup()
  renderEmployeeRoutes('/enterprise/employees/employee-1')

  await user.click(await screen.findByRole('tab', { name: /Skills/ }))
  await user.click(await screen.findByRole('button', { name: '添加 Skill' }))
  expect(await screen.findByText('添加 Skill 到 办公助手 · v2')).toBeInTheDocument()
  await user.click(screen.getByRole('checkbox', { name: /工单流程 v2/ }))
  await user.click(screen.getByRole('button', { name: '绑定所选 Skill' }))

  await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([input, init]) => (
    String(input) === '/api/v1/digital-employees/employee-1/versions/1/fork'
    && init?.method === 'POST'
  ))).toBe(true))
  const bindingCall = vi.mocked(fetch).mock.calls.find(([input, init]) => (
    String(input) === '/api/v1/digital-employees/employee-1/versions/2/skill-bindings'
    && init?.method === 'PUT'
  ))
  expect(JSON.parse(String(bindingCall?.[1]?.body))).toEqual({
    skill_version_refs: [
      { resource_id: 'skill-2', version: 1 },
      { resource_id: 'skill-1', version: 2 },
    ],
  })
  expect(screen.getByRole('tab', { name: /Skills/ })).toHaveAttribute('aria-selected', 'true')
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
}, 15000)

test('Skill picker hides every version of a resource already bound to this draft', async () => {
  fakeVersions = [{
    employee_id: 'employee-1', tenant_id: 'tenant-a', version: 1, status: 'DRAFT',
    system_prompt: '保持原指令', skill_ids: ['skill-1'], tool_ids: [],
    skill_version_refs: [{ skill_id: 'skill-1', version: 2 }], tool_version_refs: [],
    execution_profile_id: 'profile-fake', execution_profile_version: 1,
    model_route_id: null, approval_policy: 'none',
  }]
  const user = userEvent.setup()
  renderEmployeeRoutes('/enterprise/employees/employee-1')

  await user.click(await screen.findByRole('tab', { name: /Skills/ }))
  await user.click(await screen.findByRole('button', { name: '添加 Skill' }))

  expect(screen.queryByRole('checkbox', { name: /工单流程/ })).not.toBeInTheDocument()
  expect(screen.getByRole('checkbox', { name: /退款规则 v1/ })).toBeInTheDocument()
}, 12000)

test('Hub Skill picker shows authorized dependencies and copies before binding', async () => {
  fakeVersions = [{
    employee_id: 'employee-1', tenant_id: 'tenant-a', version: 1, status: 'DRAFT',
    system_prompt: '保留系统指令', skill_ids: [], tool_ids: [],
    skill_version_refs: [], tool_version_refs: [],
    execution_profile_id: 'profile-fake', execution_profile_version: 1,
    model_route_id: null, approval_policy: 'required',
  }]
  const user = userEvent.setup()
  renderEmployeeRoutes('/enterprise/employees/employee-1')

  await user.click(await screen.findByRole('tab', { name: /Skills/ }))
  await user.click(await screen.findByRole('button', { name: '添加 Skill' }))
  await user.click(await screen.findByRole('tab', { name: '资源中心' }))
  await user.click(await screen.findByRole('radio', { name: /共享客服 Skill.*租户 B/ }))
  expect(await screen.findByText('TOOL · tool-shared · v2 · 必需')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '导入并绑定 Skill' }))

  const importCall = vi.mocked(fetch).mock.calls.find(([input, init]) => (
    String(input) === '/api/v1/hub/imports' && init?.method === 'POST'
  ))
  expect(JSON.parse(String(importCall?.[1]?.body))).toEqual({
    hub_resource_id: 'hub-skill-1', hub_version: 4, mode: 'COPY',
  })
  const bindingCall = vi.mocked(fetch).mock.calls.find(([input, init]) => (
    String(input) === '/api/v1/digital-employees/employee-1/versions/1/skill-bindings'
    && init?.method === 'PUT'
  ))
  expect(JSON.parse(String(bindingCall?.[1]?.body))).toEqual({
    skill_version_refs: [{ resource_id: 'skill-copy', version: 1 }],
  })
}, 15000)

test('adding a Tool opens a Tool-only picker and saves only tool bindings', async () => {
  fakeVersions = [{
    employee_id: 'employee-1', tenant_id: 'tenant-a', version: 1, status: 'DRAFT',
    system_prompt: '保留系统指令', skill_ids: ['skill-1'], tool_ids: [],
    skill_version_refs: [{ skill_id: 'skill-1', version: 2 }], tool_version_refs: [],
    execution_profile_id: 'profile-fake', execution_profile_version: 1,
    model_route_id: null, approval_policy: 'required',
  }]
  const user = userEvent.setup()
  renderEmployeeRoutes('/enterprise/employees/employee-1')

  await user.click(await screen.findByRole('tab', { name: /Tools/ }))
  await user.click(await screen.findByRole('button', { name: '绑定 Tool' }))
  expect(await screen.findByText('添加 Tool 到 办公助手 · v1')).toBeInTheDocument()
  expect(screen.getByRole('tab', { name: '本租户 Tools' })).toBeInTheDocument()
  expect(screen.queryByRole('checkbox', { name: /工单流程/ })).not.toBeInTheDocument()
  await user.click(screen.getByRole('checkbox', { name: /读取工单/ }))
  await user.click(screen.getByRole('button', { name: '绑定所选 Tool' }))

  const bindingCall = vi.mocked(fetch).mock.calls.find(([input, init]) => (
    String(input) === '/api/v1/digital-employees/employee-1/versions/1/tool-bindings'
    && init?.method === 'PUT'
  ))
  expect(JSON.parse(String(bindingCall?.[1]?.body))).toEqual({
    tool_version_refs: [{ resource_id: 'tool-1', version: 3 }],
  })
  expect(screen.getByRole('tab', { name: /Tools/ })).toHaveAttribute('aria-selected', 'true')
}, 15000)

test('removing a Skill updates only the current draft Skill binding', async () => {
  fakeVersions = [{
    employee_id: 'employee-1', tenant_id: 'tenant-a', version: 1, status: 'DRAFT',
    system_prompt: '保留指令', skill_ids: ['skill-1'], tool_ids: ['tool-1'],
    skill_version_refs: [{ skill_id: 'skill-1', version: 2 }],
    tool_version_refs: [{ tool_id: 'tool-1', version: 3 }],
    execution_profile_id: 'profile-fake', execution_profile_version: 1,
    model_route_id: 'route-fixed', approval_policy: 'required',
  }]
  const user = userEvent.setup()
  renderEmployeeRoutes('/enterprise/employees/employee-1')

  await user.click(await screen.findByRole('tab', { name: /Skills/ }))
  const skills = await screen.findByRole('region', { name: 'Skills' })
  await user.click(within(skills).getByRole('button', { name: '移除' }))
  await user.click(await screen.findByRole('button', { name: '确认移除' }))

  await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([input, init]) => (
    String(input) === '/api/v1/digital-employees/employee-1/versions/1/skill-bindings'
    && init?.method === 'PUT'
    && JSON.stringify(JSON.parse(String(init.body))) === JSON.stringify({ skill_version_refs: [] })
  ))).toBe(true))
  expect(await within(skills).findByText('此版本还没有绑定 Skill')).toBeInTheDocument()
  expect(fakeVersions[0].tool_version_refs).toEqual([{ tool_id: 'tool-1', version: 3 }])
}, 15000)

test('removing a Tool updates only the current draft Tool binding', async () => {
  fakeVersions = [{
    employee_id: 'employee-1', tenant_id: 'tenant-a', version: 1, status: 'DRAFT',
    system_prompt: '保留指令', skill_ids: ['skill-1'], tool_ids: ['tool-1'],
    skill_version_refs: [{ skill_id: 'skill-1', version: 2 }],
    tool_version_refs: [{ tool_id: 'tool-1', version: 3 }],
    execution_profile_id: 'profile-fake', execution_profile_version: 1,
    model_route_id: 'route-fixed', approval_policy: 'required',
  }]
  const user = userEvent.setup()
  renderEmployeeRoutes('/enterprise/employees/employee-1')

  await user.click(await screen.findByRole('tab', { name: /Tools/ }))
  const tools = await screen.findByRole('region', { name: 'Tools' })
  await user.click(within(tools).getByRole('button', { name: '移除' }))
  await user.click(await screen.findByRole('button', { name: '确认移除' }))

  await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([input, init]) => (
    String(input) === '/api/v1/digital-employees/employee-1/versions/1/tool-bindings'
    && init?.method === 'PUT'
    && JSON.stringify(JSON.parse(String(init.body))) === JSON.stringify({ tool_version_refs: [] })
  ))).toBe(true))
  expect(await within(tools).findByText('此版本还没有绑定 Tool')).toBeInTheDocument()
  expect(fakeVersions[0].skill_version_refs).toEqual([{ skill_id: 'skill-1', version: 2 }])
}, 15000)

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
