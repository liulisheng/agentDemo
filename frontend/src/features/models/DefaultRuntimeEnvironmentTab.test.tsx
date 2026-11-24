import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, expect, test, vi } from 'vitest'

import { DefaultRuntimeEnvironmentTab } from './DefaultRuntimeEnvironmentTab'
import { TenantProvider } from '../management/TenantContext'

let versions: Record<string, unknown>[] = []
let latestProfiles: Record<string, unknown>[] = []
let failEffectiveRouteOnce = false
let emptyRuntimeCatalogs = false
const profileV1 = {
  id: 'profile-a', tenant_id: 'tenant-a', version: 1, engine_name: 'codex_cli',
  engine_available: true, engine_execution_mode: 'SIMULATION', model_alias: null,
  tool_policy: [], context_policy: 'default', model_route_id: null,
  workspace_profile_id: null, workspace_profile_version: null,
  sandbox_profile_id: null, sandbox_profile_version: null, approval_policy: 'none', status: 'PUBLISHED',
}
const profileV2Draft = { ...profileV1, version: 2, engine_name: 'deepseek_harness', status: 'DRAFT' }
const route = {
  route_id: 'route-a', tenant_id: 'tenant-a', alias: '本地模型', status: 'ACTIVE',
  route_version: 1, authorization_source: 'platform',
  profile: {
    model_name: 'model-a', provider: 'openai-compatible', protocol: 'openai-chat-completions',
    endpoint: 'https://model.invalid/v1', connection_id: 'connection-a', catalog_model_id: 'model-a',
  },
}

beforeEach(() => {
  window.localStorage.clear()
  window.localStorage.setItem('agent-platform.fake-user', 'admin-local')
  versions = [profileV1]
  latestProfiles = [profileV1]
  failEffectiveRouteOnce = false
  emptyRuntimeCatalogs = false
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    const method = init?.method ?? 'GET'
    const fakeUser = new Headers(init?.headers).get('X-Fake-User')
    if (method === 'GET' && url === '/api/v1/engine-providers') {
      return { ok: true, status: 200, json: async () => ({ items: [
        { engine_name: 'codex_cli', display_name: 'Codex CLI', supported_model_protocols: ['openai-responses'], capabilities: {}, execution_mode: 'SIMULATION' },
        { engine_name: 'deepseek_harness', display_name: 'DeepSeek Harness', supported_model_protocols: ['openai-chat-completions'], capabilities: {}, execution_mode: 'SIMULATION' },
      ] }) }
    }
    if (method === 'GET' && url === '/api/v1/execution-profiles') {
      return { ok: true, status: 200, json: async () => ({ items: emptyRuntimeCatalogs ? [] : latestProfiles }) }
    }
    if (method === 'GET' && url === '/api/v1/execution-profiles/profile-a/versions') {
      return { ok: true, status: 200, json: async () => ({ items: versions }) }
    }
    if (method === 'GET' && url === '/api/v1/model-routes/selectable') {
      return { ok: true, status: 200, json: async () => ({ items: emptyRuntimeCatalogs ? [] : [route] }) }
    }
    if (method === 'GET' && url === '/api/v1/execution-profile-bindings/effective') {
      return { ok: true, status: 200, json: async () => ({
        binding: { profile_id: 'profile-a', profile_version: 1 },
        profile: { profile_id: 'profile-a', version: 1, engine_name: 'codex_cli', status: 'PUBLISHED' },
        source: 'TENANT_DEFAULT',
      }) }
    }
    if (method === 'GET' && url === '/api/v1/model-route-bindings/effective') {
      if (failEffectiveRouteOnce) {
        failEffectiveRouteOnce = false
        return { ok: false, status: 503, json: async () => ({ message: '模型默认读取暂不可用' }) }
      }
      return { ok: true, status: 200, json: async () => ({
        binding: { route_id: 'route-a' },
        route: { route_id: 'route-a', route_version: 1, alias: '本地模型', model_name: 'model-a', provider: 'openai-compatible', protocol: 'openai-chat-completions', status: 'ACTIVE' },
        source: 'TENANT_DEFAULT',
      }) }
    }
    if (method === 'GET' && url === '/api/v1/runtime-strategy-bindings/effective') {
      return { ok: true, status: 200, json: async () => ({
        binding: null,
        runtime_options: { reasoning_mode: 'AUTO', reasoning_effort: 'AUTO', response_verbosity: 'STANDARD' },
        source: 'SYSTEM_DEFAULT',
      }) }
    }
    if (method === 'GET' && url.includes('/runtime-strategy-bindings/user')) {
      return { ok: true, status: 200, json: async () => ({ binding: { binding_id: 's-user', scope_type: 'user', scope_id: fakeUser ?? '', runtime_options: { reasoning_mode: 'AUTO', reasoning_effort: 'AUTO', response_verbosity: 'STANDARD' }, status: 'ACTIVE' } }) }
    }
    if (method === 'GET' && url.includes('/runtime-strategy-bindings/tenant')) {
      return { ok: true, status: 200, json: async () => ({ binding: { binding_id: 's-tenant', scope_type: 'tenant', scope_id: 'tenant-a', runtime_options: { reasoning_mode: 'AUTO', reasoning_effort: 'AUTO', response_verbosity: 'STANDARD' }, status: 'ACTIVE' } }) }
    }
    if (method === 'GET' && url.includes('/execution-profile-bindings/user')) {
      return { ok: true, status: 200, json: async () => ({ binding: fakeUser === 'member-local' ? null : { binding_id: 'b-user', scope_type: 'user', scope_id: 'admin-local', profile_id: 'profile-a', profile_version: 1, status: 'ACTIVE' } }) }
    }
    if (method === 'GET' && url.includes('/execution-profile-bindings/tenant')) {
      return { ok: true, status: 200, json: async () => ({ binding: { binding_id: 'b-tenant', scope_type: 'tenant', scope_id: 'tenant-a', profile_id: 'profile-a', profile_version: 1, status: 'ACTIVE' } }) }
    }
    if (method === 'GET' && url.includes('/model-route-bindings/user')) {
      return { ok: true, status: 200, json: async () => ({ binding: { binding_id: 'r-user', scope_type: 'user', scope_id: fakeUser ?? '', route_id: 'route-a', status: 'ACTIVE' } }) }
    }
    if (method === 'GET' && url.includes('/model-route-bindings/tenant')) {
      return { ok: true, status: 200, json: async () => ({ binding: { binding_id: 'r-tenant', scope_type: 'tenant', scope_id: 'tenant-a', route_id: 'route-a', status: 'ACTIVE' } }) }
    }
    if (method === 'POST' && url.endsWith('/versions/1/fork')) {
      versions = [...versions, profileV2Draft]
      latestProfiles = [profileV2Draft]
      return { ok: true, status: 201, json: async () => profileV2Draft }
    }
    if (method === 'PUT' && url.endsWith('/versions/2')) {
      const body = JSON.parse(String(init?.body))
      versions = versions.map((item) => Number(item.version) === 2 ? { ...profileV2Draft, engine_name: body.engine_name } : item)
      latestProfiles = [versions[1]]
      return { ok: true, status: 200, json: async () => versions[1] }
    }
    if (method === 'POST' && url.endsWith('/versions/2/publish')) {
      versions = versions.map((item) => Number(item.version) === 2 ? { ...item, status: 'PUBLISHED' } : item)
      latestProfiles = [versions[1]]
      return { ok: true, status: 200, json: async () => versions[1] }
    }
    if (method === 'PUT') {
      return { ok: true, status: 200, json: async () => ({ binding: {} }) }
    }
    return { ok: false, status: 404, json: async () => ({ code: 'RESOURCE_NOT_FOUND', message: url }) }
  }))
})

