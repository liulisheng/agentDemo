import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, expect, test, vi } from 'vitest'

import { ResourceCenterPage } from './ResourceCenterPage'
import { TenantProvider } from '../management/TenantContext'

const summary = {
  hub_resource_id: 'hub-1',
  resource_type: 'SKILL',
  name: '工单 Skill',
  description: '工单处理规范',
  publisher_name: '发布租户',
  visibility: 'PLATFORM',
  latest_version: 1,
  available_modes: [] as string[],
  is_publisher: false,
}

let resourceModes: string[] = []
let publishedByMe = false
let fetchCalls: { url: string; init?: RequestInit }[] = []

beforeEach(() => {
  window.localStorage.clear()
  resourceModes = []
  publishedByMe = false
  fetchCalls = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    fetchCalls.push({ url, init })
    if (url.startsWith('/api/v1/hub/resources?')) {
      const owned = url.includes('scope=OWNED')
      return {
        ok: true,
        json: async () => ({
          items: [{ ...summary, available_modes: resourceModes, is_publisher: owned || publishedByMe }],
        }),
      }
    }
    if (url === '/api/v1/hub/resources/hub-1/versions/1') {
      return {
        ok: true,
        json: async () => ({
          ...summary,
          available_modes: resourceModes,
          version: 1,
          manifest_sha256: 'a'.repeat(64),
          dependency_count: 1,
        }),
      }
    }
    if (url === '/api/v1/hub/resources/hub-1/versions/1/manifest?mode=REFERENCE') {
      return {
        ok: true,
        json: async () => ({
          hub_resource_id: 'hub-1', version: 1, manifest_sha256: 'a'.repeat(64),
          manifest: {
            resource: { type: 'SKILL', name: '工单 Skill', version: 1 },
            content: { content_format: 'FILE_PACKAGE', files: [{ file_path: 'SKILL.md', size_bytes: 20, content_sha256: 'b'.repeat(64), media_asset_id: 'internal-asset' }] },
            dependencies: [{ sequence_no: 1, type: 'TOOL', resource_id: 'hub-tool', version: 3, required: true }],
            requires_recipient_credentials: false,
          },
        }),
      }
    }
    if (url.startsWith('/api/v1/hub/resources/hub-1/versions/1/grants')) {
      return { ok: true, json: async () => ({ items: [] }) }
    }
    if (url.startsWith('/api/v1/hub/imports?')) {
      return {
        ok: true,
        json: async () => ({ items: [{
          import_id: 'import-1', hub_resource_id: 'hub-1', hub_version: 1,
          mode: 'REFERENCE', local_resource_type: 'SKILL', local_resource_id: 'local-skill',
          local_resource_version: 1, manifest_sha256: 'a'.repeat(64), status: 'ACTIVE',
        }] }),
      }
    }
    if (url === '/api/v1/hub/imports' && init?.method === 'POST') {
      return { ok: true, json: async () => ({ import_id: 'import-1', mode: 'REFERENCE' }) }
    }
    if (url === '/api/v1/hub/imports/import-1' && init?.method === 'DELETE') {
      return { ok: true, json: async () => ({ import_id: 'import-1', status: 'REVOKED' }) }
    }
    if (url === '/api/v1/hub/resources/hub-1/versions/1/grants' && init?.method === 'POST') {
      return { ok: true, json: async () => ({ grant_id: 'grant-1' }) }
    }
    if (url === '/api/v1/hub/grants/grant-1' && init?.method === 'DELETE') {
      return { ok: true, json: async () => ({ grant_id: 'grant-1', status: 'REVOKED' }) }
    }
    return { ok: false, status: 404, json: async () => ({ code: 'RESOURCE_NOT_FOUND', message: 'not found' }) }
  }))
})

function renderResourceCenter() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <TenantProvider>
        <MemoryRouter initialEntries={['/enterprise/resources']}>
          <Routes><Route path="/enterprise/resources" element={<ResourceCenterPage />} /></Routes>
        </MemoryRouter>
      </TenantProvider>
    </QueryClientProvider>,
  )
}

test('resource catalog explains the scope of a Skill initial instruction', async () => {
  const user = userEvent.setup()
  renderResourceCenter()

  expect(await screen.findByRole('heading', { name: '资源中心' })).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '新建技能' }))

  const instructionGuidance = await screen.findByText('描述可复用的处理步骤、触发条件和例外；只有绑定此 Skill 的员工版本会应用。')
  expect(instructionGuidance.closest('.ant-form-item')).toBe(screen.getByLabelText('初始指令').closest('.ant-form-item'))
})

test('discoverable resource without Grant cannot be imported and does not request its manifest', async () => {
  const user = userEvent.setup()
  renderResourceCenter()
  await user.click(screen.getByRole('tab', { name: '发现资源' }))
  expect(await screen.findByText('工单 Skill')).toBeInTheDocument()
  expect(screen.getByText('导入需授权')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '详情' }))
  expect(await screen.findByText('资源可发现，导入需要发布方 Grant。')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '复制到租户' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '引用为只读资源' })).not.toBeInTheDocument()
  expect(fetchCalls.some(({ url }) => url.includes('/manifest?'))).toBe(false)
})

test('authorized Reference imports show dependencies and remain read-only in My Imports', async () => {
  resourceModes = ['REFERENCE']
  const user = userEvent.setup()
  renderResourceCenter()
  await user.click(screen.getByRole('tab', { name: '发现资源' }))
  expect(await screen.findByText('工单 Skill')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '详情' }))
  await waitFor(() => expect(fetchCalls.some(({ url }) => url.endsWith('/manifest?mode=REFERENCE'))).toBe(true))
  expect(await screen.findByText('SKILL.md')).toBeInTheDocument()
  expect(screen.queryByText('internal-asset')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '引用为只读资源' }))
  await waitFor(() => expect(fetchCalls.some(({ url, init }) => (
    url === '/api/v1/hub/imports' && init?.method === 'POST'
  ))).toBe(true))

  await user.click(screen.getByRole('tab', { name: '我导入的' }))
  expect(await screen.findByText('只读引用')).toBeInTheDocument()
  expect(screen.getByText('修改请复制到租户')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /编辑/ })).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: '撤销引用' }))
  await user.click(screen.getAllByRole('button', { name: '撤销引用' }).at(-1)!)
  await waitFor(() => expect(fetchCalls.some(({ url, init }) => (
    url === '/api/v1/hub/imports/import-1' && init?.method === 'DELETE'
  ))).toBe(true))
}, 15000)

test('publisher can open an exact-version Grant and create recipient permissions', async () => {
  publishedByMe = true
  const user = userEvent.setup()
  renderResourceCenter()

  await user.click(await screen.findByRole('tab', { name: '我发布的' }))
  await user.click(await screen.findByRole('button', { name: '详情' }))
  await user.click(await screen.findByRole('button', { name: '授权租户' }))
  await user.type(screen.getByPlaceholderText('例如：tenant-b'), 'tenant-b')
  await user.click(screen.getByRole('button', { name: '创建 Grant' }))

  await waitFor(() => {
    const request = fetchCalls.find(({ url, init }) => (
      url === '/api/v1/hub/resources/hub-1/versions/1/grants' && init?.method === 'POST'
    ))
    expect(request).toBeDefined()
    expect(JSON.parse(String(request?.init?.body))).toMatchObject({
      recipient_tenant_id: 'tenant-b',
      copy_allowed: true,
      reference_allowed: false,
    })
  })
}, 15000)
