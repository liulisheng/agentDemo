import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, expect, test, vi } from 'vitest'

import { ModelManagementPage, ModelTestResultPanel } from './ModelManagementPage'
import type { ModelTestRecord } from '../../api/client'
import { ManagementLayout } from '../management/ManagementLayout'
import { TenantProvider } from '../management/TenantContext'

const testRecord: ModelTestRecord = {
  test_id: 'test-1',
  tenant_id: 'tenant-a',
  connection_id: 'connection-1',
  route_id: 'route-1',
  model_id: 'local-model-a',
  test_kind: 'MODEL',
  state: 'SUCCEEDED',
  started_at: '2026-11-21T00:00:00Z',
  finished_at: '2026-11-21T00:00:01Z',
  latency_ms: 830,
  provider_request_id: 'provider-request-1',
  input_tokens: 18,
  output_tokens: 2,
  total_tokens: 20,
  usage_status: 'EXACT',
  error_category: null,
  created_at: '2026-11-21T00:00:00Z',
  updated_at: '2026-11-21T00:00:01Z',
}

const connection = {
  connection_id: 'connection-1',
  tenant_id: 'tenant-a',
  name: '本地推理服务',
  provider_kind: 'openai-compatible',
  protocol: 'openai-chat-completions',
  endpoint: 'http://host.docker.internal:63072/v1',
  secret_ref: { secret_id: 'local-key', scope: 'tenant-a', provider: 'environment' },
  catalog_mode: 'MANUAL',
  status: 'ACTIVE',
  version: 1,
}

const route = {
  route_id: 'route-1',
  tenant_id: 'tenant-a',
  alias: '客服主模型',
  status: 'ACTIVE',
  route_version: 2,
  authorization_source: 'platform',
  profile: {
    model_name: 'local-model-a',
    catalog_model_id: 'local-model-a',
    provider: 'openai-compatible',
    endpoint: 'http://host.docker.internal:63072/v1',
    protocol: 'openai-chat-completions',
    connection_id: 'connection-1',
    parameters: {},
    secret_ref: { secret_id: 'local-key', scope: 'tenant-a', provider: 'environment' },
  },
}

const responses: Record<string, unknown> = {}

beforeEach(() => {
  window.localStorage.clear()
  window.localStorage.setItem('agent-platform.fake-user', 'admin-local')
  Object.keys(responses).forEach((key) => delete responses[key])
  responses['GET /api/v1/model-connections'] = { items: [connection] }
  responses['GET /api/v1/model-routes'] = { items: [route] }
  responses['GET /api/v1/model-connections/connection-1/models'] = {
    items: [
      {
        connection_id: 'connection-1', tenant_id: 'tenant-a', model_id: 'local-model-a',
        display_name: '本地对话模型', capabilities: { chat: true }, context_window: 32768,
        catalog_source: 'MANUAL', status: 'ACTIVE', last_seen_at: null,
      },
      {
        connection_id: 'connection-1', tenant_id: 'tenant-a', model_id: 'local-model-b',
        display_name: '本地推理模型', capabilities: { chat: true, reasoning: true }, context_window: null,
        catalog_source: 'MANUAL', status: 'ACTIVE', last_seen_at: null,
      },
    ],
  }
  responses['POST /api/v1/model-routes/route-1/tests'] = { test_id: 'test-1', state: 'QUEUED' }
  responses['GET /api/v1/model-tests/test-1'] = testRecord
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const method = init?.method ?? 'GET'
    const url = String(input)
    const key = `${method} ${url}`
    const fakeUser = new Headers(init?.headers).get('X-Fake-User')
    if (fakeUser === 'admin-other' && method === 'GET' && url === '/api/v1/model-connections') {
      return { ok: true, status: 200, json: async () => ({ items: [] }) }
    }
    if (fakeUser === 'admin-other' && method === 'GET' && url === '/api/v1/model-routes') {
      return { ok: true, status: 200, json: async () => ({ items: [] }) }
    }
    if (responses[key] !== undefined) {
      return { ok: true, status: method === 'POST' ? 202 : 200, json: async () => responses[key] }
    }
    return {
      ok: false,
      status: 404,
      json: async () => ({ code: 'RESOURCE_NOT_FOUND', message: 'not found' }),
    }
  }))
})

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <TenantProvider>
        <MemoryRouter initialEntries={['/enterprise/models']}>
          <Routes>
            <Route path="/enterprise" element={<ManagementLayout />}>
              <Route path="models" element={<ModelManagementPage />} />
            </Route>
          </Routes>
        </MemoryRouter>
      </TenantProvider>
    </QueryClientProvider>,
  )
}