function renderTab() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <TenantProvider>
        <MemoryRouter><DefaultRuntimeEnvironmentTab /></MemoryRouter>
      </TenantProvider>
    </QueryClientProvider>,
  )
}

test('saves engine and model defaults independently and keeps the route selection', async () => {
  const user = userEvent.setup()
  renderTab()

  expect(await screen.findByText('默认运行环境', { exact: false })).toBeInTheDocument()
  const scope = await screen.findByRole('combobox', { name: '默认作用域' })
  expect(screen.getByText('个人默认（当前用户）')).toBeInTheDocument()
  expect(document.querySelector('.runtime-default-layout')).toBeInTheDocument()
  expect(document.querySelector('.execution-profile-catalog-card')).toBeInTheDocument()
  const engine = screen.getByRole('combobox', { name: '默认执行引擎（ExecutionProfile）' })
  await user.click(engine)
  await user.click(await screen.findByRole('option', { name: 'codex_cli · profile-a · v1' }))
  await user.click(screen.getByRole('button', { name: '保存引擎默认' }))

  await waitFor(() => {
    const call = vi.mocked(fetch).mock.calls.find(([input, init]) => String(input) === '/api/v1/execution-profile-bindings/user' && init?.method === 'PUT')
    expect(call).toBeDefined()
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ profile_id: 'profile-a', profile_version: 1 })
  })
  expect(vi.mocked(fetch).mock.calls.some(([input, init]) => String(input) === '/api/v1/model-route-bindings/user' && init?.method === 'PUT')).toBe(false)
  await user.click(screen.getByRole('button', { name: '保存模型默认' }))
  await waitFor(() => {
    const call = vi.mocked(fetch).mock.calls.find(([input, init]) => String(input) === '/api/v1/model-route-bindings/user' && init?.method === 'PUT')
    expect(call).toBeDefined()
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ route_id: 'route-a' })
  })
  expect(screen.getByText(/Claude Code 当前未注册/)).toBeInTheDocument()
}, 15000)

