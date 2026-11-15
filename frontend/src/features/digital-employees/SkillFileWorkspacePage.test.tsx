import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { beforeEach, expect, test, vi } from 'vitest'

import { SkillFileWorkspacePage } from './SkillFileWorkspacePage'
import { TenantProvider } from '../management/TenantContext'

let readOnly = false
let requestCalls: { url: string; init?: RequestInit }[] = []

beforeEach(() => {
  window.localStorage.clear()
  readOnly = false
  requestCalls = []
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input)
    requestCalls.push({ url, init })
    if (url === '/api/v1/skills/skill-1/versions/2/files') {
      return {
        ok: true,
        json: async () => ({
          skill_id: 'skill-1', version: 2, content_format: 'FILE_PACKAGE', entry_path: 'SKILL.md',
          file_manifest_sha256: 'a'.repeat(64), status: readOnly ? 'PUBLISHED' : 'DRAFT', read_only: readOnly,
          files: [
            { path: 'SKILL.md', mime_type: 'text/markdown', size_bytes: 11, sha256: 'b'.repeat(64) },
            { path: 'references/guide.md', mime_type: 'text/markdown', size_bytes: 9, sha256: 'c'.repeat(64) },
          ],
        }),
      }
    }
    if (url.startsWith('/api/v1/skills/skill-1/versions/2/files/content')) {
      const path = new URL(url, 'http://localhost').searchParams.get('file_path')
      return {
        ok: true,
        json: async () => ({
          path,
          mime_type: 'text/markdown; charset=utf-8',
          size_bytes: 11,
          sha256: path === 'SKILL.md' ? 'b'.repeat(64) : 'c'.repeat(64),
          content_text: path === 'SKILL.md' ? '入口指令 v1' : '参考说明',
          content_base64: null,
        }),
      }
    }
    if (url === '/api/v1/skills/skill-1/versions/2/files/zip/preview?strategy=replace'
      || url === '/api/v1/skills/skill-1/versions/2/files/zip/preview?strategy=merge') {
      return {
        ok: true,
        json: async () => ({
          staging_id: 'stage-1', skill_id: 'skill-1', version: 2,
          strategy: url.endsWith('strategy=merge') ? 'merge' : 'replace',
          archive_sha256: 'd'.repeat(64), base_manifest_sha256: 'a'.repeat(64),
          manifest_sha256: 'e'.repeat(64), entry_path: 'SKILL.md', file_count: 1,
          expires_in_seconds: 900,
          files: [{ path: 'SKILL.md', mime_type: 'text/markdown', size_bytes: 7, sha256: 'f'.repeat(64) }],
          diff: { added: [], updated: ['SKILL.md'], removed: [] },
        }),
      }
    }
    if (url.endsWith('/files/zip/commit/stage-1')) {
      return { ok: true, json: async () => ({ skill_id: 'skill-1', version: 2, file_count: 1 }) }
    }
    return { ok: true, json: async () => ({}) }
  }))
})

function renderWorkspace() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={queryClient}>
      <TenantProvider>
        <MemoryRouter initialEntries={['/enterprise/employees/employee-1/skills/skill-1/versions/2/files']}>
          <Routes>
            <Route path="/enterprise/employees/:employeeId/skills/:skillId/versions/:version/files" element={<SkillFileWorkspacePage />} />
          </Routes>
        </MemoryRouter>
      </TenantProvider>
    </QueryClientProvider>,
  )
}

test('file workspace reads and updates a file with its current SHA-256', async () => {
  const user = userEvent.setup()
  renderWorkspace()

  expect(await screen.findByText('Skill 文件工作区')).toBeInTheDocument()
  expect(await screen.findByDisplayValue('入口指令 v1')).toBeInTheDocument()
  await user.clear(screen.getByLabelText('文件正文'))
  await user.type(screen.getByLabelText('文件正文'), '入口指令 v2')
  await user.click(screen.getByRole('button', { name: '保存文件' }))

  await waitFor(() => {
    const update = requestCalls.find(({ url, init }) => url === '/api/v1/skills/skill-1/versions/2/files' && init?.method === 'PUT')
    expect(update).toBeDefined()
    const body = JSON.parse(String(update?.init?.body))
    expect(body.file_path).toBe('SKILL.md')
    expect(body.expected_sha256).toBe('b'.repeat(64))
    expect(body.content_text).toBe('入口指令 v2')
  })
})

test('ZIP upload previews merge diff then confirms commit', async () => {
  renderWorkspace()
  expect(await screen.findByText('Skill 文件工作区')).toBeInTheDocument()

  const file = new File(['zip bytes'], 'skill.zip', { type: 'application/zip' })
  fireEvent.change(screen.getByLabelText('上传 Skill ZIP'), { target: { files: [file] } })
  expect(await screen.findByText('ZIP 导入预览')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('radio', { name: '合并' }))
  await waitFor(() => expect(requestCalls.some(({ url }) => url.endsWith('zip/preview?strategy=merge'))).toBe(true))
  await userEvent.click(screen.getByRole('button', { name: '确认导入' }))
  await waitFor(() => expect(requestCalls.some(({ url }) => url.endsWith('zip/commit/stage-1'))).toBe(true))
})

test('file workspace creates, renames and deletes an auxiliary file', async () => {
  const user = userEvent.setup()
  renderWorkspace()
  expect(await screen.findByText('Skill 文件工作区')).toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: '新建文件' }))
  const createDialog = await screen.findByRole('dialog', { name: '新建 Skill 文件' })
  await user.type(within(createDialog).getByLabelText('文件路径'), 'references/new.md')
  await user.type(within(createDialog).getByLabelText('文件正文'), '辅助说明')
  await user.click(within(createDialog).getByRole('button', { name: '创建文件' }))
  await waitFor(() => expect(requestCalls.map(({ url, init }) => `${init?.method ?? 'GET'} ${url}`)).toContain(
    'POST /api/v1/skills/skill-1/versions/2/files',
  ))

  fireEvent.click(screen.getByText('references'))
  await user.click(await screen.findByText('guide.md'))
  await screen.findByDisplayValue('参考说明')
  await user.click(screen.getByRole('button', { name: '重命名' }))
  const newPath = screen.getByLabelText('新路径')
  await user.clear(newPath)
  await user.type(newPath, 'references/guide-v2.md')
  await user.click(screen.getByRole('button', { name: '保存路径' }))
  await waitFor(() => expect(requestCalls.some(({ url, init }) => (
    url.endsWith('/files/rename') && init?.method === 'POST'
  ))).toBe(true))

  await user.click(screen.getByRole('button', { name: /删\s*除/ }))
  await user.click(await screen.findByRole('button', { name: /确认删\s*除/ }))
  await waitFor(() => expect(requestCalls.some(({ url, init }) => (
    url.includes('file_path=references%2Fguide-v2.md') && init?.method === 'DELETE'
  ))).toBe(true))
}, 15000)

test('Hub reference file workspace exposes read-only source without edit actions', async () => {
  readOnly = true
  renderWorkspace()
  expect(await screen.findByText('该 Skill 版本或 Hub REFERENCE 只读')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '新建文件' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '上传 ZIP' })).not.toBeInTheDocument()
  expect(await screen.findByLabelText('文件正文')).toHaveAttribute('readonly')
}, 15000)