test('shows a tenant connection with multiple model IDs and its authorization route', async () => {
  renderPage()

  expect(await screen.findByText('本地推理服务')).toBeInTheDocument()
  expect(await screen.findByText('本地对话模型')).toBeInTheDocument()
  expect(screen.getByText('本地推理模型')).toBeInTheDocument()
  await userEvent.click(screen.getByRole('tab', { name: '模型路由' }))
  expect(await screen.findByText('客服主模型')).toBeInTheDocument()
  expect(screen.getByText('平台授权')).toBeInTheDocument()
  expect(screen.getAllByText('local-model-a').length).toBeGreaterThan(0)
})

test('reloads tenant connections after switching the current identity', async () => {
  const user = userEvent.setup()
  renderPage()
  expect(await screen.findByText('本地推理服务')).toBeInTheDocument()

  await user.selectOptions(screen.getByLabelText('当前身份'), 'admin-other')

  expect(await screen.findByText(/还没有模型连接/)).toBeInTheDocument()
  expect(vi.mocked(fetch).mock.calls.some(([input, init]) => (
    String(input) === '/api/v1/model-connections' &&
    new Headers(init?.headers).get('X-Fake-User') === 'admin-other'
  ))).toBe(true)
})

test('validates and creates a tenant model connection with Docker Worker guidance', async () => {
  const user = userEvent.setup()
  responses['POST /api/v1/model-connections'] = {
    ...connection,
    connection_id: 'connection-created',
    name: '新建本地连接',
    version: 1,
  }
  renderPage()
  expect(await screen.findByText(/host.docker.internal/)).toBeInTheDocument()

  await user.click(screen.getAllByRole('button', { name: '添加连接' })[0])
  await user.click(screen.getByRole('button', { name: '创建连接' }))
  expect(await screen.findByText('请输入连接名称')).toBeInTheDocument()
  expect(screen.getByText('请输入模型服务地址')).toBeInTheDocument()

  await user.type(screen.getByLabelText('连接名称'), '新建本地连接')
  await user.type(screen.getByLabelText('Base URL'), 'http://host.docker.internal:63072/v1')
  await user.type(screen.getByLabelText('凭据引用 ID（可选）'), 'local-secret-ref')
  await user.click(screen.getByRole('button', { name: '创建连接' }))

  await waitFor(() => {
    const call = vi.mocked(fetch).mock.calls.find(([input, init]) => (
      String(input) === '/api/v1/model-connections' && init?.method === 'POST'
    ))
    expect(call).toBeDefined()
    expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
      name: '新建本地连接',
      endpoint: 'http://host.docker.internal:63072/v1',
      secret_id: 'local-secret-ref',
    })
  })
}, 30000)

test('adds a manual model ID to the selected connection', async () => {
  const user = userEvent.setup()
  responses['POST /api/v1/model-connections/connection-1/models'] = {
    connection_id: 'connection-1', tenant_id: 'tenant-a', model_id: 'local-model-c',
    display_name: 'Local C', capabilities: {}, context_window: null,
    catalog_source: 'MANUAL', status: 'ACTIVE', last_seen_at: null,
  }
  renderPage()

  await screen.findByText('本地对话模型')
  await user.click(await screen.findByRole('button', { name: '添加模型' }))
  const modelDrawer = await screen.findByRole('dialog', { name: '添加模型 ID' })
  await user.type(within(modelDrawer).getByLabelText('外部模型 ID'), 'local-model-c')
  await user.type(within(modelDrawer).getByLabelText('显示名称'), 'Local C')
  await user.click(within(modelDrawer).getByRole('button', { name: '添加模型' }))
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([input, init]) => (
    String(input) === '/api/v1/model-connections/connection-1/models' && init?.method === 'POST'
  ))).toBe(true))
  await waitFor(() => expect(screen.queryByRole('dialog', { name: '添加模型 ID' })).not.toBeInTheDocument())

}, 15000)