test('members can edit personal defaults but cannot edit tenant defaults', async () => {
  window.localStorage.setItem('agent-platform.fake-user', 'member-local')
  const user = userEvent.setup()
  renderTab()

  const scope = await screen.findByRole('combobox', { name: '默认作用域' })
  await user.click(scope)
  expect(screen.queryByRole('option', { name: '租户默认（成员回退值）' })).not.toBeInTheDocument()
  expect(screen.getByRole('combobox', { name: '默认执行引擎（ExecutionProfile）' })).toBeEnabled()
  expect(screen.getByRole('combobox', { name: '默认模型（ModelRoute）' })).toBeEnabled()
  expect(screen.getByRole('button', { name: '保存引擎默认' })).toBeEnabled()
  expect(screen.getByRole('button', { name: '保存模型默认' })).toBeEnabled()
  expect(screen.getByText(/租户默认只读：/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '保存租户默认' })).not.toBeInTheDocument()
})

test('saves the prototype answer strategy as a scoped default', async () => {
  const user = userEvent.setup()
  renderTab()

  await screen.findByText('默认运行环境', { exact: false })
  expect(document.querySelector('.runtime-default-form > .runtime-strategy-panel')).toBeInTheDocument()
  await user.selectOptions(screen.getByRole('combobox', { name: '思考模式' }), 'DEEP')
  await user.selectOptions(screen.getByRole('combobox', { name: '推理强度' }), 'HIGH')
  await user.click(screen.getByRole('button', { name: '保存回答策略' }))

  await waitFor(() => {
    const call = vi.mocked(fetch).mock.calls.find(([input, init]) => String(input) === '/api/v1/runtime-strategy-bindings/user' && init?.method === 'PUT')
    expect(call).toBeDefined()
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      runtime_options: {
        reasoning_mode: 'DEEP',
        reasoning_effort: 'HIGH',
        response_verbosity: 'STANDARD',
      },
    })
  })
})

test('shows a retryable page error when the effective ModelRoute default cannot be read', async () => {
  failEffectiveRouteOnce = true
  const user = userEvent.setup()
  renderTab()

  expect(await screen.findByText('无法读取默认运行环境')).toBeInTheDocument()
  expect(screen.getByText('模型默认读取暂不可用')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: /重\s*试/ }))
  expect(await screen.findByText('默认运行环境', { exact: false })).toBeInTheDocument()
  expect(vi.mocked(fetch).mock.calls.filter(([input]) => String(input) === '/api/v1/model-route-bindings/effective')).toHaveLength(2)
}, 15000)

test('shows Profile and ModelRoute empty states when both catalogs are empty', async () => {
  emptyRuntimeCatalogs = true
  latestProfiles = []
  versions = []
  renderTab()

  expect(await screen.findByRole('button', { name: '创建第一个 Profile' })).toBeInTheDocument()
  expect(screen.getByText(/没有可用模型路由/)).toBeInTheDocument()
  expect(screen.getByRole('link', { name: '模型路由' })).toHaveAttribute('href', '/enterprise/models?tab=routes')
}, 15000)

test('marks historical environment Profile fields unavailable and excludes them from defaults', async () => {
  const user = userEvent.setup()
  const legacy = { ...profileV1, workspace_profile_id: 'workspace-legacy', runtime_policy_supported: false, unsupported_runtime_fields: ['workspace_profile'] }
  latestProfiles = [legacy]
  versions = [legacy]
  renderTab()

  await screen.findByRole('combobox', { name: '默认执行引擎（ExecutionProfile）' })
  expect(await screen.findByText('环境策略待接入')).toBeInTheDocument()
  await user.click(screen.getByRole('combobox', { name: '默认执行引擎（ExecutionProfile）' }))
  expect(screen.queryByRole('option', { name: 'codex_cli · profile-a · v1' })).not.toBeInTheDocument()
}, 15000)

test('creates and publishes a new Profile version under the existing ID', async () => {
  const user = userEvent.setup()
  renderTab()

  await user.click(await screen.findByRole('button', { name: '创建新版本' }))
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([input, init]) => String(input).endsWith('/versions/1/fork') && init?.method === 'POST')).toBe(true))
  expect(await screen.findByRole('dialog')).toBeInTheDocument()
  expect(await screen.findByText('profile-a · v2 草稿')).toBeInTheDocument()
  const engine = screen.getByRole('combobox', { name: 'EngineProvider' })
  fireEvent.mouseDown(engine)
  await user.click(await screen.findByRole('option', { name: 'DeepSeek Harness · 演示模式' }))
  await user.click(screen.getByRole('button', { name: '保存草稿' }))
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([input, init]) => String(input).endsWith('/versions/2') && init?.method === 'PUT')).toBe(true))
  await waitFor(() => expect(screen.getByRole('button', { name: '保存草稿' })).not.toHaveClass('ant-btn-loading'))
  await user.click(screen.getByRole('button', { name: '发布此版本' }))
  await waitFor(() => expect(
    vi.mocked(fetch).mock.calls.some(([input, init]) => String(input).endsWith('/versions/2/publish') && init?.method === 'POST'),
    JSON.stringify(vi.mocked(fetch).mock.calls.map(([input, init]) => [String(input), init?.method])),
  ).toBe(true))
}, 15000)