test('opens a route form for the selected model and saves its binding', async () => {
  const user = userEvent.setup()
  responses['POST /api/v1/model-routes'] = route
  renderPage()
  const modelRow = (await screen.findByText('本地对话模型')).closest('tr')
  expect(modelRow).not.toBeNull()
  const createRouteButton = within(modelRow as HTMLElement).getByRole('button', { name: '创建路由' })
  expect(createRouteButton).toBeEnabled()
  fireEvent.click(createRouteButton)

  expect(await screen.findByText('连接中的模型')).toBeInTheDocument()
  const routeDrawer = screen.getByRole('dialog', { name: '创建模型路由' })
  await user.clear(within(routeDrawer).getByLabelText('路由别名'))
  await user.type(within(routeDrawer).getByLabelText('路由别名'), '客服回复路由')
  await user.click(within(routeDrawer).getByRole('button', { name: '创建路由' }))

  await waitFor(() => {
    const call = vi.mocked(fetch).mock.calls.find(([input, init]) => (
      String(input) === '/api/v1/model-routes' && init?.method === 'POST'
    ))
    expect(call).toBeDefined()
    expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
      alias: '客服回复路由',
      connection_id: 'connection-1',
      catalog_model_id: 'local-model-a',
      model_name: 'local-model-a',
    })
  })
}, 15000)

test('does not create a real model test until confirmation and shows the result', async () => {
  const user = userEvent.setup()
  renderPage()

  await user.click(await screen.findByRole('tab', { name: '模型路由' }))
  const routeRow = await screen.findByText('客服主模型')
  const row = routeRow.closest('tr')
  expect(row).not.toBeNull()
  await user.click(within(row as HTMLElement).getByRole('button', { name: '测试模型' }))
  expect(await screen.findByText(/可能产生供应商费用/)).toBeInTheDocument()
  expect(vi.mocked(fetch).mock.calls.some(([input, init]) => (
    String(input) === '/api/v1/model-routes/route-1/tests' && init?.method === 'POST'
  ))).toBe(false)

  await user.click(screen.getByRole('button', { name: 'Close' }))
  expect(vi.mocked(fetch).mock.calls.some(([input, init]) => (
    String(input) === '/api/v1/model-routes/route-1/tests' && init?.method === 'POST'
  ))).toBe(false)

  await user.click(within(row as HTMLElement).getByRole('button', { name: '测试模型' }))
  await user.click(await screen.findByRole('button', { name: '确认并测试模型' }))

  await waitFor(() => {
    const call = vi.mocked(fetch).mock.calls.find(([input, init]) => (
      String(input) === '/api/v1/model-routes/route-1/tests' && init?.method === 'POST'
    ))
    expect(call).toBeDefined()
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ confirmed_cost: true })
  })
  expect(await screen.findByText('provider-request-1')).toBeInTheDocument()
  expect(document.querySelector('.model-test-result')).toHaveTextContent('可用')
  expect(screen.getByText('830 ms')).toBeInTheDocument()
  expect(screen.getByText('EXACT')).toBeInTheDocument()
}, 15000)

const statusCases: [ModelTestRecord['state'], string][] = [
  ['QUEUED', '排队中'],
  ['RUNNING', '测试中'],
  ['SUCCEEDED', '可用'],
  ['FAILED', '测试未通过'],
  ['UNKNOWN', '无法确认上游是否完成请求'],
]

test.each(statusCases)('shows the %s model test state', (state, text) => {
  const record = {
    ...testRecord,
    state,
    error_category: state === 'FAILED' ? 'MODEL_NOT_FOUND' : null,
  } satisfies ModelTestRecord
  render(<ModelTestResultPanel record={record} />)
  expect(screen.getByText(text)).toBeInTheDocument()
  if (state === 'FAILED') expect(screen.getByText('MODEL_NOT_FOUND')).toBeInTheDocument()
}, 15000)
